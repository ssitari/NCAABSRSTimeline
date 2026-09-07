// ============================================================
//  app.js — Visualization engine
//  Do not edit unless you are modifying the tool itself.
// ============================================================

import {
  SRS_FILE, COLORS_FILE, CHAMPIONSHIPS_FILE, CURATED_FILE,
  FULL_YEAR_RANGE, DEFAULT_YEAR_RANGE, MOVING_AVERAGE_WINDOW,
  BASE_COLOR, BASE_OPACITY, BASE_HOVER_OPACITY,
  CURATED_OPACITY, CURATED_STROKE_WIDTH,
  PINNED_STROKE_WIDTH, HOVER_STROKE_WIDTH, BASE_STROKE_WIDTH,
  TOP_N_TEAMS, CONFERENCE_HUE_SATURATION, CONFERENCE_HUE_LIGHTNESS,
  CHAMPIONSHIP_MARKER_RADIUS, CHAMPIONSHIP_MARKER_FILL, CHAMPIONSHIP_MARKER_STROKE,
} from './config.js';

// ============================================================
//  STATE
// ============================================================

const state = {
  yearRange: [...DEFAULT_YEAR_RANGE],   // currently visible [start, end]
  movingAverage: false,
  curatedOnly: false,                 // hide the non-curated grey field entirely
  pinned: new Map(),                 // key ("team:School" / "conf:Conf") -> {type, id, label, color}
  hovered: null,                     // key of team under the cursor, or null
};

let teams = new Map();     // school -> { school, conf, color, rows: [...], segments: [[...]] }
let conferences = new Map(); // conf  -> { conf, color, rows: [...] (all member rows, any team) }
let allYears = [];
let championshipsByTeam = new Map(); // school -> Map(year -> { coach, score, runner_up, vacated })

// ============================================================
//  DOM
// ============================================================

const el = {
  focusSvg:    d3.select('#focus-svg'),
  contextSvg:  d3.select('#context-svg'),
  tooltip:     document.getElementById('tooltip'),
  loading:     document.getElementById('loading'),
  readout:     document.getElementById('readout'),
  rangeLabel:  document.getElementById('rangeLabel'),
  movingAvgToggle: document.getElementById('movingAvgToggle'),
  curatedOnlyToggle: document.getElementById('curatedOnlyToggle'),
  resetBtn:    document.getElementById('resetZoom'),
  teamSearch:  document.getElementById('teamSearch'),
  teamSuggest: document.getElementById('teamSuggest'),
  confSearch:  document.getElementById('confSearch'),
  confSuggest: document.getElementById('confSuggest'),
  pinnedChips: document.getElementById('pinnedChips'),
  summaryRange: document.getElementById('summaryRange'),
  summaryAvg:  document.getElementById('summaryAvg'),
  topTeamsBody: document.querySelector('#topTeamsTable tbody'),
  confAvgBody:  document.querySelector('#confAvgTable tbody'),
  minSeasonsNote: document.getElementById('minSeasonsNote'),
};

// ============================================================
//  LOAD + SHAPE
// ============================================================

