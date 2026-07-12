#!/usr/bin/env python3
"""
Surf conditions + 5-day forecast for Narragansett -> Point Judith -> Matunuck.

Fetch -> normalise onto a common hourly grid -> score per spot per timestep -> emit JSON.
Spec: docs/forecast-agent.md.  Registry: spots.json.

Python 3 stdlib only, no dependencies, no build step -- matching this repo's character.

Usage:
    python3 forecast.py                          # live 5-day -> data/forecast.json
    python3 forecast.py --from 2023-09-14 --to 2023-09-18 --out data/x.json
    python3 forecast.py --no-cache               # bypass .cache/

--from/--to replays a PAST window through the identical normalise+score path,
using reanalysis instead of forecast sources. It exists because you cannot judge
a scoring model, or a UI, on a flat week. See `historical` in the emitted JSON --
the artifact says which mode produced it, and which wave model.
"""

import argparse
import hashlib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = "gansett-juice/forecast@1"
TZ = ZoneInfo("America/New_York")
STEPS = 120                      # 5 days, hourly
UA = "gansett-juice/0.1 (+https://github.com/jstockdi/gansett-juice)"

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]           # .claude/skills/surf-forecast -> repo root
CACHE = REPO / ".cache"

# api.weather.gov 403s on an empty User-Agent. Verified 2026-07-12.
NWS_GRIDS = {
    "narragansett": "BOX/66,56",
    "point-judith": "BOX/65,53",
    "matunuck":     "BOX/63,54",
}
REGION_LATLON = {                # same three points, for the ERA5 historical path
    "narragansett": {"lat": 41.4262, "lon": -71.4495},
    "point-judith": {"lat": 41.3633, "lon": -71.4900},
    "matunuck":     {"lat": 41.3785, "lon": -71.5300},
}
TIDE_STATION = "8455083"         # Point Judith, Harbor of Refuge. Predictions only.
BUOYS = ["44097", "44085"]       # Block Island (primary), Buzzards Bay 260 (fallback)

# Cache TTLs, in seconds -- matched to how often each upstream actually changes.
# Polling faster than the source updates is just rudeness.
TTL = {"marine": 3 * 3600, "nws": 3600, "tide": 24 * 3600, "ndbc": 20 * 60}

# --- scoring calibration -------------------------------------------------
# NONE of these are sourced. They are physically motivated but numerically
# invented, and they are the reason scores should be read as ordinal
# ("Deep Hole beats Green Hill today") and not cardinal ("62 out of 100").
# The calibration loop is: score a day, read the Warm Winds human report,
# adjust. See docs/forecast-agent.md §8.5.
KAPPA0, KAPPA1 = 6.0, 1.5        # diffraction: how far swell bends around a headland
GLASS = 4.0                      # kt; below this, glassy regardless of direction
ONSHORE_KILL = 12.0              # kt of straight-onshore that zeroes a spot
CROSS_KILL = 25.0                # kt of cross-shore that zeroes a spot
TIDE_SIGMA = 0.25


# ==================================================================== fetch

def get(url, ttl, kind="txt"):
    """HTTP GET with an on-disk cache. Returns (body, from_cache).

    Key on a hash of the full URL, never a truncated slug: the live and archive
    marine URLs share a ~120-char prefix, so slug-truncation collides them and a
    historical run silently reads live data (timestamps miss, everything reads
    no_data). Learned the hard way."""
    CACHE.mkdir(exist_ok=True)
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    path = CACHE / f"{key}.{kind}"
    if ttl and path.exists() and time.time() - path.stat().st_mtime < ttl:
        return path.read_text(), True
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        body = r.read().decode("utf-8", "replace")
    path.write_text(body)
    return body, False


