---
name: surf-forecast
description: Score the surf at Narragansett / Point Judith / Matunuck for the next 5 days, per spot per hour, and emit forecast.json. Use when asked "what's the forecast", "where should I surf", "is it any good", "what's it doing at Deep Hole", or for anything about southern Rhode Island surf conditions.
---

# Surf forecast — Narragansett → Point Judith → Matunuck

Produces a **structured JSON** report: current conditions plus a 5-day forecast, scored
**per surf spot, per hour**. Architecture spec: `docs/forecast-agent.md`. Registry: `spots.json`.

## Run it

```sh
python3 .claude/skills/surf-forecast/forecast.py            # -> data/forecast.json
```

Python 3 stdlib only. No dependencies, no build step, no API keys. ~5 HTTP calls, cached
under `.cache/`.

Replay a past swell (same normalise + score path, reanalysis sources):

```sh
python3 .claude/skills/surf-forecast/forecast.py \
        --from 2023-09-14 --to 2023-09-18 --out data/forecast-example-swell.json
```

Other flags: `--out PATH`, `--no-cache`.

## Reading the output

`data/forecast.json` — see §6 of the spec for the full schema. The three things that matter:

- **`scores[spotId][i].s`** is `0–100` **or `null`**. `null` means *no data*, and it is not
  the same as `0`. Never rank, colour-map, or average a `null` as if it were a low score.
  `status` is `ok` | `partial` | `no_data`.
- **`limiting`** names the factor holding the spot back (`size` | `wind` | `period` | `tide`
  | `none`). This is the honest one-word answer to "why isn't it good" — and it gives the UI
  a channel that isn't colour.
- **`caution: "diffraction-taper-inferred"`** means the spot is scoring on swell that only
  reaches it by bending around the headland. That taper is **our inference, not a sourced
  rule**. Hedge when reporting it.

`summary.bestNow` / `bestToday` / `bestWeek` are precomputed and ranked. The UI never
recomputes a score.

## Answering "what's the forecast?"

1. Run the script.
2. Read `summary.bestNow` / `bestToday` / `bestWeek`.
3. Lead with the spot and the hour, then say **what's limiting** the others.

Say **"Deep Hole, Thursday dawn — 4.7 ft at 11 s, wind offshore"**, not "Deep Hole scores 62."

## The one thing to know about this coast

Point Judith is a hard corner; the shoreline rotates ~150° across it. **The spots want
opposite things, and that is the entire value of this tool.**

A 15 kt **NE** wind is offshore at K38, Camp Cronin and Point Judith South (they score ~95)
and is nearly onshore at **Monahan's** (which scores 0). A **SW** wind flips it: the Matunuck
reefs blow out while Narragansett stays ridable. An **SSW swell** reaches Deep Hole through the
Block Island–Montauk gap and is **blocked from Narragansett entirely**.

If a run ever shows every spot lighting up together, **something is broken** — that is the
failure signature of this model, and it is worth checking before you trust the output.

## Honesty rules — these are not optional

- **Scores are ordinal, not cardinal.** Every calibration constant (diffraction κ, wind kill
  thresholds, tide widths) is physically motivated but numerically invented and **uncalibrated**.
  Say "Deep Hole beats Green Hill today," never "Deep Hole is a 62 out of 100."
- **A flat board is a flat ocean.** Southern RI in summer is routinely 1 ft at 5 s and every
  spot legitimately scores 0. Do not interpret that as a bug, and do not talk it up.
- **`spots.json` carries a `confidence` field.** `partial` and `unconfirmed` spots (K38,
  Camp Cronin, Green Hill's size range) are guesses. Surface that; don't bury it.
- **Camp Cronin essentially never scores.** It sits fully behind the Point Judith breakwater
  with no direct line of sight, so it only receives diffracted energy. That may be correct, or
  the constants may be too harsh. Nobody has checked. See spec §8.7.
- Calibrate against the **Warm Winds** human report (`warmwinds.com/surf-report`) — link out
  to it, never republish their prose.

## Sources (all verified live, no keys)

| What | Source |
|---|---|
| Wave forecast | Open-Meteo Marine, **`models=ncep_gfswave016`** (GFS-Wave 0.16°) |
| Wind | NWS `api.weather.gov/gridpoints/BOX/{66,56 · 65,53 · 63,54}` — **three cells, one per sub-region** |
| Tide | NOAA CO-OPS station **8455083** (Point Judith, Harbor of Refuge) |
| Observed swell | NDBC **44097** (Block Island), fallback **44085** |

Three traps, all of which have already bitten:
- **NWS 403s on an empty User-Agent**, and its values are *variable-duration* ISO-8601
  intervals — `windSpeed`, `windGust` and `windDirection` come back with **different array
  lengths**. Expand them; never zip positionally.
- Open-Meteo's `best_match` is **MFWAM, not GFS-Wave**. Pass `models=` explicitly.
- NDBC `.spec` gives `SwD`/`WWD` as **compass text** ("SE"), not degrees.

## UI

`demos/` has three standalone views over the same JSON (heatmap, sparklines, ranked).
`fetch` is blocked on `file://`, so serve them: `python3 -m http.server 8000` →
`localhost:8000/demos/`.