async function main() {
  let srsRows, colorRows, champRows, curatedRows;
  try {
    [srsRows, colorRows, champRows, curatedRows] = await Promise.all([
      d3.csv(SRS_FILE, rowConvert),
      d3.csv(COLORS_FILE, d => d),
      d3.csv(CHAMPIONSHIPS_FILE, d => ({
        year: +d.year,
        school: d.school,
        coach: d.coach,
        score: d.score,
        runner_up: d.runner_up,
        vacated: d.vacated === 'True',
      })),
      d3.csv(CURATED_FILE, d => d).catch(() => []),
    ]);
  } catch (err) {
    el.loading.textContent = 'Failed to load data: ' + err.message;
    el.loading.classList.add('err');
    return;
  }

  const colorByTeam = new Map(colorRows.map(d => [d.school, d.primary_hex]));
  const curatedSet = new Set(curatedRows.map(d => d.school));
  // If the curated file is missing/empty, treat every team as curated so the
  // tool still works (it just won't have the readable opening view).
  const curatedFallbackAll = curatedSet.size === 0;

  const confNames = [...new Set(srsRows.map(d => d.conf))].filter(Boolean).sort(d3.ascending);
  const confHue = d3.scaleOrdinal()
    .domain(confNames)
    .range(confNames.map((_, i) => i * 360 / confNames.length));

  const confColor = conf => `hsl(${confHue(conf)}, ${CONFERENCE_HUE_SATURATION}%, ${CONFERENCE_HUE_LIGHTNESS}%)`;

  for (const conf of confNames) {
    conferences.set(conf, { conf, color: confColor(conf), rows: [] });
  }

  const bySchool = d3.group(srsRows, d => d.school);
  for (const [school, rows] of bySchool) {
    rows.sort((a, b) => d3.ascending(a.year, b.year));
    const mostRecentConf = rows[rows.length - 1].conf;
    const color = colorByTeam.get(school) || confColor(mostRecentConf);
    teams.set(school, {
      school,
      conf: mostRecentConf,
      color,
      curated: curatedFallbackAll || curatedSet.has(school),
      rows,
      segments: contiguousSegments(rows),
    });
    for (const row of rows) {
      const bucket = conferences.get(row.conf);
      if (bucket) bucket.rows.push(row);
    }
  }

  for (const [, team] of teams) {
    team.rows.forEach((row, i) => {
      row.ma = movingAverageAt(team.rows, i, 'srs');
    });
  }
  for (const [, bucket] of conferences) {
    bucket.rows.sort((a, b) => d3.ascending(a.year, b.year));
  }

  allYears = srsRows.map(d => d.year);

  for (const c of champRows) {
    if (!teams.has(c.school)) continue; // shouldn't happen; matched at build time
    let byYear = championshipsByTeam.get(c.school);
    if (!byYear) championshipsByTeam.set(c.school, byYear = new Map());
    byYear.set(c.year, {
      coach: c.coach, score: c.score, runner_up: c.runner_up, vacated: c.vacated,
    });
  }

  el.loading.style.display = 'none';
  buildLayout();
  render();
  wireControls();
}

function rowConvert(d) {
  return {
    year: +d.year,
    school: d.school,
    conf: baseConf(d.conf),
    ap_rank: d.ap_rank,
    wins: +d.wins || 0,
    losses: +d.losses || 0,
    srs_off: d.srs_off === '' ? NaN : +d.srs_off,
    srs_def: d.srs_def === '' ? NaN : +d.srs_def,
    // Sports Reference lists W-L for D-I *transitional* programs (and a few
    // early independents) but computes no rating for them -- keep the season
    // row, but as NaN so the line breaks and it's left out of every mean.
    srs: d.srs === '' ? NaN : +d.srs,
  };
}

// Strip any parenthetical qualifier from a conference name so a divisional
// or regional label collapses into its parent conference. (Rare in the CBB
// data, but harmless where absent.)
function baseConf(conf) {
  return (conf || '').replace(/\s*\([^)]*\)\s*$/, '').trim();
}

// Break a sorted rows array into runs of consecutive years, so lines don't
// bridge gaps where a program didn't field a rated team.
function contiguousSegments(rows) {
  const segments = [];
  let current = [];
  for (const row of rows) {
    if (current.length && row.year !== current[current.length - 1].year + 1) {
      segments.push(current);
      current = [];
    }
    current.push(row);
  }
  if (current.length) segments.push(current);
  return segments;
}

// Trailing average over the team's own last N *rated* seasons ending at
// index i (not N calendar years), so a program that skipped years still
// gets a sensible smoothed value once it resumes.
function movingAverageAt(rows, i, field) {
  const start = Math.max(0, i - MOVING_AVERAGE_WINDOW + 1);
  const slice = rows.slice(start, i + 1);
  return d3.mean(slice, d => d[field]);
}

// ============================================================
//  LAYOUT
// ============================================================

