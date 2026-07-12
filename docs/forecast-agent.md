# Surf conditions + 5-day forecast agent — architecture spec

Status: **shipped**. Skill at `.claude/skills/surf-forecast/`, UI options in `demos/`, forecast tab
live in `index.html` (`#forecast`). Rebuilt daily at 5am RI time by
`.github/workflows/forecast.yml`.
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

| id | lat, lon | facing | openWindow | taper (inferred) | offshoreDir | size works/ideal/max (ft) | period tMin/tGood | tide (pref, strength) | windRegion | confidence |
|---|---|---|---|---|---|---|---|---|---|---|
| `narragansett-town-beach` | 41.4342, -71.4531 | 130 | 90–175 | — | 315 | 1.5 / 2.5–5 / 7 | 6 / 10 | mid, 0.3 | narragansett | sourced |
| `monahans-dock` | 41.4227, -71.4541 | 70 | 85–165 | — | 290 | 3 / 4–12 / 18 | 8 / 13 | low, 0.4 | narragansett | sourced |
| `point-judith-east` | 41.3626, -71.4801 | 105 | 70–160 | — | 305 | 3 / 4–8 / 16 | 5 / 10 | mid, 0.5 | point-judith | sourced |
| `point-judith-south` | 41.3615, -71.4805 | 195 | 160–190; 210–220 | — | 45 | 3 / 4–8 / 16 | 5 / 10 | mid, 0.5 | point-judith | sourced |
| `k38` | 41.367, -71.4947 | 235 | 175–190 | — | 45 | 2 / 3–6 / 8 | 5 / 10 | low, 0.5 | point-judith | partial |
| `camp-cronin` | 41.3655, -71.498 | 240 | *(none)* | — | 45 | 2 / 3–5 / 6 | 9 / 14 | low, 0.5 | point-judith | unconfirmed |
| `deep-hole` | 41.3725, -71.5351 | 173 | 135–183; 197–215 | 100–135 | 15 | 1 / 3–7 / 10 | 5 / 10 | low, 0.85 | matunuck | sourced |
| `matunuck-trestles` | 41.3733, -71.5388 | 178 | 135–183; 197–215 | 100–135 | 22 | 1 / 3–7 / 10 | 8 / 13 | low, 0.6 | matunuck | sourced |
| `matunuck-the-point` | 41.374, -71.541 | 184 | 135–183; 197–215 | 100–135 | 22 | 2 / 3–8 / 12 | 5 / 10 | low-mid, 0.5 | matunuck | sourced |
| `green-hill` | 41.3643, -71.6011 | 173 | 150–167; 182–215 | 100–115 | 22 | 2 / 3–6 / 8 | 5 / 10 | low, 0.5 | matunuck | partial |

**Generated from `spots.json` — that file is the source of truth, this table mirrors it.**
