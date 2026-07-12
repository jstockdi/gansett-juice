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
TTL = {"marine": 3 * 3600, "nws": 3600, "tide": 24 * 3600, "ndbc": 20 * 60,
       "warmwinds": 6 * 3600}   # hand-written, ~daily -- 6h is already generous

# --- scoring calibration -------------------------------------------------
# NONE of these are sourced. They are physically motivated but numerically
# invented, and they are the reason scores should be read as ordinal
# ("Deep Hole beats Green Hill today") and not cardinal ("62 out of 100").
# The calibration loop is: score a day, read the Warm Winds human report,
# adjust. See docs/forecast-agent.md §8.5.
# The one calibration constant that is FITTED rather than invented: a single scale on
# effective wave height, tuned against the Warm Winds human report by calibrate.py.
# 1.0 = trust the wave model as-is. Changed only by a reviewed PR from the compounding
# CI step -- never silently.
SIZE_BIAS = 1.0

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


FT_TO_M = 0.3048


def power(h_ft, t_s):
    """Deep-water wave power, kW per metre of crest -- what surfers call "swell energy".

        P = (rho g^2 / 64pi) H^2 T  ~=  0.5 * H(m)^2 * T(s)

    This is the honest scalar, and it is why height alone is a bad headline. Energy
    goes as H^2*T, so 3ft@14s carries 2.3x the punch of 3ft@6s at the SAME height --
    a difference height literally cannot express. Over our own data, Hurricane Lee was
    4.3x today's height but 34x its energy. Height is what you photograph; energy is
    what you feel."""
    h = h_ft * FT_TO_M
    return 0.5 * h * h * t_s


def component(h, t, d):
    if h is None or t is None or d is None:
        return None
    return {"h": round(h, 2), "t": round(t, 1), "d": int(d), "e": round(power(h, t), 2)}


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
                h = round(swh * M_TO_FT, 1)
                obs["swell"] = {"h": h, "t": swp, "d": int(swd), "e": round(power(h, swp), 2)}
            if None not in (wwh, wwp, wwd):
                obs["windWave"] = {"h": round(wwh * M_TO_FT, 1), "t": wwp, "d": int(wwd)}
            return obs
    return None


# ==================================================================== tropics

def fetch_tropics(src):
    """NHC Atlantic tropical outlook. Found via the links at the bottom of the Warm
    Winds report -- and it is the most valuable thing on that page.

    Hurricane groundswell is what makes surf here good; Hurricane Lee is the whole
    reason our example dataset has waves in it. But GFS-Wave only sees 5 days, so a
    storm spinning up 7-10 days out is INVISIBLE to this app. NHC sees it first.

    That cuts both ways, and the honest half matters more: when NHC says no formation
    is expected in 7 days, a flat forecast is not just flat inside our window -- there
    is nothing coming behind it either. That turns "nothing in the next 5 days" from a
    statement about our horizon into a statement about the ocean."""
    out = {}
    storms = src.json("nhc-storms", "https://www.nhc.noaa.gov/CurrentStorms.json", "nws")
    if storms is not None:
        out["activeStorms"] = [
            {"name": s.get("name"), "class": s.get("classification"),
             "lat": s.get("lat"), "lon": s.get("lon"),
             "windMph": s.get("intensity"), "movement": s.get("movementDir")}
            for s in storms.get("activeStorms", [])
            if str(s.get("binNumber", "")).startswith("AT") or s.get("id", "").startswith("al")
        ]

    xml = src.text("nhc-outlook", "https://www.nhc.noaa.gov/xml/TWOAT.xml", "nws")
    if xml:
        m = re.search(r"(Tropical cyclone formation is[^.]*\.)", xml)
        if m:
            out["outlook"] = m.group(1).strip()
        out["formationExpected"] = bool(m and "not expected" not in m.group(1))
    return out or None


# =================================================================== spectrum

SPEC_PAIR = re.compile(r"([\d.]+)\s*\(([\d.]+)\)")


