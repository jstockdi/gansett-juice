/* ==================================================================== *
 *  <surf-forecast> — the forecast tab for index.html                    *
 *                                                                       *
 *  Two views over the same data/forecast.json, switched by width:       *
 *    phone  -> ranked "best now / today / this week" (demos/ranked)     *
 *    wide   -> spots x time heatmap                 (demos/heatmap)     *
 *                                                                       *
 *  Scores are precomputed by .claude/skills/surf-forecast/forecast.py.  *
 *  Nothing here recomputes one. Quality is encoded twice -- colour AND  *
 *  length/height -- so it survives colour-blindness and direct sun.     *
 *                                                                       *
 *  score is number | null. null means NO DATA and is never rendered as  *
 *  the bad end of the ramp.                                             *
 * ==================================================================== */

const FC_RAMP = ['#184f95', '#256abf', '#3987e5', '#6da7ec', '#9ec5f4', '#cde2fb'];
const fcRamp = s => FC_RAMP[Math.min(5, Math.floor(s / 100 * 6))];
const FC_VERDICT = s =>
  s >= 75 ? 'Go' : s >= 55 ? 'Worth a look' : s >= 30 ? 'Marginal' : s > 0 ? 'Poor' : 'Flat / blown out';
const FC_LIMIT = {
  size: 'not the right size', wind: 'wind is wrong', period: 'period too short',
  tide: 'tide is wrong', none: 'nothing holding it back',
};
const FC_REGION = id =>
  ['narragansett-town-beach', 'monahans-dock'].includes(id) ? 'narragansett'
  : ['point-judith-east', 'point-judith-south', 'k38', 'camp-cronin'].includes(id) ? 'point-judith'
  : 'matunuck';
const fcWhen = t => new Date(t).toLocaleString('en-US',
  { weekday: 'short', hour: 'numeric', hour12: true });

