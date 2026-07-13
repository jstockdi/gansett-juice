/* ==================================================================== *
 *  <surf-forecast> — the forecast tab for index.html                    *
 *                                                                       *
 *  Two views over data/forecast.json, switched by width:                *
 *    phone  -> the verdict + what's actually happening + ranked spots   *
 *    wide   -> spots x time heatmap                                     *
 *                                                                       *
 *  DESIGN RULE, learned the hard way: most days here are flat, so the   *
 *  flat day is the DEFAULT case, not the degraded one. A UI that leads  *
 *  with "BEST TODAY: Deep Hole, 2/100" on a flat week is lying with     *
 *  emphasis. When nothing clears summary.worthItThreshold we say so in  *
 *  plain words and show when the next real window is -- or that there   *
 *  isn't one.                                                           *
 *                                                                       *
 *  Scores come precomputed from the agent. Nothing here recomputes one. *
 *  score is number | null; null means NO DATA and is never rendered as  *
 *  the bad end of the ramp. Quality is encoded twice -- colour AND      *
 *  length/height -- so it survives colour-blindness and direct sun.     *
 * ==================================================================== */

const FC_RAMP = ['#184f95', '#256abf', '#3987e5', '#6da7ec', '#9ec5f4', '#cde2fb'];
const fcRamp = s => FC_RAMP[Math.min(5, Math.floor(s / 100 * 6))];
const FC_LIMIT = {
  size: 'too small', wind: 'wind is wrong', period: 'period too short',
  tide: 'tide is wrong', none: 'nothing holding it back',
};
/* the spot's wind region now comes from the data, not a hardcoded id list here.
   The old list named spots by id and rotted the moment the registry changed --
   a removed spot would have silently mapped to the wrong NWS cell. */
let FC_SPOTS = {};
const FC_REGION = id => (FC_SPOTS[id] && FC_SPOTS[id].windRegion) || 'point-judith';
const RI = 'America/New_York';     // the forecast is FOR Rhode Island -- always show RI
                                   // time, not the viewer's. Default toLocaleString uses
                                   // the viewer's tz and silently reported a 7pm peak as
                                   // "11 PM" from a UTC box.
const fcWhen = t => new Date(t).toLocaleString('en-US',
  { weekday: 'short', hour: 'numeric', hour12: true, timeZone: RI });
const fcDay = t => new Date(t).toLocaleDateString('en-US', { weekday: 'long', timeZone: RI });
const fcHour = t => new Date(t).toLocaleTimeString('en-US',
  { hour: 'numeric', hour12: true, timeZone: RI });
const DEG = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW',
  'W', 'WNW', 'NW', 'NNW'];
const compass = d => DEG[Math.round(d / 22.5) % 16];

