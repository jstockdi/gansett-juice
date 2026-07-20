#!/usr/bin/env python3
"""
Log an eyes-on session as a FINGERPRINT the next forecast can match against.

Warm Winds gives one human report a day. This gives the thing it can't: what a
spot actually did, at the hour you were on it. Each surfed hour is stored with
its raw conditions AND the model's quality vector q{size,wind,period,tide} -- the
fingerprint. A future run measures how close each upcoming hour is to your logged
fingerprints and, if it lands within threshold, leads the description with it
(see local_knowledge / MATCH_TAU in forecast.py).

    log-session.py point-judith-south --rating fun --from 7 --to 9 --because good-size
    log-session.py matunuck --rating fun --unverified --note "surfers out, looked good"

Ratings are ordinal words: flat | marginal | fun | firing | blown-out.
--because splits the one word into axes: good-size | too-small | too-crossed |
    too-crowded | wrong-tide  (optional, comma-separated).
--from/--to are hours of the day you surfed. Omit both and it snapshots the day's
    best-scoring daylight hour for that spot.
--unverified marks a beach/webcam call, not a paddle-out. Weaker evidence; the
    read-back says so.
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FORECAST = REPO / "data" / "forecast.json"
LOG = REPO / "data" / "sessions.jsonl"

RATINGS = ["flat", "marginal", "fun", "firing", "blown-out"]
BECAUSE = ["good-size", "too-small", "too-crossed", "too-crowded", "wrong-tide"]


def constants_version():
    """Tag each row with the constants it was logged under, so a later recalibration
    is detectable (the stored q is only comparable within one constants regime)."""
    m = re.search(r"^SIZE_BIAS\s*=\s*([\d.]+)", (HERE / "forecast.py").read_text(), re.M)
    return f"SIZE_BIAS={m.group(1) if m else '?'}"


def hour_records(d, spot, day, hours):
    """Build one fingerprint record per surfed grid-hour."""
    if spot not in d.get("scores", {}):
        known = ", ".join(s["id"] for s in d.get("spots", []))
        sys.exit(f"unknown spot '{spot}'. known: {known}")
    region = next(s["windRegion"] for s in d["spots"] if s["id"] == spot)
    times, cond, scores = d["times"], d["conditions"], d["scores"][spot]

    # day's tide swing, so tide position is recoverable (range isn't shipped as a field)
    day_ft = [cond["tide"][i]["ft"] for i, t in enumerate(times)
              if t[:10] == day and cond["tide"][i]]
    lo, hi = (min(day_ft), max(day_ft)) if day_ft else (None, None)

    # which grid indices this session covers
    idx = []
    for i, t in enumerate(times):
        if t[:10] != day:
            continue
        h = datetime.datetime.fromisoformat(t).hour
        if hours is None or hours[0] <= h <= hours[1]:
            idx.append(i)
    if hours is None:                                    # default: the day's best hour
        day_idx = [i for i, t in enumerate(times)
                   if t[:10] == day and d["daylight"][i]]
        if not day_idx:
            sys.exit(f"no daylight hours for {day} in forecast.json")
        idx = [max(day_idx, key=lambda i: scores[i]["s"] if scores[i]["s"] is not None else -1)]

    out = []
    for i in idx:
        ti = cond["tide"][i]
        tide = None
        if ti is not None:
            pos = round((ti["ft"] - lo) / (hi - lo), 2) if hi and hi > lo else None
            tide = {"ft": ti["ft"], "stage": ti["stage"],
                    "range": round(hi - lo, 2) if hi is not None else None, "pos": pos}
        out.append({
            "at": times[i],
            "swell": cond["swell"][i],
            "windWave": cond["windWave"][i],
            "wind": cond["wind"][region][i],
            "tide": tide,
            "q": scores[i].get("q"),                     # the fingerprint
            "s": scores[i]["s"],
            "limiting": scores[i].get("limiting"),
        })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spot")
    ap.add_argument("--rating", required=True, choices=RATINGS)
    ap.add_argument("--from", dest="h_from", type=int, metavar="HOUR")
    ap.add_argument("--to", dest="h_to", type=int, metavar="HOUR")
    ap.add_argument("--because", default="", help="comma-separated: " + " ".join(BECAUSE))
    ap.add_argument("--note", default="")
    ap.add_argument("--unverified", action="store_true")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    a = ap.parse_args()

    if (a.h_from is None) != (a.h_to is None):
        ap.error("--from and --to must be given together (or neither)")
    because = [b.strip() for b in a.because.split(",") if b.strip()]
    bad = [b for b in because if b not in BECAUSE]
    if bad:
        ap.error(f"unknown --because tag(s): {bad}. choose from {BECAUSE}")

    if not FORECAST.exists():
        sys.exit("no data/forecast.json -- run forecast.py first")
    d = json.loads(FORECAST.read_text())
    day = a.date or datetime.date.today().isoformat()
    hours = (a.h_from, a.h_to) if a.h_from is not None else None

    row = {
        "date": day,
        "loggedAt": datetime.datetime.now().astimezone().isoformat(),
        "spot": a.spot,
        "rating": a.rating,
        "because": because,
        "verified": not a.unverified,
        "note": a.note,
        "constantsVersion": constants_version(),
        "hours": hour_records(d, a.spot, day, hours),
    }
    with LOG.open("a") as f:
        f.write(json.dumps(row) + "\n")

    hs = row["hours"]
    at = ", ".join(h["at"][11:16] for h in hs)
    tag = "" if row["verified"] else " [unverified]"
    print(f"logged: {a.spot} {a.rating}{tag} on {day} @ {at} "
          f"({len(hs)} hr fingerprint{'s' if len(hs) != 1 else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
