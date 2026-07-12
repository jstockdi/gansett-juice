# Surf conditions + 5-day forecast agent — architecture spec

Status: **built**. Skill at `.claude/skills/surf-forecast/`, UI options in `demos/`.
Scope: Narragansett → Point Judith → Matunuck, southern Rhode Island.
Date of research: 2026-07-12. Every endpoint below was fetched live on that date.

**Validation of the core claim** (the model must make spots *disagree*): over a replay of the
Hurricane Lee swell (2023-09-14 → 18), the mean best-worst spread across the 10 spots is
**69.9 points**, and only **3 of 96 hours** have every spot within 10 points of each other. At the
most-divided hour, **Point Judith East scores 100 while Point Judith South scores 0** — two faces
of the same headland, on the same swell, because 132° is inside one window and outside the other
(`hEff` 5.51 ft vs 0.40 ft). A model where all spots light up together would be a failed model;
this one does not.

---

## 0. Evidence convention

Three labels are used throughout, and they are load-bearing:

- **[SOURCED]** — from a page or feed actually fetched; URL given.
- **[DERIVED]** — computed from OpenStreetMap coastline + breakwater geometry
  (`natural=coastline`, `man_made=breakwater`, via Overpass) by shoreline-normal and
  line-of-sight raycasting. Reproducible, but it is our computation, not a citation.
- **[UNCONFIRMED]** — research did not confirm it. These are **not** filled with plausible
  guesses; they are listed in §8 Open Questions.

Anything in the spot registry carrying a `confidence` other than `sourced` is a thing we
are guessing at, and the code must surface it rather than bury it.

---

## 1. The core finding: this is two coastlines, not one

Point Judith is a hard corner. Across it the shoreline azimuth rotates roughly 150°.
Everything interesting about this app falls out of that one fact.

| Spot | Faces (°) [DERIVED] | Offshore wind [SOURCED] |
|---|---|---|
| Monahan's Dock | ~70 (ENE) | W, WNW, NW (Surfline) |
| Narragansett Town Beach | ~130 (SE) | NW (Surfline) |
| Point Judith — east face | ~100–110 (E/ESE) | WNW–NW (Surfline) |
| Point Judith — south face | ~195 (SSW) | NE (surf-forecast, "Lighthouse-Southside") |
| K38/39 · Camp Cronin | ~225–245 (SW/WSW) | **NE** (surf-forecast) |
| Deep Hole / Matunuck | ~170–185 (S/SSW) | N–NNE (Surfline / surf-forecast) |
| Green Hill | ~173 (S) | NNE (surf-forecast) |

The derived facings independently reproduce the sourced offshore-wind directions
(offshore ≈ facing − 180° in every row). That mutual agreement is the strongest validation
we have, and it is why the model can be trusted to make spots *disagree*.

**The contrast, stated plainly:**

- A **NE wind** is offshore at K38, Camp Cronin, the Lighthouse south side, and (with shelter)
  Matunuck — and is within ~25° of straight onshore at **Monahan's**. One wind, opposite verdicts.
- A **SW wind** is straight onshore at Deep Hole / Matunuck / Green Hill → blown out — while it
  has an offshore component at **Monahan's** and is merely cross-shore at Narragansett.
- An **SSW swell (195–215°)** reaches Matunuck/Deep Hole through the Block Island–Montauk gap
  and is **blocked from Narragansett entirely** by the headland. The cleanest opposite-spots
  pairing in the region.