const margin = { top: 16, right: 24, bottom: 28, left: 44 };
const contextMargin = { top: 6, right: 24, bottom: 20, left: 44 };

let width = 0, focusHeight = 0, contextHeight = 0;
let xFocus, yFocus, xContext, yContext;
let brush;

function buildLayout() {
  const focusBox = el.focusSvg.node().parentElement.getBoundingClientRect();
  width = focusBox.width;
  focusHeight = focusBox.height;
  contextHeight = el.contextSvg.node().parentElement.getBoundingClientRect().height;

  el.focusSvg.attr('viewBox', `0 0 ${width} ${focusHeight}`);
  el.contextSvg.attr('viewBox', `0 0 ${width} ${contextHeight}`);

  xFocus = d3.scaleLinear().range([margin.left, width - margin.right]);
  yFocus = d3.scaleLinear().range([focusHeight - margin.bottom, margin.top]);
  xContext = d3.scaleLinear().domain(FULL_YEAR_RANGE).range([contextMargin.left, width - contextMargin.right]);
  yContext = d3.scaleLinear().range([contextHeight - contextMargin.bottom, contextMargin.top]);

  // Axis domains follow the curated set only -- a single -35 season from an
  // otherwise-ignored program shouldn't stretch the whole scale.
  const scaleTeams = [...teams.values()].filter(t => t.curated);
  const allRawExtent = d3.extent(
    scaleTeams.flatMap(t => t.rows.map(r => r.srs))
  );
  yContext.domain(allRawExtent).nice();

  el.focusSvg.selectAll('*').remove();
  el.contextSvg.selectAll('*').remove();

  el.focusSvg.append('g').attr('class', 'axis x-axis');
  el.focusSvg.append('g').attr('class', 'axis y-axis');
  el.focusSvg.append('line').attr('class', 'zero-line');
  el.focusSvg.append('g').attr('class', 'bg-field');
  el.focusSvg.append('g').attr('class', 'hit-field');
  el.focusSvg.append('g').attr('class', 'pinned-field');
  el.focusSvg.append('g').attr('class', 'avg-field');
  el.focusSvg.append('g').attr('class', 'champ-field');

  el.contextSvg.append('g').attr('class', 'axis x-axis')
    .attr('transform', `translate(0,${contextHeight - contextMargin.bottom})`);
  el.contextSvg.append('g').attr('class', 'bg-field ctx-bg');

  const gCtxBg = el.contextSvg.select('.ctx-bg');
  const ctxLine = d3.line()
    .defined(d => Number.isFinite(d.srs))
    .x(d => xContext(d.year)).y(d => yContext(d.srs));
  for (const team of scaleTeams) {
    for (const seg of team.segments) {
      if (seg.length < 2) continue;
      gCtxBg.append('path').attr('d', ctxLine(seg))
        .attr('fill', 'none').attr('stroke', BASE_COLOR)
        .attr('stroke-width', 0.6).attr('stroke-opacity', 0.3);
    }
  }
  el.contextSvg.select('.x-axis').call(d3.axisBottom(xContext).tickFormat(d3.format('d')).ticks(10));

  const brushG = el.contextSvg.append('g').attr('class', 'brush');
  brush = d3.brushX()
    .extent([[contextMargin.left, contextMargin.top], [width - contextMargin.right, contextHeight - contextMargin.bottom]])
    .on('brush end', brushed);
  brushG.call(brush);
  brushG.call(brush.move, state.yearRange.map(xContext));

  window.addEventListener('resize', debounce(() => { buildLayout(); render(); }, 200));
}