class SurfForecast extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._scope = 'bestToday';
    this._open = null;
    this.shadowRoot.innerHTML = `
      <style>
        :host { position: absolute; inset: 0; display: block; overflow-y: auto;
          -webkit-overflow-scrolling: touch; background: #111110; color: #fff;
          padding: max(18px, env(safe-area-inset-top)) 16px 96px;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        :host([hidden]) { display: none; }
        .wrap { max-width: 640px; margin: 0 auto; }

        .stale { font-size: 11.5px; color: #86857c; margin-bottom: 12px; }
        .stale b { color: #c3c2b7; font-weight: 600; }

        .hero { background: #1a1a19; border: 1px solid #34342f; border-radius: 12px;
          padding: 15px 16px 14px; margin-bottom: 12px; }
        .hero .lbl { font-size: 10.5px; letter-spacing: .09em; text-transform: uppercase; color: #86857c; }
        .hero .spot { font-size: 21px; font-weight: 650; margin: 3px 0 1px; letter-spacing: -.02em; }
        .hero .fig { font-size: 50px; font-weight: 300; line-height: 1.05;
          font-variant-numeric: tabular-nums; letter-spacing: -.03em; }
        .hero .fig span { font-size: 16px; color: #86857c; font-weight: 400; }
        .hero .verdict { font-size: 13px; color: #c3c2b7; margin-top: 2px; }
        .hero .why { font-size: 12px; color: #86857c; margin-top: 8px; line-height: 1.5; }

        .tabs { display: flex; gap: 6px; margin-bottom: 10px; }
        .tabs button { flex: 1; background: #1a1a19; color: #c3c2b7; border: 1px solid #34342f;
          border-radius: 8px; padding: 8px 6px; font: inherit; font-size: 12.5px;
          font-weight: 600; cursor: pointer; -webkit-tap-highlight-color: transparent; }
        .tabs button[aria-selected="true"] { background: #2e2e2a; color: #fff; border-color: #55554d; }

        ol { list-style: none; margin: 0; padding: 0; background: #1a1a19;
          border: 1px solid #34342f; border-radius: 12px; overflow: hidden; }
        li { border-bottom: 1px solid #34342f; }
        li:last-child { border-bottom: 0; }
        .row { display: grid; grid-template-columns: 20px 1fr auto; gap: 10px; align-items: center;
          padding: 11px 13px; cursor: pointer; -webkit-tap-highlight-color: transparent; }
        .rank { font-size: 12px; color: #86857c; font-variant-numeric: tabular-nums; text-align: right; }
        .nm { font-size: 13.5px; font-weight: 600; }
        .conf { font-size: 10px; color: #86857c; font-weight: 400; margin-left: 5px; }
        /* CHANNEL 2 -- length. display:block matters; a bare span collapses it. */
        .meter { display: block; height: 5px; border-radius: 3px; background: #2b2b28;
          margin-top: 6px; overflow: hidden; }
        .meter i { display: block; height: 100%; border-radius: 3px; }
        .why { display: block; font-size: 11.5px; color: #86857c; margin-top: 5px; }
        .sc { text-align: right; }
        .sc b { font-size: 19px; font-variant-numeric: tabular-nums; }
        .sc .at { display: block; font-size: 10.5px; color: #86857c; margin-top: 1px; }

        .drill { padding: 0 13px 13px; }
        .drill.hidden { display: none; }
        .strip { display: flex; gap: 1px; align-items: flex-end; height: 40px; background: #22221f;
          border-radius: 6px; padding: 4px; margin-bottom: 8px; }
        .strip i { flex: 1; border-radius: 1.5px; min-height: 1px; }
        .strip i.nd { background: repeating-linear-gradient(45deg,#2b2b28 0 3px,transparent 3px 6px);
          height: 100% !important; border: 1px dashed #3d3d38; }
        .facts { display: grid; grid-template-columns: repeat(auto-fit,minmax(88px,1fr)); gap: 8px;
          font-size: 11.5px; }
        .fact { background: #22221f; border-radius: 7px; padding: 7px 9px; }
        .fact .k { color: #86857c; font-size: 10px; display: block; }
        .fact .v { font-weight: 600; }
        .cau { font-size: 11.5px; color: #fab219; margin-top: 8px; line-height: 1.45; }

        .foot { font-size: 11px; color: #6a695f; margin-top: 14px; line-height: 1.5; }
        .foot a { color: #86857c; }

        /* ---- wide screens get the heatmap instead ---- */
        .grid { display: none; }
        @media (min-width: 900px) {
          .wrap { max-width: 1180px; }
          .ranked { display: none; }
          .grid { display: block; }
        }
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
      this.render();
    } catch (e) {
      this.shadowRoot.getElementById('wrap').innerHTML =
        `<div class="err">Couldn't load the forecast.<br>
         <span style="color:#86857c">It's rebuilt each morning at 5am —
         if this persists, the build may have failed.</span></div>`;
    }
  }

  render() {
    const d = this._d;
    const age = (Date.now() - new Date(d.generatedAt)) / 36e5;
    const stale = age > 30;
    this.shadowRoot.getElementById('wrap').innerHTML = `
      <div class="stale">
        <b>${stale ? '⚠ Stale' : 'Updated'}</b> ${fcWhen(d.generatedAt)}
        · ${age < 1 ? 'just now' : Math.round(age) + 'h ago'}
        ${stale ? ' — the 5am build may not have run.' : ''}
      </div>
      <div class="ranked">${this._ranked()}</div>
      <div class="grid">${this._heatmap()}</div>
      <div class="foot">
        Scores are <b>ordinal, not cardinal</b> — “Deep Hole beats Green Hill today”, not
        “Deep Hole is a 62”. The model is not yet calibrated against real sessions.
        Ground truth: <a href="https://www.warmwinds.com/surf-report" target="_blank"
        rel="noopener">Warm Winds</a>.
      </div>`;
    this._bind();
  }

  /* ---------------------------------------------------------- ranked view */
  _ranked() {
    const d = this._d;
    const list = d.summary[this._scope] || [];
    const spot = id => d.spots.find(s => s.id === id);
    const idxOf = e => (e.at ? d.times.indexOf(e.at) : 0);
    const top = list[0];

    const hero = !top ? `<div class="hero"><div class="lbl">Nothing scored</div>
        <div class="spot">No data</div></div>` : (() => {
      const i = idxOf(top), c = d.scores[top.spot][i];
      const w = d.conditions.wind[FC_REGION(top.spot)][i];
      return `<div class="hero">
        <div class="lbl">${this._scope === 'bestNow' ? 'Best right now'
          : this._scope === 'bestToday' ? 'Best today' : 'Best this week'}</div>
        <div class="spot">${spot(top.spot).name}</div>
        <div class="fig">${top.s}<span>/100</span></div>
        <div class="verdict">${FC_VERDICT(top.s)}${top.at ? ' · ' + fcWhen(top.at) : ''}</div>
        <div class="why">${c.hEff} ft @ ${c.tEff}s from ${c.dEff}°${
          w ? ` · wind ${w.spd} kt from ${w.dir}°` : ''}<br>
          ${top.s > 0 ? FC_LIMIT[c.limiting] : 'nothing is breaking'}</div>
      </div>`;
    })();

    const rows = list.map((e, n) => {
      const i = idxOf(e), c = d.scores[e.spot][i], sp = spot(e.spot);
      const open = this._open === e.spot;
      return `<li>
        <div class="row" data-spot="${e.spot}">
          <span class="rank">${n + 1}</span>
          <span>
            <span class="nm">${sp.name}${sp.confidence !== 'sourced'
              ? `<span class="conf">${sp.confidence}</span>` : ''}</span>
            <span class="meter"><i style="width:${e.s}%;background:${fcRamp(e.s)}"></i></span>
            <span class="why">${e.s === 0 ? 'flat or blown out'
              : c.limiting === 'none' ? `${c.hEff} ft @ ${c.tEff}s from ${c.dEff}°`
              : FC_LIMIT[c.limiting]}</span>
          </span>
          <span class="sc"><b>${e.s}</b>${e.at ? `<span class="at">${fcWhen(e.at)}</span>` : ''}</span>
        </div>
        <div class="drill ${open ? '' : 'hidden'}">${open ? this._drill(e.spot, i) : ''}</div>
      </li>`;
    }).join('') || '<li><div class="row"><span></span><span class="why">No spots scored.</span></div></li>';

    return `${hero}
      <div class="tabs">
        <button data-k="bestNow" aria-selected="${this._scope === 'bestNow'}">Right now</button>
        <button data-k="bestToday" aria-selected="${this._scope === 'bestToday'}">Today</button>
        <button data-k="bestWeek" aria-selected="${this._scope === 'bestWeek'}">This week</button>
      </div>
      <ol>${rows}</ol>`;
  }

  _drill(id, i) {
    const d = this._d, row = d.scores[id], c = row[i];
    const w = d.conditions.wind[FC_REGION(id)][i];
    const td = d.conditions.tide[i];
    const strip = row.map((x, k) => x.s === null ? '<i class="nd"></i>'
      : `<i style="height:${Math.max(2, x.s)}%;background:${fcRamp(x.s)};${
          k === i ? 'outline:1.5px solid #fff;outline-offset:-1px' : ''}"></i>`).join('');
    const fact = (k, v) => `<div class="fact"><span class="k">${k}</span><span class="v">${v}</span></div>`;
    return `<div class="strip">${strip}</div>
      <div class="facts">
        ${fact('Surf', `${c.hEff} ft @ ${c.tEff}s`)}
        ${fact('From', `${c.dEff}°`)}
        ${w ? fact('Wind', `${w.spd} kt @ ${w.dir}°`) : ''}
        ${td ? fact('Tide', `${td.ft} ft ${td.stage}`) : ''}
        ${fact('Limited by', FC_LIMIT[c.limiting])}
      </div>
      ${c.caution ? `<div class="cau">⚠ Scoring on swell that only reaches this spot by bending
        around the headland. That taper is an inference, not a sourced rule.</div>` : ''}`;
  }

  /* --------------------------------------------------------- heatmap view */
  _heatmap() {
    const d = this._d, times = d.times;
    const days = [];
    times.forEach((t, i) => {
      const day = t.slice(0, 10);
      if (!days.length || days[days.length - 1].day !== day) days.push({ day, start: i, n: 0 });
      days[days.length - 1].n++;
    });
    const fmt = day => new Date(day + 'T12:00:00')
      .toLocaleDateString('en-US', { weekday: 'short', month: 'numeric', day: 'numeric' });

    let h = '<div class="scroll"><table><thead><tr><th class="spotcell"></th>';
    days.forEach(dd => { h += `<th class="dayhdr daysep" colspan="${dd.n}">${fmt(dd.day)}</th>`; });
    h += '</tr></thead><tbody>';

    for (const sp of d.spots) {
      const row = d.scores[sp.id];
      const vals = row.map(c => c.s).filter(v => v !== null);
      const peak = vals.length ? Math.max(...vals) : null;
      let done = false;
      h += `<tr><th class="spotcell">${sp.name}</th>`;
      row.forEach((c, i) => {
        const sep = days.some(dd => dd.start === i) ? ' daysep' : '';
        if (c.s === null) { h += `<td class="cell${sep}"><span class="nodata"></span></td>`; return; }
        const isPeak = !done && peak > 0 && c.s === peak;
        if (isPeak) done = true;
        h += `<td class="cell${sep}${isPeak ? ' peak' : ''}${c.caution ? ' caution' : ''}"
                title="${sp.name} — ${c.s}/100 · ${c.hEff}ft @ ${c.tEff}s · ${FC_LIMIT[c.limiting]}">
                <span class="fill" style="height:${Math.max(2, Math.round(c.s / 100 * 24))}px;
                background:${fcRamp(c.s)}"></span></td>`;
      });
      h += '</tr>';
    }
    return h + '</tbody></table></div><div class="key">'
      + '<span class="sw">worse <span class="ramp">'
      + FC_RAMP.map((c, i) => `<i style="background:${c};height:${6 + i * 4}px"></i>`).join('')
      + '</span> better</span>'
      + '<span class="sw"><span class="hatch"></span> no data (never “bad”)</span>'
      + '<span class="sw"><span class="dot"></span> diffraction taper — inferred</span></div>';
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
  }
}
customElements.define('surf-forecast', SurfForecast);