class SurfForecast extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._scope = 'bestToday';
    this._open = null;
    this._showFlat = false;
    this.shadowRoot.innerHTML = `
      <style>
        :host { position: absolute; inset: 0; display: block; overflow-y: auto;
          -webkit-overflow-scrolling: touch; background: #111110; color: #fff;
          padding: max(16px, env(safe-area-inset-top)) 16px 140px;   /* clears the dock */
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        :host([hidden]) { display: none; }
        .wrap { max-width: 640px; margin: 0 auto; }

        /* ---- the verdict: the one thing you came here for ---- */
        .verdict { background: #1a1a19; border: 1px solid #34342f; border-radius: 12px;
          padding: 16px; margin-bottom: 10px; }
        .verdict.go { border-color: #2c5f8a; background: linear-gradient(#1a2028, #1a1a19); }
        .kicker { font-size: 10.5px; letter-spacing: .09em; text-transform: uppercase;
          color: #86857c; }
        .call { font-size: 25px; font-weight: 650; letter-spacing: -.02em; margin: 4px 0 3px;
          line-height: 1.15; }
        .call.dim { color: #c3c2b7; font-weight: 500; font-size: 21px; }
        .detail { font-size: 13px; color: #c3c2b7; line-height: 1.5; }
        .detail b { color: #fff; font-weight: 600; }
        .next { font-size: 12.5px; color: #86857c; margin-top: 9px; padding-top: 9px;
          border-top: 1px solid #2b2b28; line-height: 1.5; }
        .next b { color: #9ec5f4; font-weight: 600; }
        /* attribution sits WITH the claim, not in the small print */
        .trop { font-size: 12px; color: #86857c; margin-top: 9px; padding-top: 9px;
          border-top: 1px solid #2b2b28; line-height: 1.5; }
        .trop.hot { color: #fab219; }
        .trop.hot b { color: #fff; }
        .credit { font-size: 12px; color: #86857c; margin-top: 9px; padding-top: 9px;
          border-top: 1px solid #2b2b28; line-height: 1.5; }
        .credit a { color: #c3c2b7; font-weight: 600; text-decoration: none;
          border-bottom: 1px solid #4a4a44; }
        .credit a:hover { color: #fff; }
        .credit b { color: #c3c2b7; font-weight: 600; }

        .why-box { background: #1a1a19; border: 1px solid #34342f; border-radius: 12px;
          margin-bottom: 10px; font-size: 12.5px; }
        .why-box summary { padding: 11px 14px; cursor: pointer; color: #c3c2b7;
          font-weight: 600; list-style: none; -webkit-tap-highlight-color: transparent; }
        .why-box summary::-webkit-details-marker { display: none; }
        .why-box summary::before { content: '▸ '; color: #86857c; }
        .why-box[open] summary::before { content: '▾ '; }
        .why-box ul { margin: 0; padding: 0 14px 12px 30px; color: #86857c; line-height: 1.55; }
        .why-box li { margin-bottom: 6px; }
        .why-box li:last-child { margin-bottom: 0; }

        .ecurve { margin-bottom: 10px; }
        .ec-hd { font-size: 11px; color: #86857c; margin-bottom: 4px; }
        .ec-hd b { color: #c3c2b7; }
        .ecurve svg { display: block; width: 100%; height: 46px; background: #15161a;
          border: 1px solid #2b2b28; border-radius: 6px; }

        .spec-body { padding: 0 14px 12px; }
        wave-spectrum { display: block; margin-bottom: 8px; }
        .spec-key { font-size: 11.5px; color: #86857c; line-height: 1.55; }
        .spec-key b { color: #c3c2b7; }
        .spec-num { display: flex; gap: 14px; margin-bottom: 6px; }
        .spec-num span { font-size: 11px; color: #86857c; }
        .spec-num b { color: #fff; font-size: 14px; display: block; }
        .obs { color: #6a695f; }

        .rose { margin-bottom: 10px; }
        .rose svg { display: block; width: 100%; max-width: 260px; margin: 0 auto 6px; height: auto; }
        .rose-key { font-size: 11px; color: #86857c; line-height: 1.5; }
        .rose-key > div { display: flex; gap: 7px; align-items: baseline; margin-bottom: 3px; }
        .rose-key .d { width: 9px; height: 9px; border-radius: 2px; flex: 0 0 auto;
          position: relative; top: 1px; }
        .rose-key .sw { background: #3987e5; }
        .rose-key .win { background: #9ec5f4; }
        .rose-key .wd { background: #c98500; }
        .rose-key .swl { background: #fff; }
        .rose-key .amb { color: #fab219; font-style: normal; }
        .rose-key b { color: #c3c2b7; }
        .rose-call { display: block !important; margin-top: 7px; padding-top: 7px;
          border-top: 1px solid #2b2b28; color: #c3c2b7; font-size: 11.5px; }
        .rose-call b { color: #fff; }

        /* ---- what's actually happening right now ---- */
        .now { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1px;
          background: #34342f; border: 1px solid #34342f; border-radius: 10px;
          overflow: hidden; margin-bottom: 12px; }
        .now div { background: #1a1a19; padding: 9px 8px; }
        .now .k { font-size: 9.5px; letter-spacing: .07em; text-transform: uppercase;
          color: #86857c; }
        .now .v { font-size: 14px; font-weight: 600; margin-top: 2px; white-space: nowrap; }
        .now .u { font-size: 11px; color: #86857c; font-weight: 400; }
        /* 5 cells in a 2-col grid leaves an orphan -- let the last one span */
        .now > div:last-child:nth-child(odd) { grid-column: 1 / -1; }
        @media (min-width: 460px) {
          .now { grid-template-columns: repeat(5, 1fr); }
          .now > div:last-child:nth-child(odd) { grid-column: auto; }
        }

        .tabs { display: flex; gap: 6px; margin-bottom: 10px; }
        .tabs button { flex: 1; background: #1a1a19; color: #c3c2b7; border: 1px solid #34342f;
          border-radius: 8px; padding: 8px 6px; font: inherit; font-size: 12.5px;
          font-weight: 600; cursor: pointer; -webkit-tap-highlight-color: transparent; }
        .tabs button[aria-selected="true"] { background: #2e2e2a; color: #fff; border-color: #55554d; }

        ol { list-style: none; margin: 0; padding: 0; background: #1a1a19;
          border: 1px solid #34342f; border-radius: 12px; overflow: hidden; }
        li { border-bottom: 1px solid #34342f; }
        li:last-child { border-bottom: 0; }
        .row { display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: center;
          padding: 11px 13px; cursor: pointer; -webkit-tap-highlight-color: transparent; }
        .nm { font-size: 13.5px; font-weight: 600; }
        .conf { font-size: 10px; color: #86857c; font-weight: 400; margin-left: 5px; }
        /* CHANNEL 2 -- length. display:block matters; a bare span collapses it. */
        .meter { display: block; height: 5px; border-radius: 3px; background: #2b2b28;
          margin-top: 6px; overflow: hidden; max-width: 240px; }
        .meter i { display: block; height: 100%; border-radius: 3px; }
        .why { display: block; font-size: 11.5px; color: #86857c; margin-top: 5px; }
        .sc { text-align: right; }
        .sc b { font-size: 19px; font-variant-numeric: tabular-nums; }
        .sc .at { display: block; font-size: 10.5px; color: #86857c; margin-top: 1px; }

        /* flat spots collapse behind one line -- ranking zeros is noise */
        .flatline { padding: 10px 13px; font-size: 12px; color: #86857c; cursor: pointer;
          background: #161615; -webkit-tap-highlight-color: transparent; }
        .flatline:hover { color: #c3c2b7; }

        .drill { padding: 0 13px 13px; }
        .drill.hidden { display: none; }
        .strip { display: flex; gap: 1px; align-items: flex-end; height: 40px; background: #22221f;
          border-radius: 6px; padding: 4px; margin-bottom: 8px; }
        .strip i { flex: 1; border-radius: 1.5px; min-height: 1px; }
        .strip i.night { background: #202020; }
        .strip i.nd { background: repeating-linear-gradient(45deg,#2b2b28 0 3px,transparent 3px 6px);
          height: 100% !important; border: 1px dashed #3d3d38; }
        .facts { display: grid; grid-template-columns: repeat(auto-fit,minmax(88px,1fr)); gap: 8px;
          font-size: 11.5px; }
        .fact { background: #22221f; border-radius: 7px; padding: 7px 9px; }
        .fact .k { color: #86857c; font-size: 10px; display: block; }
        .fact .v { font-weight: 600; }
        .cau { font-size: 11.5px; color: #fab219; margin-top: 8px; line-height: 1.45; }

        .foot { font-size: 11px; color: #6a695f; margin-top: 14px; line-height: 1.55; }
        .foot a { color: #86857c; }
        .foot b { color: #86857c; }

        .grid { display: none; }
        @media (min-width: 900px) {
          .wrap { max-width: 1180px; }
          .ranked { display: none; }
          .grid { display: block; }
        }
        .deck { display: grid; grid-template-columns: minmax(0,1fr) 274px; gap: 14px;
          align-items: start; }
        .rosepane { background: #1a1a19; border: 1px solid #34342f; border-radius: 10px;
          padding: 12px; }
        .rosepane-hd { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
        .rosepane-hd span { display: block; font-size: 10.5px; color: #86857c;
          font-weight: 400; margin-top: 1px; }
        .spotcell[data-rose] { cursor: pointer; }
        .spotcell[data-rose]:hover { color: #fff; }
        .spotcell.on { color: #fff; box-shadow: inset 2px 0 0 #9ec5f4; }
        .scroll { overflow-x: auto; border: 1px solid #34342f; border-radius: 10px; background: #1a1a19; }
        table { border-collapse: collapse; }
        th, td { padding: 0; }
        .spotcell { position: sticky; left: 0; z-index: 2; background: #1a1a19;
          border-right: 1px solid #34342f; text-align: left; padding: 0 10px; font-size: 12px;
          font-weight: 500; white-space: nowrap; min-width: 150px; color: #c3c2b7; }
        .dayhdr { font-size: 11px; font-weight: 600; color: #86857c; text-align: left;
          padding: 7px 0 5px 4px; white-space: nowrap; border-bottom: 1px solid #34342f; }
        .daysep { border-left: 1px solid #34342f; }
        .cell { width: 13px; height: 26px; position: relative; vertical-align: bottom; }
        .cell.night { background: #151514; }          /* dark hours, greyed -- never recommended */
        .fill { position: absolute; left: 1px; right: 1px; bottom: 1px; border-radius: 2px 2px 1px 1px; }
        .nodata { position: absolute; inset: 1px; border-radius: 2px; border: 1px dashed #3d3d38;
          background: repeating-linear-gradient(45deg,#2b2b28 0 3px,transparent 3px 6px); }
        .cell.caution::after { content: ''; position: absolute; top: 2px; right: 2px; width: 3px;
          height: 3px; border-radius: 50%; background: #fab219; }
        .cell:hover { outline: 1px solid #fff; outline-offset: -1px; }
        .peak { outline: 1.5px solid #fff; outline-offset: -1px; }
        .key { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; margin-top: 12px;
          font-size: 11.5px; color: #86857c; }
        .ramp { display: flex; align-items: flex-end; gap: 2px; }
        .ramp i { display: block; width: 13px; border-radius: 2px 2px 1px 1px; }
        .sw { display: inline-flex; align-items: center; gap: 6px; }
        .hatch { width: 14px; height: 14px; border-radius: 3px; border: 1px dashed #3d3d38;
          background: repeating-linear-gradient(45deg,#2b2b28 0 3px,transparent 3px 6px); }
        .nightsw { width: 14px; height: 14px; border-radius: 3px; background: #151514;
          border: 1px solid #2b2b28; }
        .dot { width: 6px; height: 6px; border-radius: 50%; background: #fab219; }
        .err { font-size: 13px; color: #c3c2b7; background: #1a1a19; border: 1px solid #34342f;
          border-radius: 10px; padding: 14px; line-height: 1.5; }
      </style>
      <div class="wrap" id="wrap"><div class="err">Loading forecast…</div></div>`;
  }

  connectedCallback() { if (!this._d) this.load(); }

  async load() {
    try {
      const r = await fetch('./data/forecast.json', { cache: 'no-cache' });
      if (!r.ok) throw new Error(r.status);
      this._d = await r.json();
      FC_SPOTS = Object.fromEntries(this._d.spots.map(s => [s.id, s]));
      this.render();
    } catch {
      this.shadowRoot.getElementById('wrap').innerHTML =
        `<div class="err">Couldn't load the forecast.<br>
         <span style="color:#86857c">It's rebuilt each morning at 5am — if this persists,
         the build may have failed.</span></div>`;
    }
  }

  render() {
    const d = this._d;
    this.shadowRoot.getElementById('wrap').innerHTML =
      this._verdict() + this._spectrum() + this._now() +
      `<div class="ranked">${this._ranked()}</div>
       <div class="grid">${this._heatmap()}</div>` + this._foot();

    // <wave-spectrum> takes its data as a property, not an attribute -- the spectrum is
    // an object, and stringifying it into HTML just to parse it back out is silly.
    const ws = this.shadowRoot.querySelector('wave-spectrum');
    if (ws) ws.data = d.spectrum;

    this._bind();
  }

  /* ---------------------------------------------------------- the verdict */
  _verdict() {
    const d = this._d, sum = d.summary;
    const bar = sum.worthItThreshold ?? 35;
    const today = sum.bestToday[0];
    const spot = id => d.spots.find(s => s.id === id).name;

    // GOOD DAY — there is genuinely something to recommend
    if (today && today.s >= bar) {
      const c = d.scores[today.spot][d.times.indexOf(today.at)];
      const w = d.conditions.wind[FC_REGION(today.spot)][d.times.indexOf(today.at)];
      return `<div class="verdict go">
        <div class="kicker">Go surf</div>
        <div class="call">${spot(today.spot)}</div>
        <div class="detail"><b>${fcWhen(today.at)}</b> — ${c.hEff} ft at ${c.tEff}s
          from ${compass(c.dEff)}${w ? `, wind ${Math.round(w.spd)} kt ${compass(w.dir)}` : ''}.
          ${c.limiting === 'none' ? 'Nothing holding it back.' : 'Limited by ' + FC_LIMIT[c.limiting] + '.'}
        </div>
        ${this._credit()}
      </div>`;
    }

    // SMALL / FLAT. Do not dress up the least-bad hour as a recommendation -- but do
    // not overclaim either. "Not worth it" is a shortboarder's verdict; 1.2ft clean is
    // a longboard day. Say what it IS and let the reader decide.
    const nw = sum.nextWindow;
    const sw = d.conditions.swell[0];
    const big = (sum.rationale || []).length ? this._biggest() : 0;
    const loggable = big >= 0.8;
    return `<div class="verdict">
      <div class="kicker">Right now</div>
      <div class="call dim">${loggable ? 'Small — longboard only.' : 'Flat.'}</div>
      <div class="detail">${sw
        ? `<b>${sw.h} ft at ${sw.t}s</b> from ${compass(sw.d)} — ${
            sw.t < 7 ? 'that’s windchop, not swell' : 'too small to break'}.`
        : 'No swell data.'}</div>
      <div class="next">${nw
        ? `Next window: <b>${spot(nw.spot)}, ${fcDay(nw.at)} ${fcHour(nw.at)}</b> (${nw.s}/100).`
        : 'No proper swell in the next 5 days.'}</div>
      ${this._tropics()}
      ${this._credit()}
    </div>` + this._why();
  }

  /* NHC's tropical outlook runs 7 days; our wave model only sees 5. Hurricane
     groundswell is what makes surf here good, so a storm forming beyond our horizon
     is the most important thing we CAN'T otherwise see.

     The quiet case matters just as much: when NHC says no formation is expected, a
     flat forecast stops being a statement about our window and becomes a statement
     about the ocean. "Nothing in my 5 days" and "nothing is coming" are different
     claims, and only one of them is worth acting on. */
  _tropics() {
    const t = this._d.tropics;
    if (!t) return '';
    const live = (t.activeStorms || []).length;
    if (live) {
      const names = t.activeStorms.map(s => `${s.class || ''} ${s.name}`.trim()).join(', ');
      return `<div class="trop hot">🌀 <b>${names}</b> active in the Atlantic. Hurricane
        groundswell is what turns this coast on — watch the next few days, it is beyond
        the wave model's 5-day horizon.</div>`;
    }
    if (t.formationExpected) {
      return `<div class="trop hot">🌀 NHC expects tropical formation in the Atlantic.
        Groundswell may follow — beyond the 5-day model horizon.</div>`;
    }
    return `<div class="trop">NHC: no tropical formation expected for 7 days — so nothing
      is building behind this, either.</div>`;
  }

  _biggest() {
    const d = this._d;
    let m = 0;
    for (const row of Object.values(d.scores))
      row.forEach((c, i) => { if (c.s !== null && d.daylight[i] && c.hEff > m) m = c.hEff; });
    return m;
  }

  /* ==================================================================== *
   *  The compass rose — the whole decision in one picture.                *
   *                                                                       *
   *  Three vectors, and they are the three that decide it:                *
   *    1. WHERE THE ENERGY IS COMING FROM — petals, sized by swell ENERGY *
   *       (kW/m, ~ H^2*T). Not height: height cannot tell 3ft@6s from     *
   *       3ft@14s, which differ 2.3x in punch.                            *
   *    2. WHETHER THIS SPOT CAN SEE IT — the shaded window arc. The point *
   *       and Block Island block huge bearings, so here direction is a    *
   *       GATE, not a refinement.                                         *
   *    3. WHETHER THE WIND WILL WRECK IT — the wind arrow, plus a dashed  *
   *       tick at the bearing that is offshore for THIS spot.             *
   *                                                                       *
   *  No general surf forecast can draw this — it needs per-spot window    *
   *  geometry. hopewaves shows the rose and leaves the spot reasoning to  *
   *  you; this closes that loop.                                          *
   *                                                                       *
   *  Convention: swell and wind bearings are both meteorological (the     *
   *  direction it comes FROM), so the arrows point INWARD — the way the   *
   *  water and the air are actually travelling.                           *
   * ==================================================================== */
  _rose(spotId) {
    const d = this._d;
    const sp = d.spots.find(s => s.id === spotId);
    if (!sp || !sp.openWindow) return '';

    const R = 92, CX = 110, CY = 106;
    const pol = (deg, r) => [                    // bearing (0=N, clockwise) -> x,y
      CX + r * Math.sin(deg * Math.PI / 180),
      CY - r * Math.cos(deg * Math.PI / 180),
    ];
    const arc = (a0, a1, r) => {
      const [x0, y0] = pol(a0, r), [x1, y1] = pol(a1, r);
      return `M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 ${
        (a1 - a0 + 360) % 360 > 180 ? 1 : 0} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
    };
    const wedge = (a0, a1, r) => {
      const [x0, y0] = pol(a0, r), [x1, y1] = pol(a1, r);
      return `M ${CX} ${CY} L ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 0 1 ${
        x1.toFixed(1)} ${y1.toFixed(1)} Z`;
    };

    // 1. energy petals — next 24h of swell + windwave, binned by bearing, summed by ENERGY
    const bins = new Array(16).fill(0);
    for (let i = 0; i < Math.min(24, d.times.length); i++) {
      for (const key of ['swell', 'windWave']) {
        const c = d.conditions[key][i];
        if (c && c.e) bins[Math.round(c.d / 22.5) % 16] += c.e;
      }
    }
    const peak = Math.max(...bins, 0.0001);
    // sqrt: energy spans ~30x between a flat day and a hurricane, and a linear radius
    // would render every ordinary day as an invisible nub
    const petals = bins.map((e, k) => e <= 0 ? '' : `<path d="${
      wedge(k * 22.5 - 9, k * 22.5 + 9, 14 + Math.sqrt(e / peak) * (R - 28))
    }" fill="#3987e5" opacity="${(0.35 + 0.5 * (e / peak)).toFixed(2)}"/>`).join('');

    // 2. what this spot can actually see
    const open = sp.openWindow.map(([a, b]) =>
      `<path d="${arc(a, b, R - 4)}" stroke="#9ec5f4" stroke-width="7" fill="none"/>`).join('');
    const taper = (sp.disputedWindow || []).map(([a, b]) =>
      `<path d="${arc(a, b, R - 4)}" stroke="#fab219" stroke-width="7" fill="none"
             stroke-dasharray="3 3" opacity=".85"/>`).join('');

    // 3. wind — and where offshore lies for THIS spot
    const w = d.conditions.wind[FC_REGION(spotId)][0];
    const sw = d.conditions.swell[0];
    let windArt = '', windTxt = '—';
    if (w) {
      // wind lives OUTSIDE the rim and points in; swell lives inside and points to the
      // centre. Same bearing no longer means same pixels.
      const [wx0, wy0] = pol(w.dir, R + 26), [wx1, wy1] = pol(w.dir, R + 7);
      const [lx, ly] = pol(w.dir, R + 36);
      const off = Math.abs(((w.dir - sp.offshoreDir + 180) % 360 + 360) % 360 - 180);
      const kind = off < 45 ? 'offshore' : off > 135 ? 'onshore' : 'cross-shore';
      const col = kind === 'offshore' ? '#199e70' : kind === 'onshore' ? '#e66767' : '#c98500';
      // the STATE goes in the label, not just the colour. Offshore/cross/onshore is the
      // single most consequential fact on this dial, and colour alone can't carry it.
      // arrowhead drawn explicitly: a marker with fill="context-stroke" silently paints
      // BLACK where that keyword isn't resolved, which is exactly how the head went dark
      const a = w.dir * Math.PI / 180;
      const ux = -Math.sin(a), uy = Math.cos(a);          // unit vector, pointing inward
      const HL = 9, HW = 5;                                // head length / half-width
      const [bx, by] = [wx1 - ux * HL, wy1 - uy * HL];     // base of the head
      const head = `${wx1.toFixed(1)},${wy1.toFixed(1)} ` +
                   `${(bx - uy * HW).toFixed(1)},${(by + ux * HW).toFixed(1)} ` +
                   `${(bx + uy * HW).toFixed(1)},${(by - ux * HW).toFixed(1)}`;
      windArt = `<line x1="${wx0.toFixed(1)}" y1="${wy0.toFixed(1)}" x2="${bx.toFixed(1)}"
          y2="${by.toFixed(1)}" stroke="${col}" stroke-width="3.5" stroke-linecap="round"/>
        <polygon points="${head}" fill="${col}"/>
        <text x="${lx.toFixed(1)}" y="${(ly - 4).toFixed(1)}" fill="${col}" font-size="9.5"
          font-weight="700" text-anchor="middle"
          stroke="#111110" stroke-width="2.5" paint-order="stroke">WIND ${Math.round(w.spd)}kt</text>
        <text x="${lx.toFixed(1)}" y="${(ly + 6).toFixed(1)}" fill="${col}" font-size="8.5"
          font-weight="600" text-anchor="middle"
          stroke="#111110" stroke-width="2.5" paint-order="stroke">${kind}</text>`;
      windTxt = `<b style="color:${col}">${Math.round(w.spd)} kt ${compass(w.dir)} · ${kind}</b>`;
    }



    const inArc = (arcs, deg) => (arcs || []).some(([a, b]) => deg >= a && deg <= b);
    const sees = sw && inArc(sp.openWindow, sw.d);
    const tapers = sw && inArc(sp.disputedWindow, sw.d);

    const ticks = ['N', 'E', 'S', 'W'].map((L, k) => {
      const [x, y] = pol(k * 90, R + 14);
      return `<text x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}" text-anchor="middle"
        font-size="10" fill="#86857c">${L}</text>`;
    }).join('');

    return `<div class="rose">
      <svg viewBox="-22 -24 264 268" role="img" aria-label="Compass: swell energy by direction, ${
        sp.name}'s open swell window, and the wind.">
        <circle cx="${CX}" cy="${CY}" r="${R}" fill="#15161a" stroke="#2b2b28"/>
        <circle cx="${CX}" cy="${CY}" r="${(R * 0.62).toFixed(0)}" fill="none" stroke="#242424"/>
        <circle cx="${CX}" cy="${CY}" r="${(R * 0.32).toFixed(0)}" fill="none" stroke="#242424"/>
        ${petals}${open}${taper}${windArt}${ticks}
      </svg>
      <div class="rose-key">
        <div><span class="d sw"></span><span>Swell <b>energy</b>, next 24h, by where it comes from</span></div>
        <div><span class="d win"></span><span>Sees ${sp.openWindow.map(([a, b]) => `${a}–${b}°`).join(', ')}${
          sp.disputedWindow ? ` <i class="amb">+ ${sp.disputedWindow.map(([a, b]) => `${a}–${b}°`)
            .join(', ')} bent round the point (inferred)</i>` : ''}</span></div>
        <div><span class="d wd"></span><span>The <b>WIND</b> arrow — the only arrow —
          shows where the wind blows from: ${windTxt}. When it sits opposite the petals it
          is blowing into the swell's face: offshore, and clean.</span></div>
        <div class="rose-call">${sw
          ? (sees
            ? `Swell from <b>${sw.d}° ${compass(sw.d)}</b> — <b>inside the window</b>. It reaches this spot.`
            : tapers
              ? `Swell from <b>${sw.d}° ${compass(sw.d)}</b> — only bends in around the headland. Heavily attenuated.`
              : `Swell from <b>${sw.d}° ${compass(sw.d)}</b> — <b>blocked</b>. It never gets here.`)
          : 'No swell data.'}</div>
      </div>
    </div>`;
  }

  /* The reasoning, not just the verdict. Precomputed by the agent (summary.rationale)
     so the UI stays dumb. It exists because "not worth it" is a judgement, and a
     judgement you can't inspect is just an assertion -- especially when it disagrees
     with the shop's human call while agreeing with every number in it. */
  _why() {
    const r = this._d.summary.rationale || [];
    if (!r.length) return '';
    return `<details class="why-box" ${this._why0 ? 'open' : ''}>
      <summary>Why we’re calling it this</summary>
      <ul>${r.map(x => `<li>${x}</li>`).join('')}</ul>
    </details>`;
  }

  /* Warm Winds is the local shop's HUMAN report and the ground truth this model is
     calibrated against. When we make a call -- "nothing worth surfing in the next 5
     days" -- we show what THEY called, right next to it, with their name on it and a
     link to their report. Attribution belongs where the claim is made, not in the
     small print at the bottom.

     We print the numbers we parsed and never their prose: the write-up is their
     copyrighted work. Do not "improve" this by pasting in their summary. */
  _credit() {
    const gt = this._d.groundTruth;
    if (!gt) return '';
    const o = gt.outlook || [];
    const short = s => s.split(',')[0].slice(0, 3);
    const band = o.length && o.every(x => x.sizeFt === o[0].sizeFt)
      ? `${o[0].sizeFt} ft through ${short(o[o.length - 1].day)}`
      : o.length ? o.map(x => `${short(x.day)} ${x.sizeFt} ft`).join(' · ')
      : `${gt.waveHeightFt} ft at ${gt.periodS}s`;
    return `<div class="credit">
      <a href="${gt.source}" target="_blank" rel="noopener">Warm Winds</a>${
        gt.reporter ? ` — ${gt.reporter}` : ''} calls it <b>${band}</b>.
      Their report is the human check on this forecast.
    </div>`;
  }

  /* ------------------------------------------- what's happening right now */
  _now() {
    const d = this._d;
    const sw = d.conditions.swell[0], td = d.conditions.tide[0];
    const w = d.conditions.wind['point-judith'][0];
    const gt = d.groundTruth;
    const cell = (k, v, u) => `<div><div class="k">${k}</div>
      <div class="v">${v}${u ? ` <span class="u">${u}</span>` : ''}</div></div>`;
    return `<div class="now">
      ${cell('Energy', sw ? sw.e : '–', 'kW/m')}
      ${cell('Swell', sw ? sw.h : '–', sw ? `ft ${sw.t}s ${compass(sw.d)}` : '')}
      ${cell('Wind', w ? Math.round(w.spd) : '–', w ? `kt ${compass(w.dir)}` : '')}
      ${cell('Tide', td ? td.ft : '–', td ? `ft ${td.stage}` : '')}
      ${cell('Water', gt && gt.waterTempF ? gt.waterTempF : '–',
              gt && gt.waterTempF ? '°F · Warm Winds' : '°F')}
    </div>`;
  }

  /* ------------------------------------------------------------ the spots */
  _ranked() {
    const d = this._d;
    const list = d.summary[this._scope] || [];
    const spot = id => d.spots.find(s => s.id === id);
    const idxOf = e => (e.at ? d.times.indexOf(e.at) : 0);

    const live = list.filter(e => e.s > 0);
    const flat = list.filter(e => e.s === 0);

    const rows = live.map(e => {
      const i = idxOf(e), c = d.scores[e.spot][i], sp = spot(e.spot);
      const open = this._open === e.spot;
      return `<li>
        <div class="row" data-spot="${e.spot}">
          <span>
            <span class="nm">${sp.name}${sp.confidence !== 'sourced'
              ? `<span class="conf">${sp.confidence}</span>` : ''}</span>
            <span class="meter"><i style="width:${e.s}%;background:${fcRamp(e.s)}"></i></span>
            <span class="why">${c.hEff} ft at ${c.tEff}s${
              c.limiting === 'none' ? '' : ' · ' + FC_LIMIT[c.limiting]}</span>
          </span>
          <span class="sc"><b>${e.s}</b>${e.at ? `<span class="at">${fcWhen(e.at)}</span>` : ''}</span>
        </div>
        <div class="drill ${open ? '' : 'hidden'}">${open ? this._drill(e.spot, i) : ''}</div>
      </li>`;
    }).join('');

    // ranking a column of zeros is noise -- collapse them behind one line
    const flatRow = flat.length ? `<li><div class="flatline" id="flip">
        ${this._showFlat ? '▾' : '▸'} ${flat.length} spot${flat.length > 1 ? 's' : ''} flat
        or blown out${this._showFlat ? ': ' + flat.map(e => spot(e.spot).name).join(', ') : ''}
      </div></li>` : '';

    return `<div class="tabs">
        <button data-k="bestNow" aria-selected="${this._scope === 'bestNow'}">Right now</button>
        <button data-k="bestToday" aria-selected="${this._scope === 'bestToday'}">Today</button>
        <button data-k="bestWeek" aria-selected="${this._scope === 'bestWeek'}">This week</button>
      </div>
      <ol>${rows || (flat.length ? '' : '<li><div class="row"><span class="why">No spots scored.</span></div></li>')}${flatRow}</ol>`;
  }

  _drill(id, i) {
    const d = this._d, row = d.scores[id], c = row[i];
    const w = d.conditions.wind[FC_REGION(id)][i];
    const td = d.conditions.tide[i];
    const strip = row.map((x, k) => {
      if (x.s === null) return '<i class="nd"></i>';
      if (!d.daylight[k]) return '<i class="night" style="height:100%"></i>';
      return `<i style="height:${Math.max(2, x.s)}%;background:${fcRamp(x.s)};${
        k === i ? 'outline:1.5px solid #fff;outline-offset:-1px' : ''}"></i>`;
    }).join('');
    const fact = (k, v) => `<div class="fact"><span class="k">${k}</span><span class="v">${v}</span></div>`;
    return `<div class="strip">${strip}</div>
      ${this._energyCurve(id)}
      ${this._rose(id)}
      <div class="facts">
        ${fact('Surf', `${c.hEff} ft @ ${c.tEff}s`)}
        ${fact('Energy', `${c.eEff ?? '–'} kW/m`)}
        ${fact('From', compass(c.dEff))}
        ${w ? fact('Wind', `${Math.round(w.spd)} kt ${compass(w.dir)}`) : ''}
        ${td ? fact('Tide', `${td.ft} ft ${td.stage}`) : ''}
        ${fact('Limited by', FC_LIMIT[c.limiting])}
      </div>
      ${c.caution ? `<div class="cau">⚠ Scoring on swell that only reaches this spot by bending
        around the headland — an inference, not a sourced rule.</div>` : ''}`;
  }

  /* --------------------------------------------------------- heatmap view */
  _heatmap() {
    const d = this._d, bar = d.summary.worthItThreshold ?? 35;
    const days = [];
    d.times.forEach((t, i) => {
      const day = t.slice(0, 10);
      if (!days.length || days[days.length - 1].day !== day) days.push({ day, start: i, n: 0 });
      days[days.length - 1].n++;
    });
    const fmt = day => new Date(day + 'T12:00:00')
      .toLocaleDateString('en-US', { weekday: 'short', month: 'numeric', day: 'numeric' });

    let h = '<div class="deck"><div class="scroll"><table><thead><tr><th class="spotcell"></th>';
    days.forEach(dd => { h += `<th class="dayhdr daysep" colspan="${dd.n}">${fmt(dd.day)}</th>`; });
    h += '</tr></thead><tbody>';

    for (const sp of d.spots) {
      const row = d.scores[sp.id];
      // only outline a peak that's actually worth something -- a bright ring around
      // a 2/100 shouts about the least meaningful cell on the screen
      const daylit = row.map((c, i) => (d.daylight[i] && c.s !== null) ? c.s : -1);
      const peak = Math.max(...daylit);
      let done = false;
      const sel = (this._roseSpot || this._defaultRoseSpot()) === sp.id;
      h += `<tr><th class="spotcell${sel ? ' on' : ''}" data-rose="${sp.id}"
              title="Show ${sp.name} on the compass">${sp.name}</th>`;
      row.forEach((c, i) => {
        const cls = [ 'cell' ];
        if (days.some(dd => dd.start === i)) cls.push('daysep');
        if (!d.daylight[i]) cls.push('night');
        if (c.s === null) { h += `<td class="${cls.join(' ')}"><span class="nodata"></span></td>`; return; }
        if (!done && peak >= bar && c.s === peak && d.daylight[i]) { cls.push('peak'); done = true; }
        if (c.caution) cls.push('caution');
        h += `<td class="${cls.join(' ')}"
                title="${sp.name} — ${c.s}/100 · ${c.hEff}ft @ ${c.tEff}s · ${FC_LIMIT[c.limiting]}">
                <span class="fill" style="height:${Math.max(2, Math.round(c.s / 100 * 24))}px;
                background:${fcRamp(c.s)}"></span></td>`;
      });
      h += '</tr>';
    }
    const rs = this._roseSpot || this._defaultRoseSpot();
    h += `</tbody></table></div>
      <aside class="rosepane">
        <div class="rosepane-hd">${d.spots.find(x => x.id === rs).name}
          <span>click any spot to change</span></div>
        ${this._rose(rs)}
      </aside></div>`;
    return h + '<div class="key">'
      + '<span class="sw">worse <span class="ramp">'
      + FC_RAMP.map((c, i) => `<i style="background:${c};height:${6 + i * 4}px"></i>`).join('')
      + '</span> better</span>'
      + '<span class="sw"><span class="nightsw"></span> dark — never recommended</span>'
      + '<span class="sw"><span class="hatch"></span> no data (never “bad”)</span>'
      + '<span class="sw"><span class="dot"></span> diffraction taper — inferred</span></div>';
  }

  _defaultRoseSpot() {
    const b = this._d.summary.bestToday;
    return b && b.length ? b[0].spot : this._d.spots[0].id;
  }

  /* Energy arriving AT THIS SPOT over the 5 days. This is the chart the app exists to
     draw: the same ocean delivers a DIFFERENT energy curve to each spot, because the
     headland gates each one differently. A height-over-time chart would flatten that
     away -- and would also hide the 3ft@6s vs 3ft@14s distinction entirely. */
  _energyCurve(spotId) {
    const d = this._d, row = d.scores[spotId];
    const W = 300, H = 46, PAD = 3;
    const vals = row.map(c => (c.s === null ? null : (c.eEff ?? 0)));
    const max = Math.max(...vals.filter(v => v !== null), 0.01);
    const sx = W / (vals.length - 1);
    const y = v => H - PAD - (v / max) * (H - PAD * 2);

    let line = '', pen = false;
    vals.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      line += (pen ? 'L' : 'M') + (i * sx).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
      pen = true;
    });
    const night = [];
    for (let i = 0; i < vals.length; i++) {
      if (d.daylight[i]) continue;
      let j = i; while (j < vals.length && !d.daylight[j]) j++;
      night.push(`<rect x="${(i * sx).toFixed(1)}" y="0" width="${((j - i) * sx).toFixed(1)}"
        height="${H}" fill="#000" opacity=".3"/>`);
      i = j;
    }
    const pk = Math.max(...vals.filter(v => v !== null), 0);
    return `<div class="ecurve">
      <div class="ec-hd">Energy reaching this spot · peak <b>${pk.toFixed(2)} kW/m</b> · 5 days</div>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        ${night.join('')}
        <path d="${line.trim()} L ${W} ${H} L 0 ${H} Z" fill="#3987e5" opacity=".18"/>
        <path d="${line.trim()}" fill="none" stroke="#6da7ec" stroke-width="1.6"
              stroke-linejoin="round"/>
      </svg>
    </div>`;
  }

  /* The buoy's raw energy spectrum. OBSERVED, not forecast -- GFS-Wave hands us
     partitions, not a spectrum, so there is no honest forecast version of this chart
     and we do not fake one by drawing a curve through two points.

     It earns its place by showing the one thing height/period/direction cannot:
     whether the energy is ORGANISED. One tall narrow peak = a clean groundswell.
     Two peaks = crossed swells, a confused lineup. A low broad hump at 4-6s = slop.
     All three read identically as "3 ft @ 9 s". */
  _spectrum() {
    const sp = this._d.spectrum;
    if (!sp || !sp.bins || !sp.bins.length) return '';

    const max = Math.max(...sp.bins.map(b => b[1]), 0.0001);
    const org = sp.organization;
    const read = org >= 60 ? 'one clean, organised swell'
      : org >= 40 ? 'mostly one swell, with some clutter'
      : 'energy smeared across periods — crossed swells or windblown slop';

    // the chart is <wave-spectrum>; render() hands it the data after this HTML lands
    return `<details class="why-box spec">
      <summary>What’s actually in the water right now</summary>
      <div class="spec-body">
        <wave-spectrum></wave-spectrum>
        <div class="spec-key">
          <div class="spec-num">
            <span>Peak <b>${sp.peakPeriod}s</b></span>
            <span>Hs <b>${sp.hsFt} ft</b></span>
            <span>Organised <b>${org}%</b></span>
          </div>
          Curve height = how much wave energy sits at that period
          (<b>${max.toFixed(2)} m²/Hz</b> at the peak). Reads as <b>${read}</b>.
          One sharp spike is a single organised swell; two humps are crossed swells.
          Height, period and direction look identical for both — this is the only
          chart that tells them apart.
          <br><span class="obs">Buoy ${sp.buoy} (Block Island), measured ${fcWhen(sp.at)} —
          <b>observed, not forecast</b>. There is no forecast spectrum: the wave model
          gives partitions, not a spectrum.</span>
        </div>
      </div>
    </details>`;
  }

  _foot() {
    const d = this._d;
    const age = (Date.now() - new Date(d.generatedAt)) / 36e5;
    const gt = d.groundTruth;
    return `<div class="foot">
      Updated ${fcWhen(d.generatedAt)} (${age < 1 ? 'just now' : Math.round(age) + 'h ago'});
      rebuilt daily at 5am.
      ${gt ? `Observations and the human surf report are courtesy of
        <a href="${gt.source}" target="_blank" rel="noopener">Warm Winds</a>, Narragansett.` : ''}
      <br>Scores are <b>ordinal, not cardinal</b> — “Deep Hole beats Green Hill today”,
      not “Deep Hole is a 62”. Not yet calibrated against real sessions.
    </div>`;
  }

  _bind() {
    this.shadowRoot.querySelectorAll('.tabs button').forEach(b =>
      b.addEventListener('click', () => {
        this._scope = b.dataset.k; this._open = null; this.render();
      }));
    this.shadowRoot.querySelectorAll('.row').forEach(r =>
      r.addEventListener('click', () => {
        this._open = this._open === r.dataset.spot ? null : r.dataset.spot;
        this.render();
      }));
    this.shadowRoot.querySelectorAll('.spotcell[data-rose]').forEach(c =>
      c.addEventListener('click', () => { this._roseSpot = c.dataset.rose; this.render(); }));
    const f = this.shadowRoot.getElementById('flip');
    if (f) f.addEventListener('click', () => { this._showFlat = !this._showFlat; this.render(); });
  }
}
customElements.define('surf-forecast', SurfForecast);