class Sources:
    """Tracks provenance + failures so the artifact can be honest about them."""

    def __init__(self, use_cache=True):
        self.use_cache = use_cache
        self.log = []

    def json(self, sid, url, kind):
        try:
            body, cached = get(url, TTL[kind] if self.use_cache else 0)
            self.log.append({"id": sid, "url": url, "status": "cached" if cached else "ok",
                             "fetchedAt": now_local().isoformat()})
            return json.loads(body)
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.log.append({"id": sid, "url": url, "status": "failed", "note": str(e)})
            return None

    def text(self, sid, url, kind):
        try:
            body, cached = get(url, TTL[kind] if self.use_cache else 0)
            self.log.append({"id": sid, "url": url, "status": "cached" if cached else "ok",
                             "fetchedAt": now_local().isoformat()})
            return body
        except (urllib.error.URLError, OSError) as e:
            self.log.append({"id": sid, "url": url, "status": "failed", "note": str(e)})
            return None


def now_local():
    return datetime.now(TZ)


# ------------------------------------------------------------------ marine
MARINE_VARS = ("swell_wave_height,swell_wave_direction,swell_wave_period,"
               "wind_wave_height,wind_wave_direction,wind_wave_period")


def fetch_marine(src, window=None):
    """GFS-Wave 0.16deg (WaveWatch III derived). Explicitly NOT best_match, which
    resolves to MeteoFrance MFWAM here -- see docs §3.1.

    ncep_gfswave016 has NO archive coverage (verified: all nulls for past dates),
    so a historical replay falls back to best_match/MFWAM. That is a different
    model, it reads bigger, and the artifact records which one was used."""
    base = ("https://marine-api.open-meteo.com/v1/marine"
            "?latitude=41.36&longitude=-71.49"
            f"&hourly={MARINE_VARS}"
            "&timezone=America%2FNew_York&length_unit=imperial")
    if window:
        sid = "mfwam-archive"
        url = base + f"&start_date={window[0]}&end_date={window[1]}"
    else:
        sid = "gfswave016"
        url = base + "&models=ncep_gfswave016&forecast_days=6"
    d = src.json(sid, url, "marine")
    if not d:
        return {}
    h = d["hourly"]
    out = {}
    for i, t in enumerate(h["time"]):                    # naive local ISO, e.g. 2026-07-12T14:00
        out[t] = {
            "swell": component(h["swell_wave_height"][i], h["swell_wave_period"][i],
                               h["swell_wave_direction"][i]),
            "windWave": component(h["wind_wave_height"][i], h["wind_wave_period"][i],
                                  h["wind_wave_direction"][i]),
        }
    return out


def component(h, t, d):
    if h is None or t is None or d is None:
        return None
    return {"h": round(h, 2), "t": round(t, 1), "d": int(d)}


# -------------------------------------------------------------------- wind
ISO_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def dur_hours(s):
    m = ISO_DUR.match(s)
    if not m:
        raise ValueError(f"unparsed ISO-8601 duration: {s}")
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return d * 24 + h + (1 if mi else 0)


def expand(values):
    """NWS encodes each value as an ISO-8601 interval with a *variable* duration
    ('.../PT1H', but also PT2H, P1DT9H). windSpeed / windGust / windDirection come
    back with DIFFERENT array lengths because of it. Zipping them positionally
    would silently misalign speed against direction -- the worst bug available in
    this codebase. So: expand each value across its own duration onto the hour."""
    out = {}
    for v in values or []:
        start_s, dur_s = v["validTime"].split("/")
        start = datetime.fromisoformat(start_s).astimezone(TZ)
        for k in range(dur_hours(dur_s)):
            out[iso_naive(start + timedelta(hours=k))] = v["value"]
    return out


def fetch_wind_era5(src, window):
    """Historical replay: ERA5 reanalysis, per region. ERA5 is ~0.25deg, coarser
    than the NWS grid, so the three regions may snap to the same cell -- the
    per-region wind contrast is weaker here than in a live run. Stated, not hidden."""
    regions = {}
    for region, meta in REGION_LATLON.items():
        d = src.json(f"era5-{region}",
                     "https://archive-api.open-meteo.com/v1/archive"
                     f"?latitude={meta['lat']}&longitude={meta['lon']}"
                     "&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m"
                     f"&start_date={window[0]}&end_date={window[1]}"
                     "&wind_speed_unit=kn&timezone=America%2FNew_York", "marine")
        out = {}
        if d:
            h = d["hourly"]
            for i, t in enumerate(h["time"]):
                spd, dr = h["wind_speed_10m"][i], h["wind_direction_10m"][i]
                if spd is None or dr is None:
                    continue
                out[t] = {"spd": round(spd, 1), "dir": int(dr), "gust": h["wind_gusts_10m"][i]}
        regions[region] = out
    return regions