def fetch_spectrum(src):
    """The raw wave energy spectrum from the buoy: energy density (m^2/Hz) across
    ~64 frequency bins. OBSERVED, right now -- there is no forecast equivalent.

    Why bother, when we already have height/period/direction? Because those three
    numbers cannot tell one clean 3ft groundswell from two crossing 2ft swells --
    they read identically. The spectrum can. A tall narrow peak is one organised
    swell; two peaks are crossed swells and a confused lineup; a low broad hump at
    4-6s is slop. Nothing else in this app sees that.

    It is also an independent check on the model: the buoy is MEASURING, not
    predicting. GFS-Wave gives us partitions, not a spectrum, so a forecast
    spectrum is not something we can honestly draw -- and we don't."""
    for bid in BUOYS:
        body = src.text(f"ndbc-spec-{bid}", f"https://www.ndbc.noaa.gov/data/realtime2/{bid}.data_spec",
                        "ndbc")
        if not body:
            continue
        rows = [l for l in body.splitlines() if not l.startswith("#")]
        if not rows:
            continue
        f = rows[0].split()
        try:
            at = datetime(int(f[0]), int(f[1]), int(f[2]), int(f[3]), int(f[4]),
                          tzinfo=timezone.utc).astimezone(TZ)
        except ValueError:
            continue

        bins = [(float(fr), float(e)) for e, fr in SPEC_PAIR.findall(rows[0]) if float(fr) > 0]
        bins = [(fr, e) for fr, e in bins if 0.03 <= fr <= 0.5]        # 2s..33s, the surf band
        total = sum(e for _, e in bins)
        if not bins or total <= 0:
            continue

        pf, pe = max(bins, key=lambda x: x[1])
        # what fraction of the energy sits near the peak -- our "is it organised?" number
        near = sum(e for fr, e in bins if abs(fr - pf) <= 0.2 * pf)
        return {
            "buoy": bid,
            "at": at.isoformat(),
            "peakPeriod": round(1 / pf, 1),
            "organization": round(near / total * 100),
            # Hs = 4*sqrt(m0); m0 is the spectral area. A cross-check on the model.
            "hsFt": round(4 * math.sqrt(sum(e * 0.005 for _, e in bins)) / FT_TO_M, 1),
            "bins": [[round(1 / fr, 1), round(e, 4)] for fr, e in bins],   # [period_s, m^2/Hz]
        }
    return None


# ============================================================== ground truth

WW_URL = "https://www.warmwinds.com/surf-report"
TAG = re.compile(r"(?s)<[^>]+>")
SCRIPT = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
DAYBAND = re.compile(r"^([\d.]+\s*-\s*[\d.]+|[\d.]+\+?)\s*(?:feet|ft)\s*/\s*(.+)$", re.I)


def fetch_warmwinds(src):
    """The local shop's HUMAN surf report -- our only ground truth.

    We extract the numeric observation and their per-day size band, and we log
    those beside the model's call so the calibration constants can eventually be
    fitted instead of invented (docs §8.5).

    Deliberately NOT extracted: their written prose. It is their copyrighted work.
    We keep numbers and link out. Do not 'improve' this by grabbing the summary
    paragraph."""
    body = src.text("warmwinds", WW_URL, "warmwinds")
    if not body:
        return None
    import html as _html
    text = _html.unescape(TAG.sub("\n", SCRIPT.sub(" ", body)))
    L = [x.strip() for x in text.split("\n") if x.strip()]
    try:
        i = L.index("TODAY'S RI SURF REPORT")
    except ValueError:
        return None                                   # page restructured -- fail loudly, not silently
    blk = L[i:i + 60]

    def after(label, n=1):
        for k, x in enumerate(blk):
            if x.strip().rstrip(':').upper() == label.upper():
                return blk[k + n] if k + n < len(blk) else None
        return None

    def num(s):
        if not s:
            return None
        m = re.search(r"[\d.]+", s)
        return float(m.group()) if m else None

    def bearing(s):
        m = re.search(r"([\d.]+)\s*°", s or "")
        return float(m.group(1)) if m else None

    out = {
        "source": WW_URL,
        "reporter": after("Reporter"),
        "updated": after("Updated"),
        "waveHeightFt": num(after("WAVE HEIGHT")),
        "periodS": num(after("DOMINANT PERIOD")),
        "swellDir": bearing(after("SWELL DIRECTION")),
        "windMph": num(after("WIND SPEED")),
        "windDir": bearing(after("WIND DIRECTION")),
        "waterTempF": num(after("Water Temperature")),   # next line is ": 65 Degrees"
        "outlook": [],
    }
    # 3-day outlook: a heading line ("SUNDAY, JULY 12") followed by "0-1 feet / SW winds"
    for k, x in enumerate(L[i:i + 90]):
        if re.match(r"^[A-Z]+DAY,\s+[A-Z]+\s+\d+$", x) and k + 1 < len(L) - i:
            m = DAYBAND.match(L[i + k + 1])
            if m:
                out["outlook"].append({"day": x.title(), "sizeFt": m.group(1).replace(" ", ""),
                                       "wind": m.group(2).strip()})
    return out if out["waveHeightFt"] is not None else None