/* <wave-spectrum> -- the buoy's energy spectrum, as a curve.
   Set `.data` to the forecast's `spectrum` object; it draws itself.

   It is a LINE, not bars, and that is a correctness fix rather than a style choice.
   The buoy samples uniformly in FREQUENCY, and the build hands us 1/f as a period.
   Period is not linear in frequency, so those samples arrive bunched at the short
   end and stretched at the long end. Bars need a width, and there is no honest width
   to give them: a fixed one overlapped its neighbours by more than a full bar at 3 s
   (a smeared block) while leaving gaps 5x the bar width at 18 s (a picket fence).
   A curve just puts each sample at its true period and joins them, so uneven spacing
   stops being a defect and becomes a non-issue. */
class WaveSpectrum extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  set data(sp) { this._sp = sp; this._draw(); }
  get data() { return this._sp; }

  connectedCallback() { if (this._sp) this._draw(); }

  _draw() {
    const sp = this._sp;
    if (!sp || !sp.bins || !sp.bins.length) { this.shadowRoot.innerHTML = ''; return; }

    const W = 320, H = 112, L = 36, R = 8, T = 14, B = 28;
    const tMin = 2, tMax = 22;
    const x0 = L, x1 = W - R, y0 = H - B, y1 = T;

    /* Merge samples that collapse onto the same period. The build rounds 1/f, so two
       neighbouring frequencies can land on the same period -- as points that is a
       vertical jag in the curve, not a feature of the sea. */
    const m = new Map();
    for (const [t, e] of sp.bins) {
      if (t < tMin || t > tMax) continue;
      const p = m.get(t);
      if (p) { p.sum += e; p.n++; } else m.set(t, { sum: e, n: 1 });
    }
    const pts = [...m.entries()].map(([t, v]) => [t, v.sum / v.n]).sort((a, b) => a[0] - b[0]);
    if (pts.length < 2) { this.shadowRoot.innerHTML = ''; return; }

    const max = Math.max(...pts.map(p => p[1]), 0.0001);
    const X = t => x0 + (t - tMin) / (tMax - tMin) * (x1 - x0);
    const Y = e => y0 - (e / max) * (y0 - y1);

    const line = pts.map((p, i) =>
      `${i ? 'L' : 'M'} ${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(' ');
    const area = `${line} L ${X(pts[pts.length - 1][0]).toFixed(1)} ${y0}
                  L ${X(pts[0][0]).toFixed(1)} ${y0} Z`;

    /* y axis: without it a tall peak is just "the tallest peak", which says nothing
       about how much energy is actually in the water. */
    const grid = v => `
      <line x1="${x0}" y1="${Y(v).toFixed(1)}" x2="${x1}" y2="${Y(v).toFixed(1)}"
        stroke="#2b2b28" stroke-width="1" stroke-dasharray="2 3"/>
      <text x="${x0 - 6}" y="${Y(v).toFixed(1)}" font-size="7.5" fill="#6a695f"
        text-anchor="end" dominant-baseline="middle">${v.toFixed(2)}</text>`;

    const ticks = [4, 8, 12, 16, 20].map(t =>
      `<text x="${X(t).toFixed(1)}" y="${y0 + 11}" font-size="7.5" fill="#6a695f"
         text-anchor="middle">${t}</text>`).join('');

    /* Anchor the marker to the curve point nearest the build's peakPeriod, rather than
       to our own max: the two agree, and the panel prints peakPeriod right below. */
    const pk = pts.reduce((a, b) =>
      Math.abs(b[0] - sp.peakPeriod) < Math.abs(a[0] - sp.peakPeriod) ? b : a);
    const px = X(pk[0]), flip = px > x1 - 46;
    const peak = `
      <line x1="${px.toFixed(1)}" y1="${Y(pk[1]).toFixed(1)}" x2="${px.toFixed(1)}" y2="${y0}"
        stroke="#cde2fb" stroke-width="1" stroke-dasharray="2 2" opacity=".5"/>
      <circle cx="${px.toFixed(1)}" cy="${Y(pk[1]).toFixed(1)}" r="2.6" fill="#cde2fb"/>
      <text x="${(px + (flip ? -5 : 5)).toFixed(1)}" y="${(y1 + 7).toFixed(1)}" font-size="8"
        fill="#cde2fb" text-anchor="${flip ? 'end' : 'start'}">peak ${sp.peakPeriod}s</text>`;

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        svg { display: block; width: 100%; height: auto; background: #15161a;
              border: 1px solid #2b2b28; border-radius: 6px; }
      </style>
      <svg viewBox="0 0 ${W} ${H}" role="img"
        aria-label="Wave energy density against wave period. Peak ${sp.peakPeriod} seconds, ${sp.organization}% organised.">
        <text x="${x0 - 6}" y="7" font-size="7" fill="#6a695f" text-anchor="end">m²/Hz</text>
        ${grid(max)}${grid(max / 2)}
        <line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y0}" stroke="#3a3a34" stroke-width="1"/>
        <text x="${x0 - 6}" y="${y0}" font-size="7.5" fill="#6a695f"
          text-anchor="end" dominant-baseline="middle">0</text>
        <path d="${area}" fill="#3987e5" opacity=".18"/>
        <path d="${line}" fill="none" stroke="#6da7ec" stroke-width="2"
          stroke-linejoin="round" stroke-linecap="round"/>
        ${peak}${ticks}
        <text x="${((x0 + x1) / 2).toFixed(1)}" y="${H - 3}" font-size="7.5" fill="#6a695f"
          text-anchor="middle">wave period (seconds)</text>
      </svg>`;
  }
}
customElements.define('wave-spectrum', WaveSpectrum);