function brushed(eventOrNull) {
  const event = eventOrNull;
  if (!event) return;
  let range;
  if (event.selection) {
    range = event.selection.map(xContext.invert).map(Math.round);
  } else {
    range = [...DEFAULT_YEAR_RANGE];
  }
  if (range[0] === range[1]) return;
  state.yearRange = range;
  render();
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ============================================================
//  RENDER
// ============================================================

function valueField() { return state.movingAverage ? 'ma' : 'srs'; }

function visibleRowsFor(rows) {
  return rows.filter(r => r.year >= state.yearRange[0] && r.year <= state.yearRange[1]);
}

function render() {
  xFocus.domain(state.yearRange);

  const field = valueField();
  const visibleValues = [...teams.values()]
    .filter(t => t.curated)
    .flatMap(t => visibleRowsFor(t.rows).map(r => r[field]))
    .filter(v => Number.isFinite(v));
  const [lo, hi] = d3.extent(visibleValues.length ? visibleValues : [-10, 10]);
  const pad = Math.max(1, (hi - lo) * 0.08);
  yFocus.domain([lo - pad, hi + pad]);

  el.focusSvg.select('.x-axis')
    .attr('transform', `translate(0,${focusHeight - margin.bottom})`)
    .call(d3.axisBottom(xFocus).tickFormat(d3.format('d')));
  el.focusSvg.select('.y-axis')
    .attr('transform', `translate(${margin.left},0)`)
    .call(d3.axisLeft(yFocus).ticks(6));
  el.focusSvg.select('.zero-line')
    .attr('x1', margin.left).attr('x2', width - margin.right)
    .attr('y1', yFocus(0)).attr('y2', yFocus(0));

  renderBackgroundAndHit();
  renderPinned();
  renderSummary();
  updateReadout();
}

function lineGen(field) {
  return d3.line()
    .defined(d => Number.isFinite(d[field]))
    .x(d => xFocus(d.year))
    .y(d => yFocus(d[field]));
}

function renderBackgroundAndHit() {
  const field = valueField();
  const line = lineGen(field);

  const bgSel = el.focusSvg.select('.bg-field')
    .selectAll('path.bg-line')
    .data([...teams.values()], d => d.school);

  bgSel.exit().remove();
  const bgEnter = bgSel.enter().append('path').attr('class', 'bg-line')
    .attr('fill', 'none');
  bgEnter.merge(bgSel)
    .attr('d', d => d.segments.map(line).join(' '))
    .attr('stroke', d => (state.hovered === d.school || d.curated) ? d.color : BASE_COLOR)
    .attr('stroke-opacity', d => {
      if (state.pinned.has('team:' + d.school)) return 0; // drawn in pinned layer instead
      if (state.hovered === d.school) return BASE_HOVER_OPACITY;
      if (!d.curated) return state.curatedOnly ? 0 : BASE_OPACITY;
      return CURATED_OPACITY;
    })
    .attr('stroke-width', d => {
      if (state.hovered === d.school) return HOVER_STROKE_WIDTH;
      return d.curated ? CURATED_STROKE_WIDTH : BASE_STROKE_WIDTH;
    });

  const hitSel = el.focusSvg.select('.hit-field')
    .selectAll('path.hit-line')
    .data([...teams.values()], d => d.school);
  hitSel.exit().remove();
  const hitEnter = hitSel.enter().append('path').attr('class', 'hit-line')
    .attr('fill', 'none').attr('stroke', 'transparent').attr('stroke-width', 8)
    .style('pointer-events', 'stroke')
    .on('pointerenter', (event, d) => { state.hovered = d.school; renderBackgroundAndHit(); showTooltip(event, d); })
    .on('pointermove', (event, d) => showTooltip(event, d))
    .on('pointerleave', (event, d) => { state.hovered = null; renderBackgroundAndHit(); hideTooltip(); })
    .on('click', (event, d) => togglePin('team', d.school));
  hitEnter.merge(hitSel).attr('d', d => d.segments.map(line).join(' '));

  renderChampionshipMarkers();
}

function renderPinned() {
  const field = valueField();
  const line = lineGen(field);

  const entries = [...state.pinned.values()];
  const pinnedTeams = entries.filter(e => e.type === 'team').map(e => teams.get(e.id));
  const pinnedConfs = entries.filter(e => e.type === 'conf').map(e => conferences.get(e.id));

  const teamSel = el.focusSvg.select('.pinned-field')
    .selectAll('path.pinned-team')
    .data(pinnedTeams, d => d.school);
  teamSel.exit().remove();
  teamSel.enter().append('path').attr('class', 'pinned-team').attr('fill', 'none')
    .merge(teamSel)
    .attr('stroke', d => d.color)
    .attr('stroke-width', PINNED_STROKE_WIDTH)
    .attr('stroke-opacity', 1)
    .attr('d', d => d.segments.map(line).join(' '));

  // Conference average line: per-year mean SRS across that conference's rated teams.
  const confSel = el.focusSvg.select('.pinned-field')
    .selectAll('path.pinned-conf')
    .data(pinnedConfs, d => d.conf);
  confSel.exit().remove();
  confSel.enter().append('path').attr('class', 'pinned-conf').attr('fill', 'none')
    .merge(confSel)
    .attr('stroke', d => d.color)
    .attr('stroke-width', PINNED_STROKE_WIDTH)
    .attr('stroke-dasharray', '5 3')
    .attr('stroke-opacity', 1)
    .attr('d', d => {
      const byYear = d3.rollup(d.rows, v => d3.mean(v, r => r[field]), r => r.year);
      const series = [...byYear.entries()].sort((a, b) => d3.ascending(a[0], b[0]))
        .map(([year, val]) => ({ year, [field]: val }));
      const segs = contiguousSegments(series.map(s => ({ year: s.year, [field]: s[field] })));
      return segs.map(line).join(' ');
    });

  // Horizontal period-average lines (always computed from raw srs, not smoothed).
  const avgLines = entries.map(e => {
    const rows = e.type === 'team' ? teams.get(e.id).rows : conferences.get(e.id).rows;
    const visible = visibleRowsFor(rows);
    if (!visible.length) return null;
    return { ...e, avg: d3.mean(visible, r => r.srs) };
  }).filter(Boolean);

  const avgSel = el.focusSvg.select('.avg-field')
    .selectAll('line.avg-line')
    .data(avgLines, d => d.type + d.id);
  avgSel.exit().remove();
  avgSel.enter().append('line').attr('class', 'avg-line')
    .merge(avgSel)
    .attr('x1', margin.left).attr('x2', width - margin.right)
    .attr('y1', d => yFocus(d.avg)).attr('y2', d => yFocus(d.avg))
    .attr('stroke', d => d.color)
    .attr('stroke-width', 1.25)
    .attr('stroke-dasharray', '2 2')
    .attr('stroke-opacity', 0.85);

  renderChampionshipMarkers();
}

// National-championship markers: only for teams currently pinned or
// hovered, per design -- the background field stays a clean, unannotated
// grey mass, and the annotation layer lives exactly where the viewer's
// attention already is.
function renderChampionshipMarkers() {
  const field = valueField();
  const activeSchools = new Set([...state.pinned.values()].filter(e => e.type === 'team').map(e => e.id));
  if (state.hovered) activeSchools.add(state.hovered);

  const points = [];
  for (const school of activeSchools) {
    const team = teams.get(school);
    const byYear = championshipsByTeam.get(school);
    if (!team || !byYear) continue;
    for (const [year, entry] of byYear) {
      if (year < state.yearRange[0] || year > state.yearRange[1]) continue;
      const row = team.rows.find(r => r.year === year);
      if (!row || !Number.isFinite(row[field])) continue;
      points.push({ key: school + ':' + year, school, year, value: row[field], entry, color: team.color });
    }
  }

  const sel = el.focusSvg.select('.champ-field')
    .selectAll('circle.champ-marker')
    .data(points, d => d.key);
  sel.exit().remove();
  sel.enter().append('circle').attr('class', 'champ-marker')
    .attr('r', CHAMPIONSHIP_MARKER_RADIUS)
    .attr('stroke', CHAMPIONSHIP_MARKER_STROKE)
    .attr('stroke-width', 1.5)
    .style('cursor', 'default')
    .on('pointerenter', (event, d) => showChampionshipTooltip(event, d))
    .on('pointermove', (event, d) => showChampionshipTooltip(event, d))
    .on('pointerleave', hideTooltip)
    .merge(sel)
    .attr('fill', d => d.entry.vacated ? '#fff' : CHAMPIONSHIP_MARKER_FILL)
    .attr('cx', d => xFocus(d.year))
    .attr('cy', d => yFocus(d.value));
}

// ============================================================
//  TOOLTIP
// ============================================================

function showTooltip(event, team) {
  const [mx] = d3.pointer(event, el.focusSvg.node());
  const year = Math.round(xFocus.invert(mx));
  const bisect = d3.bisector(d => d.year).left;
  const idx = bisect(team.rows, year);
  const row = team.rows[Math.max(0, Math.min(team.rows.length - 1, idx))];
  if (!row) return;
  el.tooltip.style.display = 'block';
  el.tooltip.style.left = (event.pageX + 14) + 'px';
  el.tooltip.style.top = (event.pageY - 10) + 'px';
  el.tooltip.innerHTML = `
    <div class="tt-name">${team.school}</div>
    <div class="tt-conf">${row.conf} &middot; ${row.year}</div>
    <div class="tt-stat">SRS <b>${Number.isFinite(row.srs) ? row.srs.toFixed(2) : 'not rated'}</b>${Number.isFinite(row.srs_off) && Number.isFinite(row.srs_def) ? ` &nbsp; (OSRS ${row.srs_off.toFixed(1)} / DSRS ${row.srs_def.toFixed(1)})` : ''}</div>
    <div class="tt-stat">${row.wins}-${row.losses}${row.ap_rank ? ' &middot; AP #' + row.ap_rank : ''}</div>
  `;
}
function hideTooltip() { el.tooltip.style.display = 'none'; }

function showChampionshipTooltip(event, d) {
  const e = d.entry;
  el.tooltip.style.display = 'block';
  el.tooltip.style.left = (event.pageX + 14) + 'px';
  el.tooltip.style.top = (event.pageY - 10) + 'px';
  el.tooltip.innerHTML = `
    <div class="tt-name">${d.school} — ${e.vacated ? 'National Champion (title vacated)' : 'National Champion'}</div>
    <div class="tt-conf">${d.year} NCAA tournament${e.coach ? ' &middot; ' + e.coach : ''}</div>
    ${e.runner_up ? `<div class="tt-stat">def. ${e.runner_up}${e.score ? ' ' + e.score : ''}</div>` : ''}
    ${e.vacated ? '<div class="tt-conf">Won on the court; title later vacated by the NCAA.</div>' : ''}
  `;
}

// ============================================================
//  SUMMARY PANEL
// ============================================================

function renderSummary() {
  const [start, end] = state.yearRange;
  el.summaryRange.textContent = `${start}–${end}`;

  const visibleRows = [...teams.values()].flatMap(t => visibleRowsFor(t.rows));
  el.summaryAvg.textContent = visibleRows.length ? d3.mean(visibleRows, r => r.srs).toFixed(2) : '—';

  // Require presence in at least half the visible years, so a program (or
  // conference) that's only briefly in the data can't dominate a wide,
  // mostly-empty-for-them range.
  const totalYears = end - start + 1;
  const minSeasons = Math.max(1, Math.ceil(totalYears / 2));
  if (el.minSeasonsNote) {
    el.minSeasonsNote.textContent = `Min. ${minSeasons} of ${totalYears} years in view.`;
  }

  const perTeam = [...teams.values()].map(t => {
    const rows = visibleRowsFor(t.rows);
    if (rows.length < minSeasons) return null;
    return { school: t.school, color: t.color, avg: d3.mean(rows, r => r.srs), seasons: rows.length };
  }).filter(Boolean).sort((a, b) => d3.descending(a.avg, b.avg)).slice(0, TOP_N_TEAMS);

  el.topTeamsBody.innerHTML = perTeam.map((d, i) => `
    <tr>
      <td class="num">${i + 1}</td>
      <td><span class="swatch" style="background:${d.color}"></span>${d.school}</td>
      <td class="num">${d.avg.toFixed(2)}</td>
      <td class="num">${d.seasons}</td>
    </tr>
  `).join('');

  const perConf = [...conferences.values()].map(c => {
    const rows = visibleRowsFor(c.rows);
    const yearsPresent = new Set(rows.map(r => r.year)).size;
    if (yearsPresent < minSeasons) return null;
    return { conf: c.conf, color: c.color, avg: d3.mean(rows, r => r.srs) };
  }).filter(Boolean).sort((a, b) => d3.descending(a.avg, b.avg));

  el.confAvgBody.innerHTML = perConf.map(d => `
    <tr>
      <td><span class="swatch" style="background:${d.color}"></span>${d.conf}</td>
      <td class="num">${d.avg.toFixed(2)}</td>
    </tr>
  `).join('');
}

function updateReadout() {
  const n = [...teams.values()].filter(t => visibleRowsFor(t.rows).length).length;
  el.readout.textContent = `${n} programs · ${state.yearRange[0]}–${state.yearRange[1]}`;
  el.rangeLabel.textContent = `${state.yearRange[0]}–${state.yearRange[1]}`;
}

// ============================================================
//  PIN / SEARCH CONTROLS
// ============================================================

function togglePin(type, id) {
  const key = type + ':' + id;
  if (state.pinned.has(key)) {
    state.pinned.delete(key);
  } else {
    const entity = type === 'team' ? teams.get(id) : conferences.get(id);
    const label = type === 'team' ? id : id;
    state.pinned.set(key, { type, id, label, color: entity.color });
  }
  renderPinnedChips();
  renderPinned();
  renderSummary();
}

function renderPinnedChips() {
  el.pinnedChips.innerHTML = [...state.pinned.values()].map(p => `
    <span class="chip" data-key="${p.type}:${p.id}">
      <span class="swatch" style="background:${p.color}"></span>
      ${p.label}${p.type === 'conf' ? ' (conf avg)' : ''}
      <button class="chip-x" aria-label="remove">×</button>
    </span>
  `).join('');
  el.pinnedChips.querySelectorAll('.chip').forEach(chip => {
    chip.querySelector('.chip-x').addEventListener('click', () => {
      const [type, id] = chip.dataset.key.split(':');
      togglePin(type, id);
    });
  });
}

function wireSearch(inputEl, suggestEl, names, type) {
  inputEl.addEventListener('input', () => {
    const q = inputEl.value.trim().toLowerCase();
    if (!q) { suggestEl.style.display = 'none'; suggestEl.innerHTML = ''; return; }
    const matches = names.filter(n => n.toLowerCase().includes(q)).slice(0, 12);
    suggestEl.innerHTML = matches.map(n => `<div class="suggest-item" data-id="${n}">${n}</div>`).join('');
    suggestEl.style.display = matches.length ? 'block' : 'none';
    suggestEl.querySelectorAll('.suggest-item').forEach(item => {
      item.addEventListener('click', () => {
        togglePin(type, item.dataset.id);
        inputEl.value = '';
        suggestEl.style.display = 'none';
      });
    });
  });
  inputEl.addEventListener('blur', () => setTimeout(() => { suggestEl.style.display = 'none'; }, 150));
}

function wireControls() {
  el.movingAvgToggle.addEventListener('change', () => {
    state.movingAverage = el.movingAvgToggle.checked;
    render();
  });
  if (el.curatedOnlyToggle) {
    el.curatedOnlyToggle.addEventListener('change', () => {
      state.curatedOnly = el.curatedOnlyToggle.checked;
      render();
    });
  }
  el.resetBtn.addEventListener('click', () => {
    state.yearRange = [...DEFAULT_YEAR_RANGE];
    el.contextSvg.select('.brush').call(brush.move, state.yearRange.map(xContext));
  });

  wireSearch(el.teamSearch, el.teamSuggest, [...teams.keys()].sort(d3.ascending), 'team');
  wireSearch(el.confSearch, el.confSuggest, [...conferences.keys()].sort(d3.ascending), 'conf');
}

main();