BAND = re.compile(r"([\d.]+)\s*-\s*([\d.]+)")


def compare_truth(gt, report):
    """Do we and the human agree -- and if not, about WHAT?

    This matters more than it looks. On a small day we say "not worth it" and Warm
    Winds says "knee high, rideable with logs". Those are not the same claim: we
    agree exactly on the SIZE and disagree on the VERDICT, because our worth-it bar
    quietly assumes you want a wave with some push behind it. A forecast that hides
    that assumption is being dishonest about where its judgement ends and its taste
    begins. So: compare the numbers, and name which kind of disagreement it is."""
    o = gt.get("outlook") or []
    m = BAND.match(o[0]["sizeFt"]) if o else None
    if not m:
        return None
    lo, hi = float(m.group(1)), float(m.group(2))

    heffs = [c["hEff"] for row in report["scores"].values() for i, c in enumerate(row[:24])
             if c["s"] is not None and report["daylight"][i]]
    model = round(max(heffs), 2) if heffs else 0.0
    best = report["summary"]["bestToday"]
    bar = report["summary"]["worthItThreshold"]
    top = best[0]["s"] if best else 0

    if model < lo - 0.5:
        size = "model-smaller"
    elif model > hi + 0.5:
        size = "model-bigger"
    else:
        size = "agree"

    # they publish a size band, not a score -- if they're calling it rideable at all
    # and we're calling it not worth it, that's a THRESHOLD disagreement, not a data one
    verdict = "agree" if (top >= bar) == (hi >= 2) else "threshold"

    return {"humanBandFt": [lo, hi], "modelMaxFt": model, "size": size, "verdict": verdict,
            "modelTop": top, "bar": bar}


def log_calibration(report, path):
    """Append one row: what the human said vs what the model said, same day.

    This is the whole point of the ground-truth fetch. Nobody can calibrate
    KAPPA0/ONSHORE_KILL/etc. from first principles -- they get fitted against
    this log, or they stay invented forever."""
    gt = report.get("groundTruth")
    if not gt:
        return None
    swell = [c for c in report["conditions"]["swell"][:24] if c]
    # biggest surf the model thinks actually ARRIVES anywhere today -- the number
    # directly comparable to Warm Winds' "0-1 feet" call
    heffs = [c["hEff"] for row_ in report["scores"].values() for c in row_[:24]
             if c["s"] is not None]
    row = {
        "date": report["generatedAt"][:10],
        "generatedAt": report["generatedAt"],
        "human": gt,
        "model": {
            "bestToday": report["summary"]["bestToday"][:3],
            "maxHEffToday": round(max(heffs), 2) if heffs else 0,
            "swellToday": {
                "maxH": round(max([c["h"] for c in swell], default=0), 2),
                "maxT": round(max([c["t"] for c in swell], default=0), 1),
            },
        },
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # one row per day; re-running the same day replaces it rather than duplicating
    rows = []
    if path.exists():
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r["date"] != row["date"]] + [row]
    rows.sort(key=lambda r: r["date"])
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return row


# ================================================================= daylight

SPOT_LAT, SPOT_LON = 41.38, -71.50      # centre of the stretch; spots span ~12km, <1 min of sun