def fetch_wind(src):
    regions = {}
    for region, grid in NWS_GRIDS.items():
        d = src.json(f"nws-{grid.replace('/', '-').replace(',', '-')}",
                     f"https://api.weather.gov/gridpoints/{grid}", "nws")
        if not d:
            regions[region] = {}
            continue
        p = d["properties"]
        spd = expand(p["windSpeed"]["values"])           # km/h
        dr = expand(p["windDirection"]["values"])        # degrees, meteorological
        gst = expand(p["windGust"]["values"])            # km/h
        out = {}
        for t in spd.keys() & dr.keys():
            if spd[t] is None or dr[t] is None:
                continue
            g = gst.get(t)
            out[t] = {"spd": round(spd[t] / 1.852, 1),   # km/h -> kt
                      "dir": int(dr[t]),
                      "gust": round(g / 1.852, 1) if g is not None else None}
        regions[region] = out
    return regions


# -------------------------------------------------------------------- tide
def fetch_tide(src, start, end):
    base = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
            "?product=predictions&application=gansett-juice"
            f"&begin_date={start:%Y%m%d}&end_date={end:%Y%m%d}"
            f"&datum=MLLW&station={TIDE_STATION}"
            # lst_ldt = local standard/daylight. This is what handles DST; we do
            # no offset arithmetic of our own on tide data.
            "&time_zone=lst_ldt&units=english&format=json")

    hourly = src.json("coops-hourly", base + "&interval=h", "tide")
    hilo = src.json("coops-hilo", base + "&interval=hilo", "tide")

    heights = {}
    if hourly and "predictions" in hourly:
        for p in hourly["predictions"]:
            heights[p["t"].replace(" ", "T")] = float(p["v"])

    # Day range, used to normalise tide within *that day's* swing, so the score is
    # robust to spring/neap variation.
    ranges = {}
    if hilo and "predictions" in hilo:
        for p in hilo["predictions"]:
            day = p["t"][:10]
            v = float(p["v"])
            r = ranges.setdefault(day, {"low": v, "high": v})
            r["low"] = min(r["low"], v)
            r["high"] = max(r["high"], v)
    return heights, ranges


