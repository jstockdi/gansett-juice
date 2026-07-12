# Task: Surf conditions + 5-day forecast agent for Narragansett → Point Judith → Matunuck

## Context

This repo (`gansett-juice`) is currently a single `index.html`: a zero-build, vanilla web-components
surf cam viewer. Key facts to respect:

- No bundler, no npm, no framework. Plain ES modules / custom elements, everything inline or
  loaded from a CDN.
- `index.html` defines `<surfcam-app>`, `<surfcam-view>`, `<surfcam-dock>` and a `CAMS` array of
  cam descriptors (`{ name, icon, type: 'iframe'|'hls'|'mjpeg', url }`).
- Mobile: dock and tab bar hide in landscape; cams are switched by swiping.

We are adding a **conditions + forecast agent**: a skill plus supporting code that produces a
**structured JSON** report of current conditions and a **5-day forecast**, scored **per surf spot**,
for the Narragansett / Point Judith / Matunuck stretch of southern Rhode Island. The JSON then
drives a **new UI component** showing which spots are good, and when, over time.

## Deliverables

1. `docs/forecast-agent.md` — architecture spec (write this FIRST, before any code).
2. `.claude/skills/surf-forecast/SKILL.md` — the agent skill.
3. Supporting code for fetching/scraping/normalizing/scoring.
4. A committed sample `forecast.json` produced by an actual run (not hand-written).
5. **2–3 UI component options** for visualizing the report — build them as separate
   standalone demo files so they can be compared side by side. Do not pick one; present them.

Work in this order and **stop after step 1 for review** before writing code.

## Step 1 — Research (do this properly, cite what you find)

### 1a. Spots and what makes them work

Determine, from real sources rather than memory, the surf breaks worth covering in this stretch and
the conditions each one wants. Cover at minimum swell direction window, swell period sensitivity,
ideal wind direction, tide preference, and the size range where the spot turns on/blows out.

Candidate spots to confirm/extend — **treat these as unverified hints, not ground truth**:
Narragansett Town Beach, The Wall / Narragansett Beach south end, Monahan's Dock, Point Judith
Lighthouse, K-38 / Camp Cronin, Deep Hole, East Matunuck, Matunuck (Deep Hole area), Green Hill,
Second Beach / Ruggles (may be out of scope — decide and say why).

The value of this app is that these spots want *different* conditions — a swell/wind combo that
blows out The Wall can be exactly what Deep Hole wants. The scoring model must capture that
contrast; a model where all spots light up together is a failed model.

### 1b. Sources

Evaluate and document, for each: what data it gives, update cadence, whether it needs scraping vs.
has a usable API/JSON endpoint, and its terms of use.

- `hopewaves.app`
- `surfcaptain.com`
- Warm Winds surf report pages (local shop reports — human-written, good for ground truth
  calibration against the model's scores)
- Direct swell/wind/tide sources. Prefer official/free-to-use feeds over scraping where they exist:
  - NDBC buoys — identify the right ones for this coastline (Block Island / Montauk area buoys
    are the usual swell-upstream reference; confirm which are live).
  - NOAA/NWS gridpoint forecast API (wind, no key required).
  - NOAA CO-OPS tides — find the correct station for Point Judith / Newport.
  - Any wave model output (e.g. WaveWatch III / GFS-Wave derived) that is fetchable without a key.

Record station/buoy IDs and exact endpoint URLs in the spec — those are the load-bearing details.

### 1c. Scraping

Use the **obscura browser** for any scraping. Confirm how it is invoked in this container before
designing around it (`obscura --help`, or whatever the actual entrypoint is), and write the real
invocation into the spec.

Prefer stable data endpoints over DOM scraping wherever one exists; scrape only what has no feed.
Note in the spec which sources are scraped and therefore fragile.

## Step 2 — Spec (`docs/forecast-agent.md`)

Must contain:

- **Spot registry** — one entry per spot, with coordinates and its condition windows, in the exact
  shape the code will consume.
- **Source table** — endpoints, IDs, cadence, scrape-vs-API, failure modes.
- **Data flow** — fetch → normalize to a common time grid → score per spot per timestep → emit JSON.
  Be explicit about the time grid (interval, horizon, timezone — use local RI time, handle DST) and
  about how sources with different cadences get aligned.
- **Scoring model** — how swell direction/period/height, wind, and tide combine into a per-spot,
  per-timestep score. State the formula. State how "no data" differs from "bad conditions" — these
  must not collapse to the same value.
- **Output JSON schema** — versioned. Design it to be directly renderable: the UI should not need
  to recompute scores.
- **Caching / rate limiting** — be a good citizen with the scraped sources.
- **Open questions** — anything you had to guess at.

## Step 3 — Skill + code

Skill lives at `.claude/skills/surf-forecast/SKILL.md`, with code alongside it. Keep the code
consistent with this repo's character: no build step, minimal dependencies, readable.

The skill should be runnable on demand ("what's the forecast?") and produce the JSON artifact.

## Step 4 — UI options

Build **2–3 distinct visual approaches** over the same JSON, as standalone demo files (each loading
the committed sample `forecast.json`) so they can be judged against real data. All must show
**which spots are good, and when** — timeseries, not just a current snapshot.

Suggested directions (pick your own if better):
- A spots × time heatmap grid — dense, scannable, whole 5 days in one view.
- Per-spot sparkline stack with the driving variables (swell/wind/tide) underneath.
- A "best right now / best today / best this week" ranked view with drill-down.

Constraints:
- Match the existing app: vanilla custom elements, no framework, dark, works on phone.
- Use the `dataviz` skill for the visual design (color, scales, legends, accessibility).
- Colour must not be the only channel encoding quality — it needs to survive colour-blindness and
  a phone screen in direct sun.

Then report back with the options and a recommendation; do not integrate one into `index.html`
without a decision.

## Notes

- Do not fabricate spot characteristics, buoy IDs, or endpoints. If research doesn't confirm
  something, say so in the Open Questions section rather than filling the gap with a guess.
- Verify every endpoint actually returns data before writing it into the spec.