def sun_times(day):
    """Sunrise/sunset (local) for a date, NOAA equation. Stdlib math only.

    This exists because a forecast that recommends surfing at 2am is not a
    forecast. Scores are computed for every hour, but only DAYLIGHT hours are
    ever ranked or recommended -- see summarise()."""
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1 + 0.5)                       # fractional year, midday
    eq = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                   - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    dec = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
           - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
           - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    lat = math.radians(SPOT_LAT)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat) * math.cos(dec))
              - math.tan(lat) * math.tan(dec))
    if cos_ha > 1 or cos_ha < -1:
        return None, None                                       # polar day/night; not here
    ha = math.degrees(math.acos(cos_ha))

    def at(minutes_utc):
        base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        return (base + timedelta(minutes=minutes_utc)).astimezone(TZ)

    return at(720 - 4 * (SPOT_LON + ha) - eq), at(720 - 4 * (SPOT_LON - ha) - eq)


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
    h_eff = math.sqrt(sum(h * h for h, _, _ in parts)) * SIZE_BIAS
    dom = max(parts, key=lambda p: p[0])                  # dominant = biggest arriving
    # energy of what actually reaches THIS spot, after the headland has had its say
    return {"h": round(h_eff, 2), "t": dom[1], "d": dom[2],
            "e": round(power(h_eff, dom[1]), 2)}


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
        "hEff": eff["h"], "tEff": eff["t"], "dEff": eff["d"], "eEff": eff["e"],
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
    truth = None if window else fetch_warmwinds(src)   # ground truth, live runs only
    spectrum = None if window else fetch_spectrum(src)  # observed only; no forecast equivalent
    tropics = None if window else fetch_tropics(src)    # 7-day view, BEYOND our 5-day model

    registry = json.loads((HERE / "spots.json").read_text())
    spots = registry["spots"]

    times, conditions = [], {"swell": [], "windWave": [],
                             "wind": {r: [] for r in NWS_GRIDS}, "tide": []}
    tides = []
    daylight, sun = [], {}
    for dt in grid:                       # sunrise/sunset once per day, then reused
        day = dt.date()
        if day not in sun:
            sr, ss = sun_times(day)
            sun[day] = (sr, ss)
        sr, ss = sun[day]
        daylight.append(bool(sr and ss and sr <= dt <= ss))

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
        "spectrum": spectrum,
        "tropics": tropics,
        "groundTruth": truth,
        "conditions": conditions,
        # openWindow / disputedWindow / offshoreDir ship to the client so the compass
        # rose can draw WHAT THIS SPOT CAN SEE against where the energy is actually
        # coming from. That overlay is the whole app in one picture, and it is the one
        # chart no general surf forecast can draw -- they don't have per-spot geometry.
        "spots": [{k: s[k] for k in
                   ("id", "name", "lat", "lon", "facing", "offshoreDir", "openWindow",
                    "windRegion", "bottom", "confidence", "notes")
                   if k in s} | ({"disputedWindow": s["disputedWindow"]}
                                 if s.get("disputedWindow") else {})
                  for s in spots],
        "scores": scores,
        "daylight": daylight,
        "sun": {str(d): {"sunrise": a.isoformat(), "sunset": b.isoformat()}
                for d, (a, b) in sorted(sun.items()) if a and b},
        "summary": summarise(spots, scores, times, daylight),
    }
    if truth:
        # Say plainly where we agree with the human and where we merely differ in taste.
        report["groundTruth"]["agreement"] = compare_truth(truth, report)
    report["summary"]["rationale"] = rationale(report, report.get("groundTruth"))

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


WORTH_IT = 35          # below this, we do not pretend there is a session to recommend