- **NW is the universal offshore** and **strong SE wind kills everything**
  ([Warm Winds](https://www.warmwinds.com/surfing-in-rhode-island): "the only winds that are no
  good for Rhode Island … are strong southeast winds").

A scoring model in which all spots light up together is a failed model. The mechanism that
prevents that is §5.1 — direction is not a score multiplier, it changes *how much swell
physically arrives at each spot*.

### Swell shadows [DERIVED]

Angular arc subtended by each obstruction, per spot. The shadow **slides with longitude**:

| From | Block Island blocks | Long Island / Montauk blocks |
|---|---|---|
| Narragansett / Monahan's | 194–206° | 220–247° |
| Point Judith | 193–210° | 224–252° |
| Deep Hole / Matunuck | 181–196° | 218–250° |
| **Green Hill** | **167–183°** ← due south | 213–249° |

Green Hill is shadowed from a **straight-S swell** while Matunuck, 8 km east, is not.

Corroboration [SOURCED], [Warm Winds](https://www.warmwinds.com/surfing-in-rhode-island): RI's
window runs "from straight East (around 90-100 degrees) to South/southwest (around 220 degrees)";
220–270° gives "a very limited amount of surf". Our raycast (218–252° blocked by Montauk/LI)
matches that closely.

---

## 2. Spot registry

This is the exact shape the code consumes — `spots.json`, hand-maintained, not fetched.

```jsonc
{
  "id":          "deep-hole",
  "name":        "Deep Hole",
  "lat": 41.3725, "lon": -71.5351,
  "facing":      173,              // deg the break looks out to [DERIVED]
  "openWindow":  [[135,180],[195,215]],  // swell bearings with direct line of sight [DERIVED]
  "offshoreDir": 15,               // wind FROM this bearing is offshore [SOURCED where available]
  "size":        { "works": 1, "idealLo": 3, "idealHi": 7, "maxHandles": 10 },  // ft, face
  "period":      { "tMin": 5, "tGood": 10 },   // s
  "tide":        { "pref": "low", "strength": 0.85, "risingBonus": 0 },
  "windRegion":  "matunuck",       // -> NWS gridpoint cell
  "bottom":      "cobble reef",
  "confidence":  "sourced",
  "notes":       "Low tide essential. Rocks extend ~100ft at low."
}
```

`offshoreDir` policy: **a source beats our raycast.** Use the sourced offshore direction where
one exists; fall back to `facing − 180` only where none does. Divergences are logged in §8.

### The registry

| id | lat, lon | facing | openWindow | offshoreDir | size works/ideal/max (ft) | period tMin/tGood | tide (pref, strength) | windRegion | confidence |
|---|---|---|---|---|---|---|---|---|---|
| `narragansett-town-beach` | 41.4342, −71.4531 | 130 | 90–175 | 315 (NW) | 1.5 / 2.5–5 / 7 | 6 / 10 | mid, 0.30 | narragansett | sourced |
| `the-wall` | 41.4290, −71.4540 | 95 | 85–180 | 275 | 2 / 3–7 / 10 | 6 / 10 | any, 0.0 | narragansett | **name unconfirmed** |
| `monahans-dock` | 41.4227, −71.4541 | 70 | 85–165 | 290 (WNW) | 3 / 4–12 / 18 | 8 / 13 | low, 0.40 | narragansett | sourced |
| `point-judith-east` | 41.3626, −71.4801 | 105 | 70–160 | 305 (WNW) | 3 / 4–8 / 16 | 5 / 10 | mid, 0.50 (+rising) | point-judith | sourced (split derived) |
| `point-judith-south` | 41.3615, −71.4805 | 195 | 160–190, 210–220 | 45 (NE) | 3 / 4–8 / 16 | 5 / 10 | mid, 0.50 (+rising) | point-judith | sourced (split derived) |
| `k38` | 41.3670, −71.4947 | 235 | 175–190 | 45 (NE) | 2 / 3–6 / 8 | 5 / 10 | low, 0.50 | point-judith | partial |
| `camp-cronin` | 41.3655, −71.4980 | 240 | *(none)* | 45 (NE) | 2 / 3–5 / 6 | 9 / 14 | low, 0.50 | point-judith | **unconfirmed** |
| `deep-hole` | 41.3725, −71.5351 | 173 | 135–180, 195–215 | 15 (N/NNE) | 1 / 3–7 / 10 | 5 / 10 | low, 0.85 | matunuck | sourced |
| `matunuck-trestles` | 41.3733, −71.5388 | 178 | 135–180, 195–215 | 22 (NNE) | 1 / 3–7 / 10 | 8 / 13 | low, 0.60 | matunuck | sourced |
| `matunuck-the-point` | 41.3740, −71.5410 | 184 | 135–180, 195–215 | 22 (NNE) | 2 / 3–8 / 12 | 5 / 10 | low-mid, 0.50 (+rising) | matunuck | sourced |
| `green-hill` | 41.3643, −71.6011 | 173 | 100–165, 185–210 | 22 (NNE) | 2 / 3–6 / 8 | 5 / 10 | low, 0.50 | matunuck | partial |

**`camp-cronin` has an empty `openWindow`** — the raycast finds no direct line of sight past the
Point Judith breakwater. It can therefore only ever score through the diffraction term (§5.1),
which is exactly right: LocalWiki describes The Ks as a "mushy left formed by the wave that passes
through the gap." A spot fed only by diffracted energy is a real thing and the model should say so.

### Deliberately excluded

- **`east-matunuck`** — dropped. Not a named break in *any* surf source fetched (absent from
  Surfline's taxonomy, surf-forecast's 33-break RI list, and LocalWiki), and [DERIVED] it is the
  most headland-shadowed stretch on the south shore (open window only 160–180°).
- **Second Beach / Ruggles** — **out of scope.** Middletown/Newport, ~25–30 km east across the
  bay, with different shadow geometry (Block Island sits at 211–223° from them, not 180–205°).
  They belong to a different region, not this stretch. Worth noting for a future "regional bailout"
  feature: Surfline says Second Beach's western cliffs "provide some protection from prevailing
  southwest winds" — i.e. it works precisely when a SW wind has flattened Matunuck.

---

## 3. Source table

Every "verified" row below was fetched live on 2026-07-12 and returned data.

| Source | Data | Cadence | API / scrape | Key | Terms | Endpoint | Failure modes |
|---|---|---|---|---|---|---|---|
| **Open-Meteo Marine** (`ncep_gfswave016`) | wave/swell/wind-wave height, period, direction — **the forecast wave source** | hourly, 10-day horizon; GFS runs 4×/day | JSON API | none | CC-BY 4.0, **free tier is non-commercial**, <10k/day | see §3.1 | grid snaps to 41.333, −71.50 (offshore of the breaks, not at them) |
| **NWS gridpoints** | windSpeed, windDirection, windGust — **the wind source** | ~hourly, 7-day horizon | JSON (GeoJSON) | none, but **User-Agent mandatory** | US Govt public domain | `api.weather.gov/gridpoints/BOX/{x},{y}` | **empty UA → HTTP 403 (verified).** Values are *variable-duration ISO-8601 intervals* — must be expanded, not zipped. Units are `km_h-1` |
| **NOAA CO-OPS** | tide **predictions** | on demand (deterministic) | JSON | none | US Govt public domain | station **8455083** Point Judith, Harbor of Refuge | **prediction-only** — `product=water_level` returns "No data … not offered at this station" (verified). Use **8452660 Newport** for live water level / water temp |
| **NDBC 44097** Block Island | WVHT/DPD/MWD + swell/wind-wave split (`.spec`) | ~10–30 min | fixed-width text | none | public domain | `ndbc.noaa.gov/data/realtime2/44097.spec` | fields go `MM` (missing) constantly. **`SwD`/`WWD` are compass text ("SE","SSE"), not degrees — `MWD` is degrees** |
| **NDBC 44085** Buzzards Bay "260" | same | ~30 min | fixed-width text | none | public domain | `…/realtime2/44085.spec` | closest wave buoy (41.387, −71.032) but sits *east* of the point in RI Sound |
| **NDBC 44008** Nantucket | same | ~30 min | text | none | public domain | `…/realtime2/44008.spec` | 115 nm offshore — upstream groundswell indicator |
| **NWPR1** Newport | observed wind | ~hourly | text | none | public domain | `…/realtime2/NWPR1.txt` | wind only, no waves |
| ~~NDBC 44017 Montauk~~ | — | — | — | — | — | — | **DEAD — 404 on both `.txt` and `.spec` (verified).** Listed in `activestations.xml` with `met="n"`. Do not design around it |
| ~~surfcaptain.com~~ | 5-day per-spot forecast | — | scrape only (Nuxt `__NUXT__` payload) | — | **`/terms` forbids reuse** | — | **EXCLUDED — licence, not tech.** "You will not modify, publish, transmit, reverse engineer … create derivative works, or in any way exploit any of the content" |
| **hopewaves.app** | 17-day forecast, buoy, tides — purpose-built for this coast | model 4×/day | **undocumented open JSON API** | none | robots.txt `Disallow:` (nothing blocked); **no ToS granting use** | `hopewaves.app/api/surf/forecast` | **convenience only, not a core dependency.** CORS-locked to `hopewaves.web.app` → needs a server-side proxy. Undocumented ⇒ can change without notice |
| **Warm Winds** | **human-written report** + wetsuit call + 3-day outlook | ~daily, mornings | scrape (static HTML, no JS) | — | robots.txt has zero `Disallow`; no ToS; prose is **copyrighted** | `warmwinds.com/surf-report` | hand-authored ⇒ goes stale on weekends. **Use for score calibration only; link out, never republish the prose** |
| ~~NOMADS GFS-Wave~~ | HTSGW/PERPW/DIRPW | 4×/day | **GRIB2 only** | none | public domain | — | **OPeNDAP is retired** (SCN 25-81, verified). Only the GRIB filter remains → needs wgrib2/eccodes. Open-Meteo gives us the same model without the decoder |

### 3.1 The wave-model choice — a correction worth stating

Open-Meteo's `best_match` at this location is **not** WaveWatch III / GFS-Wave. Resolved by
comparison (verified — identical values byte-for-byte):

| `models=` | grid snapped to | wave_height[0:4] (ft) |
|---|---|---|
| `best_match` | 41.2917, −71.4583 | 2.034, 2.034, 2.034, 2.034 |
| `meteofrance_wave` | 41.2917, −71.4583 | 2.034, 2.034, 2.034, 2.034 ← identical |
| **`ncep_gfswave016`** | **41.3333, −71.5000** | **1.378, 1.378, 1.312, 1.312** |
| `ncep_gfswave025` | 41.2500, −71.5000 | 1.772, 1.706, 1.706, 1.640 |

`best_match` == MeteoFrance MFWAM. **We explicitly pass `models=ncep_gfswave016`**: it is the
GFS-Wave Atlantic 0.16° subset — the WaveWatch III-derived output the brief asked for — it is the
finest grid available and snaps closest to shore, and it needs no key and no GRIB decoding. It
reads *smaller* than the coarser models (1.378 vs 2.034 ft), which is the expected signature of a
finer grid resolving coastal sheltering.

Verified call:

```
https://marine-api.open-meteo.com/v1/marine
  ?latitude=41.36&longitude=-71.49
  &hourly=wave_height,wave_direction,wave_period,
          swell_wave_height,swell_wave_direction,swell_wave_period,
          wind_wave_height,wind_wave_direction,wind_wave_period
  &models=ncep_gfswave016
  &timezone=America%2FNew_York&forecast_days=5&length_unit=imperial
```

### 3.2 Wind — three cells, not one

The three sub-regions land in **three different NWS gridpoint cells** (verified), so wind is
resolved per-region rather than as one blob for the whole coast:

| windRegion | gridpoint | resolved from |
|---|---|---|
| `narragansett` | `BOX/66,56` | 41.4262, −71.4495 |
| `point-judith` | `BOX/65,53` | 41.3633, −71.4900 |
| `matunuck` | `BOX/63,54` | 41.3785, −71.5300 |

Required header (empty UA → 403, verified):
`User-Agent: gansett-juice/0.1 (+https://github.com/jstockdi/gansett-juice)`

### 3.3 Tides

```
https://api.tidesandcurrents.noaa.gov/api/prod/datagetter
  ?product=predictions&application=gansett-juice
  &begin_date=YYYYMMDD&end_date=YYYYMMDD
  &datum=MLLW&station=8455083&time_zone=lst_ldt&units=english&format=json
  [&interval=hilo]           # omit for 6-min series
```

`time_zone=lst_ldt` gives local standard/daylight time — **this is what handles DST**, and it is
why we do not do our own offset arithmetic on tide data. Both the 6-min series and the hi/lo series
were verified. Hi/lo is needed for the daily tide range that normalises the tide score (§5.5).

---

## 4. Data flow

```
  fetch (cached, §7)                normalise                    score                emit
  ─────────────────────             ──────────────               ──────────           ──────
  Open-Meteo GFS-Wave 0.16°  ──┐
  NWS BOX/66,56 · 65,53 · 63,54 ─┼──▶  common hourly grid  ──▶  per spot ×  ──▶  forecast.json
  CO-OPS 8455083 (pred + hilo) ─┤     America/New_York          per timestep
  NDBC 44097 · 44085 (now only)─┘     120 steps                 (§5)
```

**Time grid.** Hourly (`PT1H`), horizon **120 steps = 5 days**, timezone **America/New_York**,
anchored to the next whole local hour at run time. Timestamps are emitted as offset-aware ISO-8601
(`2026-07-12T14:00:00-04:00`) so a DST transition is unambiguous in the artifact itself — a
consumer never has to know the rule. Internally: Python `zoneinfo`, never naive datetimes.

**Aligning the cadences** — each source arrives on a different clock:

- **Open-Meteo marine** — natively hourly and already in local tz. Direct index.
- **NWS gridpoint** — the killer detail: values are *run-length encoded* as ISO-8601 intervals
  (`"validTime": "2026-07-12T00:00:00+00:00/PT1H"`, but also `PT2H`, `P1DT9H`). They must be
  **expanded** across their duration onto the hourly grid, not zipped positionally. `windSpeed`,
  `windGust` and `windDirection` have *different* array lengths (86 / 97 / 72 verified) precisely
  because of this. Zipping them would silently misalign wind speed against wind direction — the
  single most damaging bug available in this codebase.
- **CO-OPS** — 6-min predictions, sampled at each grid hour; hi/lo used to compute the day's tide
  range for normalisation.
- **NDBC** — **observations, not forecast.** Used only for (a) the "now" row and (b) a bias check
  of the model's first hours. Never extrapolated into future timesteps.

**Horizon mismatch is real and must not be papered over.** NWS wind runs 7 days, GFS-Wave 10 days,
tides indefinitely — but they do not all start at the same hour, and NWS wind can end short. Any
grid hour lacking a required input yields `status: "no_data"`, **not** a low score (§5.6).

---

## 5. Scoring model

Per spot `s`, per timestep `t`. Everything below is deterministic and computed **once**, at build
time — the UI never recomputes a score.

### 5.1 Effective swell — where the contrast comes from

This is the heart of the model. **Swell direction is not a score multiplier.** It changes how much
energy physically *arrives* at each spot, which then feeds the size term. That is physically right,
and it is what makes spots disagree.

For each swell component `c` in {swell, wind-wave} with height `Hc`, period `Tc`, direction `Dc`:

```
Δ           = angular distance from Dc to the nearest edge of s.openWindow   (0 if inside)
κ(Tc)       = clamp(KAPPA0 + KAPPA1 * (Tc - 8), 4, 30)        // degrees of "bend"
exposure    = 1.0                       if Δ == 0
            = exp(-Δ / κ(Tc))           otherwise             // diffraction / refraction leakage

H_eff       = sqrt( Σc (Hc * exposure_c)^2 )                  // energy-preserving combine
T_eff       = Tc of the component with the largest (Hc * exposure_c)
D_eff       = Dc of that same component
```

One mechanism, three payoffs:

1. **It creates the spot contrast.** An SSW swell at 205° is inside Deep Hole's window
   (`195–215`) → exposure 1.0, and ~30° outside Narragansett's (`90–175`) → heavily attenuated.
   Same ocean, opposite spots.
2. **It encodes "long-period wraps."** `κ` grows with period, so a 14 s E swell leaks around the
   headland into Deep Hole while a 7 s E windswell does not. This is the *only* physically honest
   reconciliation of the conflict in §8.1, and it falls straight out of the formula rather than
   being special-cased.
3. **It lets Camp Cronin exist.** Empty window ⇒ every component goes through the diffraction term
   ⇒ it only ever scores on long-period S energy, and only modestly. "Mushy left through the gap",
   as a formula.

`KAPPA0 = 6`, `KAPPA1 = 1.5` are **calibration constants, not sourced** (§8.5).

### 5.2 Size — `Q_size`

Piecewise-linear on `H_eff` against the spot's `size` band:

```
0                                        H_eff < works
ramp 0→1 over [works, idealLo]
1                                        idealLo ≤ H_eff ≤ idealHi
ramp 1→0 over [idealHi, maxHandles]
0                                        H_eff > maxHandles        // blown out / closed out
```

### 5.3 Period — `Q_period`

```
Q_period = clamp( (T_eff - tMin) / (tGood - tMin), 0.25, 1.0 )
```

Groundswell-dependent spots (Monahan's, Matunuck-Trestles: "most of the surf here comes from
groundswells" [SOURCED]) carry `tMin=8, tGood=13`; mixed spots `tMin=5, tGood=10`. The 0.25 floor
means a short period *degrades* a spot without erasing it.

### 5.4 Wind — `Q_wind`

Wind direction is meteorological (the direction it blows **from**), matching every source.

```
θ         = angular difference( windDir, s.offshoreDir )
onshore   = max(0, -cos θ) * speed_kt          // straight onshore at θ=180
cross     = |sin θ|       * speed_kt

Q_wind    = 1.0                                            if speed_kt ≤ GLASS (4 kt)
          = clamp(1 - onshore/ONSHORE_KILL
                    - 0.5 * cross/CROSS_KILL, 0, 1)        otherwise
```

`GLASS = 4` (glassy is glassy regardless of direction), `ONSHORE_KILL = 12` kt,
`CROSS_KILL = 25` kt — **calibration constants** (§8.5).

This is the term that pays off §1. A 15 kt NE wind: at K38 (`offshoreDir` 45) θ=0 → `Q_wind` 1.0.
At Monahan's (`offshoreDir` 290) θ=115 → onshore component 6.3 kt, cross 13.6 kt → `Q_wind` ≈ 0.20.
Same hour, same wind, one spot firing and one spot junk.

### 5.5 Tide — `Q_tide`

Normalise the predicted height within **that day's** hi/lo range (from the `interval=hilo` call),
so the score is robust to spring/neap variation:

```
τ        = (height - dayLow) / (dayHigh - dayLow)          ∈ [0,1]
target   = { low: 0.15, "low-mid": 0.35, mid: 0.5, high: 0.85, any: τ }
g        = exp( -((τ - target)^2) / (2 * 0.25^2) )
Q_tide   = 1 - strength * (1 - g)   ... then + risingBonus if the tide is rising, clamped to 1
```

`strength` is how much the spot actually cares. Deep Hole is 0.85 ("low tide essential"
[SOURCED]); The Wall is 0.0 (no source says tide matters). So a spot with a weak tide preference
never gets zeroed by tide alone, and Deep Hole at high tide correctly falls off a cliff.

### 5.6 Combining — and `no data` ≠ `bad`

```
score = 100 · Q_size · Q_wind · (0.5 + 0.5·Q_period) · (0.6 + 0.4·Q_tide)
```

Size and wind are **hard gates** — either one can zero the spot, because either one genuinely does.
Period and tide are **modulators** — they scale a session between roughly half and full, but never
erase one. `Q_size` already carries the directional exposure from §5.1, so direction is not
double-counted.

**Missing data must never look like bad conditions.** This is enforced structurally, not by
convention:

```jsonc
{ "s": 62,   "status": "ok" }                                     // scored
{ "s": null, "status": "no_data", "missing": ["wind"] }           // NOT 0 — unknown
{ "s": 41,   "status": "partial", "missing": ["tide"] }           // scored w/ neutral tide, flagged
```

`score` is `number | null`. A `null` can never be compared, ranked, or colour-mapped as if it were
a low score — the type system makes the mistake unavailable. The UI **must** render `no_data` as a
visually distinct state (hatch / grey), never as the "bad" end of the colour ramp.

### 5.7 `limiting` — what's actually holding the spot back

Each scored timestep also emits the factor with the lowest `Q`:

```jsonc
"limiting": "wind"    // one of: size | wind | period | tide | none
```

This does double duty. It is the honest explanation a surfer wants ("it's not flat, it's just
onshore"), and it gives the UI a **non-colour channel** — a glyph per cell — which is how we
satisfy the colour-blindness / direct-sunlight constraint in §4 of the brief rather than relying
on hue alone.

---

## 6. Output schema — `forecast.json`

Versioned, and **directly renderable**: the UI does no scoring arithmetic. Shared time axis,
index-aligned arrays, so the payload stays small and a heatmap is a straight double loop.

```jsonc
{
  "schema": "gansett-juice/forecast@1",
  "generatedAt": "2026-07-12T14:03:11-04:00",
  "timezone": "America/New_York",
  "grid": { "interval": "PT1H", "steps": 120, "start": "2026-07-12T15:00:00-04:00" },

  "sources": [                                   // provenance + staleness, per run
    { "id": "gfswave016", "url": "…", "fetchedAt": "…", "status": "ok" },
    { "id": "nws-BOX-65-53", "url": "…", "fetchedAt": "…", "status": "ok" },
    { "id": "ndbc-44097", "url": "…", "fetchedAt": "…", "status": "stale", "note": "MM in SwH" }
  ],

  "times": ["2026-07-12T15:00:00-04:00", "…"],   // length = grid.steps

  "observed": {                                  // "now" only — from NDBC, never forecast
    "at": "2026-07-12T12:56:00-04:00",
    "buoy": "44097",
    "swell": { "h": 0.7, "t": 10.5, "d": 135 },
    "windWave": { "h": 1.6, "t": 7.1, "d": 180 }
  },

  "conditions": {                                // spot-independent, index-aligned to times[]
    "swell":    [{ "h": 2.1, "t": 11.0, "d": 165 }, "…"],
    "windWave": [{ "h": 0.8, "t": 5.2, "d": 200 }, "…"],
    "wind":     { "narragansett": [{ "spd": 9, "dir": 220, "gust": 14 }, "…"],
                  "point-judith": ["…"], "matunuck": ["…"] },
    "tide":     [{ "ft": 2.1, "stage": "rising" }, "…"]
  },

  "spots": [                                     // registry echoed, so the UI is self-contained
    { "id": "deep-hole", "name": "Deep Hole", "lat": 41.3725, "lon": -71.5351,
      "facing": 173, "bottom": "cobble reef", "confidence": "sourced" }
  ],

  "scores": {                                    // index-aligned to times[]
    "deep-hole": [
      { "s": 62, "status": "ok", "limiting": "wind",
        "hEff": 3.1, "tEff": 11.0, "dEff": 165,
        "q": { "size": 0.80, "wind": 0.90, "period": 0.70, "tide": 1.00 } },
      { "s": null, "status": "no_data", "missing": ["wind"] }
    ]
  },

  "summary": {                                   // precomputed rankings for the "best…" UI
    "bestNow":   [{ "spot": "deep-hole", "s": 62 }],
    "bestToday": [{ "spot": "k38", "s": 71, "at": "2026-07-12T18:00:00-04:00" }],
    "bestWeek":  [{ "spot": "monahans-dock", "s": 84, "at": "2026-07-15T07:00:00-04:00" }]
  }
}
```

---

## 7. Caching, rate limiting, citizenship

Cache to `.cache/` keyed by source + request params. TTLs match how often the upstream actually
changes — polling faster than the data updates is pure rudeness:

| Source | TTL | Why |
|---|---|---|
| NDBC | 20 min | buoy reports ~10–30 min |
| NWS gridpoint | 60 min | `updateTime` moves ~hourly |
| Open-Meteo marine | 3 h | GFS runs 4×/day |
| CO-OPS predictions | 24 h (keyed by date) | deterministic — they never change |
| Warm Winds (scrape) | 6 h | hand-written, ~daily |

- **One request per source per run**; a run is rate-limited to at most 1 per 10 min.
- Conditional GET (`If-Modified-Since`) where the origin supports it.
- Identifying `User-Agent` on every request (mandatory for NWS; courteous everywhere else).
- Open-Meteo free tier: <10k calls/day, 600/min. A run makes ~5 calls. We are nowhere near it —
  but the tier is **non-commercial**, which is a licence constraint on the *project*, not a
  technical one (§8.6).
- Warm Winds: fetched at most 6-hourly, used **only** to calibrate scores against a human's call.
  Their prose is copyrighted — we **link out**, we do not republish.

---

## 8. Open questions

These are the things research did not settle. They are listed rather than guessed at.

1. ~~**The E-swell conflict at Deep Hole / Trestles.**~~ **RESOLVED 2026-07-12 — the geometry was
   right and the prose was loose.** Surfline's spot-guide *text* says Trestles and Deep Hole
   "typically favor E swells". Its own **machine-readable** field for the same spot
   (`services.surfline.com/kbyg/spots/reports?spotId=5842041f4e65fad6a7708e40`) gives
   `best.swellDirection.value = ["ESE","SE","SSE","S"]` = **101–191°, which excludes E.**
   Two further confirmations: (a) Surfline applies the identical "E swell" phrase to Misquamicut,
   ~25 km further inside the shadow, where a literal 90° ray is geometrically impossible — so the
   phrase is a regional label, not a bearing; (b) Surfline reports Matunuck's swell direction from
   an `offshoreLocation` node ~41 km out to sea, *outside* the Point Judith shadow — so their
   "E swell" is a **deepwater** direction and was never a claim about energy arriving at the reef.
   The raycast and the guides were answering different questions.
   **What changed in the model:** `openWindow` for the Matunuck reefs is `[[135,183],[197,215]]`
   (not the naive `[[90,180]]`). 90–100° is blocked — by the shoreline you are standing on, which
   runs ESE toward the point. 105–135° is a **taper**, reached only by diffraction, and §5.1's
   exposure formula produces that taper for free, gated on period. Green Hill narrows to
   `[[150,167],[182,215]]` — the SSE sliver between the headland shadow and the Block Island
   shadow, which independently matches surf-forecast's "ideal SSE" for that spot.
   **The residue:** the *period gate* on that taper is still **our inference**. No source anywhere
   states a period threshold for these reefs (searched: Surfline, Warm Winds, WannaSurf, Stormrider,
   surf-forecast, Ocean/Beach SAMP, CRMC, Google Scholar for RI nearshore directional spectra —
   nothing). Timesteps scoring through the taper carry
   `caution: "diffraction-taper-inferred"` so the UI can hedge.
2. ~~**"The Wall"**~~ — **dropped from the registry** on the owner's call. No surf source fetched
   uses the name. 10 spots remain.
3. **Tide conflicts.** Narragansett: Surfline "low-mid" vs surfwithoutacar "mid-high". Monahan's:
   Surfline "lower tides are the real deal" vs surf-forecast "not affected by the tide". We took
   Surfline (human-written) over surf-forecast (template-generated) and set a low `strength` to
   limit the damage of being wrong.
4. **Unconfirmed size ranges:** Green Hill, The Wall, K38, Camp Cronin. Values in the registry are
   placeholders marked `confidence: partial|unconfirmed` and should be treated as such.
5. **All calibration constants are unsourced**: `KAPPA0/KAPPA1` (diffraction), `GLASS`,
   `ONSHORE_KILL`, `CROSS_KILL`, the tide gaussian width, and the 0.5/0.6 modulator floors. They
   are physically motivated but numerically invented. **This is what Warm Winds' human report is
   for** — the calibration loop is: score a day, read their call, adjust. Until that loop has run,
   treat absolute scores as ordinal (spot A > spot B) rather than cardinal ("62 out of 100").
6. **Licence.** Open-Meteo's free tier is **non-commercial**. If gansett-juice is ever anything
   other than a personal project, the wave forecast source has to change or be paid for. Flagging
   now, not later.
7. **Camp Cronin** as a distinct scoreable spot rests on one LocalWiki line and a Surfline pin.
   If it never scores above ~30, that may be correct — or it may mean the diffraction constants are
   too harsh. Cannot distinguish these without ground truth.

---

## 9. Toolchain — the brief's assumption does not hold

The brief specifies the **obscura browser** for scraping and asks that the real invocation be
confirmed in this container. It was, and:

```
$ command -v obscura      → not found
$ command -v node         → not found
$ find / -iname '*obscura*'  → (nothing)
```

**`obscura` is not installed here, and neither is Node.** What *is* available and verified working:

- `/usr/bin/chromium` — headless, renders JS apps. Verified against hopewaves.app: 226 KB DOM with
  the JS executed.
- Python 3.13.5 (stdlib only — `urllib`, `json`, `zoneinfo`; no `requests`, no `bs4`).
- `curl`, `jq`.

The real scrape invocation, to be written into the skill:

```sh
chromium --headless --no-sandbox --disable-gpu \
         --virtual-time-budget=15000 --dump-dom "$URL"
```

This suits the repo's zero-build character: the fetch/score pipeline is **Python 3 stdlib, no
dependencies**, and chromium is needed only for the one JS-rendered scrape target. The two sources
we actually depend on are plain JSON over HTTPS and need no browser at all.

**One more environment constraint, verified:** `api.open-meteo.com` (IPv4, `188.40.99.226`)
TCP-times-out from this container, while `marine-api.open-meteo.com` (IPv6) works fine. So wind
**cannot** come from Open-Meteo here even if we wanted it to. It comes from NWS — which is the
authoritative local source anyway, so this costs us nothing. But it is a real constraint and the
code must not be written against a host that cannot be reached.

---

## 10. Historical replay mode

You cannot judge a scoring model — or a UI — on a flat week, and southern RI in July routinely is
one (the live run at build time was 0.4–1.4 ft at 4–9 s, and every spot correctly scored 0).

`--from/--to` replays a past window through the **identical** normalise + score path; only the
fetchers change:

```sh
python3 .claude/skills/surf-forecast/forecast.py --from 2023-09-14 --to 2023-09-18 \
        --out data/forecast-example-swell.json          # Hurricane Lee
```

Two honest caveats, stamped into the artifact itself under a `historical` key so nobody mistakes
the file for a forecast:

- **`ncep_gfswave016` has no archive coverage** (verified: all nulls for past dates). The replay
  therefore uses `best_match` = **MFWAM**, a different and higher-reading wave model.
- Wind comes from **ERA5** (~0.25°), coarser than the NWS grid — so the three sub-regions may snap
  to one cell and the per-region wind contrast is weaker than in a live run.

Do not compare absolute scores between a replay and a live forecast.

## 11. Deviations from the brief

- **`obscura` is not installed in this container** (nor is Node). Verified: `command -v obscura` →
  not found; `find / -iname '*obscura*'` → nothing. Substituted `/usr/bin/chromium` headless — see
  §9 for the real invocation. In the end the pipeline needs no browser at all: both sources we
  depend on are plain JSON over HTTPS.
- **surfcaptain.com is excluded on licence, not on technical grounds.** Its `/terms` forbids
  derivative works and reuse of any content.
- **"The Wall" was dropped** and East Matunuck was never included. 10 spots.
- The brief asked for a stop after Step 1 for review; that happened, and the E-swell decision
  taken at review was subsequently **overturned by the directed research it also asked for** — see
  §8.1. The evidence won.