# -------------------------------------------------------------------- buoy
COMPASS = {"N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5,
           "SE": 135, "SSE": 157.5, "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
           "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5}
M_TO_FT = 3.28084


def fetch_buoy(src):
    """NDBC .spec = OBSERVATIONS ONLY. Used for the 'now' row and as a sanity check
    on the model's first hours. Never extrapolated into future timesteps.

    Gotcha: SwD/WWD are compass TEXT ('SE', 'SSE'), not degrees. MWD is degrees."""
    for bid in BUOYS:
        body = src.text(f"ndbc-{bid}", f"https://www.ndbc.noaa.gov/data/realtime2/{bid}.spec", "ndbc")
        if not body:
            continue
        for line in body.splitlines():
            if line.startswith("#"):
                continue
            f = line.split()
            if len(f) < 15:
                continue
            try:
                at = datetime(int(f[0]), int(f[1]), int(f[2]), int(f[3]), int(f[4]),
                              tzinfo=timezone.utc).astimezone(TZ)
            except ValueError:
                continue

            def num(x):
                return None if x == "MM" else float(x)

            swh, swp, wwh, wwp = num(f[6]), num(f[7]), num(f[8]), num(f[9])
            swd, wwd = COMPASS.get(f[10]), COMPASS.get(f[11])
            if swh is None and wwh is None:
                continue                                  # all-MM row, try the next
            obs = {"at": at.isoformat(), "buoy": bid}
            if None not in (swh, swp, swd):
                obs["swell"] = {"h": round(swh * M_TO_FT, 1), "t": swp, "d": int(swd)}
            if None not in (wwh, wwp, wwd):
                obs["windWave"] = {"h": round(wwh * M_TO_FT, 1), "t": wwp, "d": int(wwd)}
            return obs
    return None


# =================================================================== scoring

def ang_diff(a, b):
    """Smallest angle between two bearings, 0..180."""
    return abs((a - b + 180) % 360 - 180)


def window_gap(bearing, window):
    """Degrees from `bearing` to the nearest edge of the open window. 0 if inside.
    An empty window (Camp Cronin) means everything is a gap -- measured from the
    spot's facing, since with no line of sight the only energy arriving is
    diffracted around the obstruction."""
    if not window:
        return None
    for lo, hi in window:
        if lo <= bearing <= hi:
            return 0.0
    return min(min(ang_diff(bearing, lo), ang_diff(bearing, hi)) for lo, hi in window)


def exposure(bearing, period, spot):
    """How much of a swell component actually ARRIVES at this spot.

    This is the heart of the model. Swell direction is not a score multiplier --
    it changes how much energy physically reaches the break, which then feeds the
    size term. That is why spots disagree with each other.

    kappa grows with period, so long-period swell bends further around the
    headland than short-period windswell does. One formula, three payoffs:
    it creates the spot contrast, it encodes "long-period wraps", and it lets
    Camp Cronin (no direct line of sight at all) exist as a real, mushy spot."""
    gap = window_gap(bearing, spot["openWindow"])
    if gap is None:                                       # no window => pure diffraction
        gap = ang_diff(bearing, spot["facing"])
    if gap == 0:
        return 1.0
    kappa = max(4.0, min(30.0, KAPPA0 + KAPPA1 * (period - 8)))
    return math.exp(-gap / kappa)


def effective_swell(cond, spot):
    """Energy-preserving combine of every swell component after exposure."""
    parts = []
    for key in ("swell", "windWave"):
        c = cond.get(key)
        if not c:
            continue
        e = exposure(c["d"], c["t"], spot)
        parts.append((c["h"] * e, c["t"], c["d"]))
    if not parts:
        return None
    h_eff = math.sqrt(sum(h * h for h, _, _ in parts))
    dom = max(parts, key=lambda p: p[0])                  # dominant = biggest arriving
    return {"h": round(h_eff, 2), "t": dom[1], "d": dom[2]}


def q_size(h, s):
    if h < s["works"] or h > s["maxHandles"]:
        return 0.0                                        # flat, or blown out / closed out
    if h < s["idealLo"]:
        return (h - s["works"]) / (s["idealLo"] - s["works"])
    if h <= s["idealHi"]:
        return 1.0
    return (s["maxHandles"] - h) / (s["maxHandles"] - s["idealHi"])


def q_period(t, p):
    return max(0.25, min(1.0, (t - p["tMin"]) / (p["tGood"] - p["tMin"])))


def q_wind(wind, spot):
    """Meteorological convention: wind['dir'] is the direction it blows FROM,
    matching every source we consume. offshoreDir is the FROM-bearing that is
    offshore at this spot.

    This is the term that makes the coast disagree with itself. A 15kt NE wind:
    at K38 (offshoreDir 45) theta=0 -> 1.00. At Monahan's (offshoreDir 290)
    theta=115 -> ~0.20. Same hour, same wind, one firing and one junk."""
    spd = wind["spd"]
    if spd <= GLASS:
        return 1.0                                        # glassy is glassy
    theta = math.radians(ang_diff(wind["dir"], spot["offshoreDir"]))
    onshore = max(0.0, -math.cos(theta)) * spd
    cross = abs(math.sin(theta)) * spd
    return max(0.0, min(1.0, 1 - onshore / ONSHORE_KILL - 0.5 * cross / CROSS_KILL))


TIDE_TARGET = {"low": 0.15, "low-mid": 0.35, "mid": 0.5, "high": 0.85}


def q_tide(height, rng, rising, spot):
    t = spot["tide"]
    if t["pref"] == "any" or not rng or rng["high"] <= rng["low"]:
        return 1.0
    tau = (height - rng["low"]) / (rng["high"] - rng["low"])
    tau = max(0.0, min(1.0, tau))
    target = TIDE_TARGET[t["pref"]]
    g = math.exp(-((tau - target) ** 2) / (2 * TIDE_SIGMA ** 2))
    q = 1 - t["strength"] * (1 - g)                       # strength = how much it cares
    if rising:
        q += t.get("risingBonus", 0)
    return max(0.0, min(1.0, q))


def in_disputed(bearing, spot):
    return any(lo <= bearing <= hi for lo, hi in spot.get("disputedWindow", []))


def score_step(cond, wind, tide, spot):
    """Score one spot at one timestep.

    Missing data must never look like bad conditions. `s` is None for no_data,
    never 0 -- a None cannot be ranked or colour-mapped as if it were a low score."""
    missing = []
    if not cond or (not cond.get("swell") and not cond.get("windWave")):
        missing.append("swell")
    if not wind:
        missing.append("wind")
    if missing:
        return {"s": None, "status": "no_data", "missing": missing}

    eff = effective_swell(cond, spot)
    if eff is None:
        return {"s": None, "status": "no_data", "missing": ["swell"]}

    qs = q_size(eff["h"], spot["size"])
    qw = q_wind(wind, spot)
    qp = q_period(eff["t"], spot["period"])

    if tide is None:
        qt, status, miss = 1.0, "partial", ["tide"]       # neutral, and say so
    else:
        qt, status, miss = q_tide(tide["ft"], tide["range"], tide["stage"] == "rising", spot), "ok", []

    # Size and wind are hard gates -- either genuinely zeroes a session.
    # Period and tide modulate between roughly half and full, but never erase.
    # Direction is already inside qs, via effective_swell. No double-counting.
    s = 100 * qs * qw * (0.5 + 0.5 * qp) * (0.6 + 0.4 * qt)

    q = {"size": round(qs, 2), "wind": round(qw, 2), "period": round(qp, 2), "tide": round(qt, 2)}
    worst = min(q, key=q.get)
    out = {
        "s": round(s),
        "status": status,
        "limiting": "none" if q[worst] >= 0.85 else worst,
        "hEff": eff["h"], "tEff": eff["t"], "dEff": eff["d"],
        "q": q,
    }
    if miss:
        out["missing"] = miss
    if in_disputed(eff["d"], spot):
        # The dominant swell is in the taper band -- outside the direct-line-of-sight
        # window, arriving only by diffraction around the headland. The exposure
        # formula gates that on period, which is our INFERENCE: no source states a
        # period threshold for these reefs. Flag it so the UI can hedge. docs §8.1.
        out["caution"] = "diffraction-taper-inferred"
    return out


# ==================================================================== assemble

def iso_naive(dt):
    return dt.strftime("%Y-%m-%dT%H:00")


def build(use_cache=True, window=None):
    """window = (from, to) as YYYY-MM-DD replays a past event. Same grid, same
    normalise, same score -- only the fetchers change. That is the whole point:
    the historical artifact exercises the identical code path."""
    src = Sources(use_cache)

    if window:
        start = datetime.fromisoformat(window[0]).replace(tzinfo=TZ)
        steps = min(STEPS, int((datetime.fromisoformat(window[1]).replace(tzinfo=TZ)
                                - start).total_seconds() // 3600))
    else:
        start = now_local().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        steps = STEPS
    grid = [start + timedelta(hours=i) for i in range(steps)]

    marine = fetch_marine(src, window)
    winds = fetch_wind_era5(src, window) if window else fetch_wind(src)
    heights, ranges = fetch_tide(src, start.date(), (start + timedelta(hours=steps)).date())
    observed = None if window else fetch_buoy(src)

    registry = json.loads((HERE / "spots.json").read_text())
    spots = registry["spots"]

    times, conditions = [], {"swell": [], "windWave": [],
                             "wind": {r: [] for r in NWS_GRIDS}, "tide": []}
    tides = []

    for i, dt in enumerate(grid):
        k = iso_naive(dt)
        times.append(dt.isoformat())

        m = marine.get(k, {})
        conditions["swell"].append(m.get("swell"))
        conditions["windWave"].append(m.get("windWave"))

        for r in NWS_GRIDS:
            conditions["wind"][r].append(winds.get(r, {}).get(k))

        ft = heights.get(k)
        if ft is None:
            tides.append(None)
            conditions["tide"].append(None)
        else:
            nxt = heights.get(iso_naive(dt + timedelta(hours=1)))
            stage = "rising" if (nxt is not None and nxt > ft) else "falling"
            tides.append({"ft": ft, "stage": stage, "range": ranges.get(k[:10])})
            conditions["tide"].append({"ft": round(ft, 2), "stage": stage})

    scores = {}
    for spot in spots:
        row = []
        for i, dt in enumerate(grid):
            cond = {"swell": conditions["swell"][i], "windWave": conditions["windWave"][i]}
            wind = conditions["wind"][spot["windRegion"]][i]
            row.append(score_step(cond, wind, tides[i], spot))
        scores[spot["id"]] = row

    report = {
        "schema": SCHEMA,
        "generatedAt": now_local().isoformat(),
        "timezone": "America/New_York",
        "grid": {"interval": "PT1H", "steps": steps, "start": start.isoformat()},
        "sources": src.log,
        "times": times,
        "observed": observed,
        "conditions": conditions,
        "spots": [{k: s[k] for k in
                   ("id", "name", "lat", "lon", "facing", "bottom", "confidence", "notes")}
                  for s in spots],
        "scores": scores,
        "summary": summarise(spots, scores, times),
    }
    if window:
        # Loudly, in the artifact itself: this is not a forecast.
        report["historical"] = {
            "window": list(window),
            "waveModel": "meteofrance_wave (MFWAM) via best_match",
            "windModel": "ERA5 reanalysis",
            "note": "Replay of a past event, NOT a forecast. ncep_gfswave016 has no "
                    "archive coverage, so this uses a different (coarser, higher-reading) "
                    "wave model than a live run, and ERA5 wind is coarser than the NWS "
                    "grid. Use for exercising the model and the UI; do not compare its "
                    "absolute scores against a live forecast.",
        }
    return report


def summarise(spots, scores, times):
    """Precomputed rankings. The UI must never recompute a score."""
    def best_in(spot_id, lo, hi):
        best = None
        for i in range(lo, min(hi, len(times))):
            c = scores[spot_id][i]
            if c["s"] is None:
                continue
            if best is None or c["s"] > best[1]:
                best = (times[i], c["s"])
        return best

    today = sum(1 for t in times if t[:10] == times[0][:10])

    def rank(lo, hi):
        out = []
        for s in spots:
            b = best_in(s["id"], lo, hi)
            if b:
                out.append({"spot": s["id"], "s": b[1], "at": b[0]})
        return sorted(out, key=lambda x: -x["s"])

    now = [{"spot": s["id"], "s": scores[s["id"]][0]["s"]}
           for s in spots if scores[s["id"]][0]["s"] is not None]
    return {
        "bestNow": sorted(now, key=lambda x: -x["s"]),
        "bestToday": rank(0, today),
        "bestWeek": rank(0, len(times)),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "data" / "forecast.json"))
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--from", dest="start", metavar="YYYY-MM-DD",
                    help="replay a past window instead of forecasting")
    ap.add_argument("--to", dest="end", metavar="YYYY-MM-DD")
    a = ap.parse_args()

    if bool(a.start) != bool(a.end):
        ap.error("--from and --to must be given together")

    report = build(use_cache=not a.no_cache,
                   window=(a.start, a.end) if a.start else None)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))

    failed = [s for s in report["sources"] if s["status"] == "failed"]
    scored = sum(1 for row in report["scores"].values() for c in row if c["s"] is not None)
    total = sum(len(row) for row in report["scores"].values())
    print(f"wrote {out}  ({out.stat().st_size // 1024} KB)")
    print(f"  {len(report['spots'])} spots x {report['grid']['steps']} steps "
          f"-> {scored}/{total} scored")
    for f in failed:
        print(f"  ! source failed: {f['id']} -- {f.get('note', '')[:80]}", file=sys.stderr)
    if report["summary"]["bestNow"]:
        b = report["summary"]["bestNow"][0]
        print(f"  best now: {b['spot']} ({b['s']})")
    return 1 if len(failed) == len(report["sources"]) else 0


if __name__ == "__main__":
    sys.exit(main())