def rationale(report, gt):
    """Say WHY, in plain words, and be honest about where judgement becomes taste.

    "Nothing worth surfing" is a stronger claim than the data supports, and it can
    disagree with the shop's human report while agreeing with it on every number.
    The gap is not data, it is the worth-it bar: 35/100 quietly assumes you want a
    wave with push behind it. 0-1 ft is a longboard day, not a nothing day.

    So the UI shows the reasoning, not just the verdict. Note we reason about OUR
    threshold in OUR voice -- we never put words in Warm Winds' mouth, only their
    numbers."""
    out = []
    sw = report["conditions"]["swell"][0]
    scores = report["scores"]
    day = [i for i in range(24) if report["daylight"][i]]

    heffs = [c["hEff"] for row in scores.values() for i, c in enumerate(row[:24])
             if c["s"] is not None and report["daylight"][i]]
    big = round(max(heffs), 2) if heffs else 0.0

    lims = {}
    for row in scores.values():
        for i in day:
            c = row[i]
            if c["s"] is not None and c["s"] < WORTH_IT:
                lims[c["limiting"]] = lims.get(c["limiting"], 0) + 1
    main = max(lims, key=lims.get) if lims else None

    if main == "size":
        out.append(f"Every spot is size-limited — the biggest surf reaching any of them "
                   f"today is {big} ft.")
    elif main == "wind":
        out.append("Wind is the problem, not the swell — it's onshore at the spots that "
                   "have waves.")
    elif main == "tide":
        out.append("The swell is there; the tide is wrong at the spots that want it.")
    elif main == "period":
        out.append("There is size but no period behind it — the waves have no push.")

    if sw and sw["t"] < 7:
        out.append(f"Period is {sw['t']}s. Under about 7s it's local windchop, not "
                   f"groundswell, so it carries no energy.")

    if gt and gt.get("agreement"):
        a = gt["agreement"]
        lo, hi = a["humanBandFt"]
        if a["size"] == "agree":
            out.append(f"Warm Winds calls it {lo:g}–{hi:g} ft; we make it {a['modelMaxFt']} ft. "
                       f"We agree on the size.")
        else:
            out.append(f"Warm Winds calls it {lo:g}–{hi:g} ft; we make it {a['modelMaxFt']} ft — "
                       f"we're reading it {'smaller' if a['size'] == 'model-smaller' else 'bigger'} "
                       f"than they are. Trust them over us: they're looking at it.")

    # the honest caveat -- this is our taste, not a fact about the ocean
    if main == "size" and big >= 0.8:
        out.append(f"Our “worth it” bar ({WORTH_IT}/100) assumes you want a wave with some "
                   f"push. At {big} ft it's a longboard day, not a nothing day — if you log, "
                   f"go anyway.")
    return out


def summarise(spots, scores, times, daylight):
    """Precomputed rankings. The UI must never recompute a score.

    Only DAYLIGHT hours are ranked. A 2am peak is not a surf recommendation, and
    ranking one is how the first version ended up telling people to paddle out at
    02:00 into a 2/100. Scores still exist for every hour -- the heatmap shows
    them -- but nothing dark is ever *recommended*."""
    def best_in(spot_id, lo, hi):
        best = None
        for i in range(lo, min(hi, len(times))):
            c = scores[spot_id][i]
            if c["s"] is None or not daylight[i]:
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

    week = rank(0, len(times))
    # The question a flat forecast still has to answer: when is it next worth going?
    nxt = None
    for i in range(len(times)):
        if not daylight[i]:
            continue
        cands = [(scores[s["id"]][i]["s"], s["id"]) for s in spots
                 if scores[s["id"]][i]["s"] is not None]
        if cands and max(cands)[0] >= WORTH_IT:
            sc, sid = max(cands)
            nxt = {"spot": sid, "s": sc, "at": times[i]}
            break

    return {
        "bestNow": sorted(now, key=lambda x: -x["s"]),
        "bestToday": rank(0, today),
        "bestWeek": week,
        "worthItThreshold": WORTH_IT,
        # null = nothing in the whole 5-day window clears the bar. Say so; don't
        # dress up the least-bad hour as a recommendation.
        "nextWindow": nxt,
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

    if not a.start:
        row = log_calibration(report, REPO / "data" / "calibration-log.jsonl")
        if row:
            h = row["human"]
            print(f"  ground truth: {h['waveHeightFt']} ft @ {h['periodS']}s "
                  f"(Warm Winds, {h['updated']}) -> logged")
        else:
            print("  ! no ground truth logged (Warm Winds unreachable or restructured)",
                  file=sys.stderr)

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
