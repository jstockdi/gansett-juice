#!/usr/bin/env python3
"""
The compounding step: read the calibration log, ask whether the model is drifting
from the human, and PROPOSE a correction -- never apply one silently.

Every daily run logs what Warm Winds called, beside what we called
(data/calibration-log.jsonl). This reads that history and answers one question:

    Is the model systematically reading the surf bigger or smaller than the human?

If it is, it proposes a new SIZE_BIAS -- a single multiplier on the effective wave
height -- and the CI job opens a PR with the evidence. A human merges it or doesn't.

Why a proposal and not an auto-fix:
  * The constants are already unsourced guesses. A robot silently tuning guesses
    against a 3-week sample would produce numbers nobody could defend, and the model
    would drift somewhere no one chose.
  * The log is one report a day. That is a small, autocorrelated sample -- a flat
    fortnight teaches you almost nothing, and a single hurricane week could yank a
    constant hard. Sample size is checked, and the step per run is capped.
  * A size disagreement can mean the MODEL is wrong or the THRESHOLD is wrong, and
    those need opposite fixes. We only ever propose against the size, which is the
    part we can actually check against a human's stated feet.

Usage:
    python3 calibrate.py            # human-readable report
    python3 calibrate.py --json     # machine-readable, for the CI job
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LOG = REPO / "data" / "calibration-log.jsonl"

MIN_ROWS = 14          # below this the sample is too small and too autocorrelated to fit
MAX_STEP = 0.15        # never move the bias more than 15% in one proposal
TRIGGER = 0.10         # only propose if the model is off by more than 10%
BAND = re.compile(r"([\d.]+)\s*-\s*([\d.]+)")


def load():
    if not LOG.exists():
        return []
    return [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]


def current_bias():
    """Read SIZE_BIAS out of forecast.py (1.0 if it isn't there yet)."""
    m = re.search(r"^SIZE_BIAS\s*=\s*([\d.]+)", (HERE / "forecast.py").read_text(), re.M)
    return float(m.group(1)) if m else 1.0


def analyse():
    rows = load()
    pairs = []
    for r in rows:
        o = (r.get("human") or {}).get("outlook") or []
        if not o:
            continue
        m = BAND.match(o[0]["sizeFt"])
        if not m:
            continue
        human = (float(m.group(1)) + float(m.group(2))) / 2      # midpoint of their band
        model = (r.get("model") or {}).get("maxHEffToday")
        if not model or human <= 0:
            continue
        pairs.append({"date": r["date"], "human": human, "model": model,
                      "ratio": model / human})

    out = {
        "rows": len(rows), "usable": len(pairs), "minRows": MIN_ROWS,
        "currentBias": current_bias(), "propose": False,
    }
    if len(pairs) < MIN_ROWS:
        out["verdict"] = (f"Not enough data: {len(pairs)} usable day(s), need {MIN_ROWS}. "
                          f"The log grows by one row a day; nothing to propose yet.")
        return out, pairs

    ratios = [p["ratio"] for p in pairs]
    med = statistics.median(ratios)                      # median: one hurricane week
    out["medianRatio"] = round(med, 3)                   # must not swing the fit
    out["spread"] = [round(min(ratios), 2), round(max(ratios), 2)]
    out["agreeWithin25pct"] = sum(1 for r in ratios if 0.75 <= r <= 1.25)

    if abs(med - 1.0) <= TRIGGER:
        out["verdict"] = (f"Model and human agree: median ratio {med:.2f} "
                          f"(within {int(TRIGGER*100)}%). No change proposed.")
        return out, pairs

    # we read `med` times as big as the human, so scale by 1/med -- capped
    ideal = out["currentBias"] / med
    step = max(-MAX_STEP, min(MAX_STEP, (ideal - out["currentBias"]) / out["currentBias"]))
    proposed = round(out["currentBias"] * (1 + step), 3)

    out["propose"] = True
    out["proposedBias"] = proposed
    out["direction"] = "over-reading" if med > 1 else "under-reading"
    out["verdict"] = (
        f"Model is {out['direction']} the surf: median model/human ratio is {med:.2f} "
        f"across {len(pairs)} days. Propose SIZE_BIAS {out['currentBias']} -> {proposed} "
        f"(step capped at {int(MAX_STEP*100)}%).")
    return out, pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    out, pairs = analyse()
    if a.json:
        print(json.dumps(out, indent=1))
        return 0

    print(f"calibration log: {out['rows']} rows, {out['usable']} usable")
    print(f"current SIZE_BIAS: {out['currentBias']}")
    print()
    if pairs:
        print("  date         human(ft)  model(ft)  ratio")
        for p in pairs[-14:]:
            print(f"  {p['date']}   {p['human']:6.2f}    {p['model']:6.2f}   {p['ratio']:.2f}")
        print()
    print(out["verdict"])
    if out["propose"]:
        print(f"\n-> would open a PR: SIZE_BIAS {out['currentBias']} -> {out['proposedBias']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
