// Fidelity harness for the additive stage figure in the vllm-gr dashboard.
//
// The figure is ported from a standalone Python prototype, so "it renders
// without throwing" is not the property worth testing: the property worth
// testing is that the ported geometry still means what the prototype's did.
// This harness therefore re-derives every rect from the summary JSON with its
// own arithmetic and compares against the shipped JavaScript's output. A drift
// in either the domain, the endpoint table or the label thresholds shows up as
// a pixel mismatch rather than as a silently different picture.
//
// The renderer builds one HTML string and assigns it to root.innerHTML, so no
// real DOM is needed -- a stub object is enough, which keeps this runnable on a
// machine with no npm packages installed.
//
// Usage: node tests/render_stage_figure.mjs <summary.json> [--js=<path>]
//
// Exit code is non-zero when any check fails, so it doubles as the negative
// control: hand it a JavaScript file whose domain has been reverted to the
// prototype's and it must fail, reporting the Band A overflow.

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.join(HERE, "..");

// --------------------------------------------------------------------------
// Geometry, mirrored from STAGE_GEOM. Kept literal here on purpose: if the JS
// constants move, this file must be edited deliberately, not silently follow.
// --------------------------------------------------------------------------
const WIDTH = 1280;
const LABEL_W = 130;
const RIGHT = 26;
const PLOT_W = WIDTH - LABEL_W - RIGHT;
const BAND_A_TOP = 52;
const BAND_A_ROW_H = 46;
const BAND_A_GAP = 34;
const ROW_H = 34;
const ROW_GAP = 10;
const GROUP_GAP = 24;
const TITLE_H = 30;
const INLINE_MIN = 96;
const PX_TOL = 0.02;

const STATES = ["miss", "hit"];
const E2E_KEY = { miss: "e2el", hit: "e2el_hit" };

// The prototype's version of the Band A endpoints, kept verbatim so the
// negative control can assert that it is still what the JS used to do.
const PROTOTYPE_DOMAIN = "hi = Math.max(hi, prefill + decode + Math.max(overhead - dispatch, 0), e2e);";

const args = process.argv.slice(2);
const summaryPath = args.find((arg) => !arg.startsWith("--"));
const jsArg = args.find((arg) => arg.startsWith("--js="));
const expectEmpty = args.includes("--expect-empty");
if (!summaryPath) {
  console.error("usage: node tests/render_stage_figure.mjs <summary.json> [--js=<path>] [--expect-empty]");
  process.exit(2);
}
const jsPath = jsArg ? jsArg.slice("--js=".length) : path.join(REPO, "docs", "javascripts", "vllm-gr-dashboard.js");

let failures = 0;
const check = (label, ok, detail = "") => {
  if (!ok) failures += 1;
  console.log(`  [${ok ? "OK  " : "FAIL"}] ${label}${detail ? `  -- ${detail}` : ""}`);
};

// --------------------------------------------------------------------------
// Load the renderer out of the IIFE and run it against a stub root.
// --------------------------------------------------------------------------
function loadRenderer(source) {
  const marker = /(\n\}\)\(\);\s*)$/;
  if (!marker.test(source)) {
    throw new Error("the dashboard IIFE no longer ends with `})();` -- update this harness");
  }
  const instrumented = source.replace(
    marker,
    "\n  globalThis.__vgrStage = { renderStageFigure, stageFrame };\n})();\n",
  );
  const sandbox = {
    document: { readyState: "complete", getElementById: () => null, addEventListener: () => {} },
    window: { location: { href: "https://example.invalid/vllm-gr/" } },
    URL,
    console,
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(instrumented, context, { filename: path.basename(jsPath) });
  if (!sandbox.__vgrStage) throw new Error("the renderer was not exported -- harness splice failed");
  return sandbox.__vgrStage;
}

// --------------------------------------------------------------------------
// Independent model of the figure, computed from the summary alone.
// --------------------------------------------------------------------------
function model(lat) {
  const mean = (key) => {
    const value = lat?.[key]?.mean;
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  };
  const state = {};
  for (const s of STATES) {
    const d = mean(`prefill_dispatch_${s}`);
    const p = mean(`prefill_${s}`);
    const dec = mean(`decode_${s}`);
    const ho = mean(`host_overhead_${s}`);
    state[s] = {
      d, p, dec, ho,
      pg: mean(`prefill_gpu_compute_${s}`),
      pi: mean(`prefill_device_idle_${s}`),
      dg: mean(`decode_gpu_compute_${s}`),
      di: mean(`decode_device_idle_${s}`),
      lead: mean(`prefill_cpu_lead_${s}`),
      cons: mean(`prefill_output_consumed_${s}`),
      e2e: mean(E2E_KEY[s]),
      tail: Math.max(ho - d, 0),
    };
  }
  let lo = Math.min(...STATES.map((s) => -state[s].d));
  let hi = Math.max(...STATES.map((s) => Math.max(state[s].p + state[s].dec + state[s].tail, state[s].e2e)));
  const scale = PLOT_W / (hi - lo);
  const x = (value) => LABEL_W + (value - lo) * scale;

  // Band A: two rows of five segments, stacked from the origin out to E2E.
  const bandA = STATES.map((s) => {
    const st = state[s];
    return [st.pg, st.pi, st.dg, st.di, st.ho];
  });

  // Band B endpoints, one set per state.
  const ends = {};
  const durations = {};
  for (const s of STATES) {
    const st = state[s];
    ends[s] = {
      dispatch_end: -st.d,
      lead_start: st.p - st.lead,
      consumed: -st.d + st.cons,
      prefill_gpu_compute: st.pg,
      prefill: st.p,
      decode_gpu_compute: st.p + st.dg,
      decode: st.p + st.dec,
      overhead_tail: st.p + st.dec + st.tail,
    };
    durations[s] = {
      prefill_dispatch: st.d,
      prefill_cpu_lead: st.lead,
      prefill_output_consumed: st.cons,
      host_overhead_head: st.d,
      host_overhead_tail: st.tail,
      prefill_gpu_compute: st.pg,
      prefill_device_idle: st.pi,
      decode_gpu_compute: st.dg,
      decode_device_idle: st.di,
    };
  }

  // (lane, row name, duration key, start endpoint, end endpoint, projected)
  const rows = [
    ["host", "dispatch", "prefill_dispatch", "dispatch_end", 0, false],
    ["host", "look-ahead", "prefill_cpu_lead", "lead_start", "prefill", false],
    ["host", "output", "prefill_output_consumed", "dispatch_end", "consumed", false],
    ["host", "overhead", "host_overhead_head", "dispatch_end", 0, true],
    ["host", "overhead", "host_overhead_tail", "decode", "overhead_tail", true],
    ["device", "prefill", "prefill_gpu_compute", 0, "prefill_gpu_compute", false],
    ["device", "prefill", "prefill_device_idle", "prefill_gpu_compute", "prefill", false],
    ["device", "decode", "decode_gpu_compute", "prefill", "decode_gpu_compute", false],
    ["device", "decode", "decode_device_idle", "decode_gpu_compute", "decode", false],
  ];
  const resolve = (s, endpoint) => (typeof endpoint === "string" ? ends[s][endpoint] : endpoint);
  const span = (s, start, end) => {
    const a = resolve(s, start);
    const b = resolve(s, end);
    return b < a ? [b, a] : [a, b];
  };

  // Row height bonus, decided over BOTH states so the two panels stay row by
  // row comparable, and over all bars of a row (the thinnest one wins).
  const thinnest = {};
  for (const [, name, , start, end] of rows) {
    const width = Math.min(...STATES.map((s) => (span(s, start, end)[1] - span(s, start, end)[0]) * scale));
    thinnest[name] = thinnest[name] === undefined ? width : Math.min(thinnest[name], width);
  }
  const bonus = {};
  for (const name of Object.keys(thinnest)) bonus[name] = thinnest[name] < INLINE_MIN ? 18 : 0;
  const rowNames = ["dispatch", "look-ahead", "output", "overhead", "prefill", "decode"];
  const panelHeight = TITLE_H + GROUP_GAP + 30 + rowNames.reduce((sum, name) => sum + ROW_H + ROW_GAP + bonus[name], 0);
  const rowY = {};
  let y = TITLE_H;
  rowNames.forEach((name, index) => {
    if (name === "prefill") y += GROUP_GAP;
    rowY[name] = y;
    y += ROW_H + ROW_GAP + bonus[name];
    if (index === rowNames.length - 1) return;
  });

  return { mean, state, lo, hi, scale, x, bandA, rows, span, durationOf: (s, key) => durations[s][key], panelHeight, rowY, bonus };
}

// --------------------------------------------------------------------------
// Minimal parsing of the emitted SVG strings.
// --------------------------------------------------------------------------
function parseAttrs(text) {
  const attrs = {};
  for (const match of text.matchAll(/([\w:-]+)="([^"]*)"/g)) attrs[match[1]] = match[2];
  return attrs;
}

function parseRects(svg) {
  const rects = [];
  let cursor = 0;
  for (;;) {
    const at = svg.indexOf("<rect", cursor);
    if (at < 0) break;
    const close = svg.indexOf(">", at);
    const raw = svg.slice(at + 5, close);
    const selfClosing = raw.trimEnd().endsWith("/");
    let title = "";
    let next = close + 1;
    if (!selfClosing) {
      const endTag = svg.indexOf("</rect>", close);
      const inner = svg.slice(close + 1, endTag);
      const match = inner.match(/<title>([\s\S]*?)<\/title>/);
      if (match) title = match[1];
      next = endTag + "</rect>".length;
    }
    rects.push({ ...parseAttrs(raw), title });
    cursor = next;
  }
  return rects;
}

function decimalsOf(text) {
  const dot = text.indexOf(".");
  return dot < 0 ? 0 : text.length - dot - 1;
}

// --------------------------------------------------------------------------
// Run.
// --------------------------------------------------------------------------
const summary = JSON.parse(fs.readFileSync(summaryPath, "utf8"));
const run = { results: summary.results };
const lat = run.results?.diagnostic?.latency_ms;
const source = fs.readFileSync(jsPath, "utf8");
console.log(`harness: ${path.relative(REPO, summaryPath)}`);
console.log(`renderer: ${path.relative(REPO, jsPath)}`);
console.log(`prototype domain line present: ${source.includes(PROTOTYPE_DOMAIN)}`);
console.log("");

const { renderStageFigure } = loadRenderer(source);
const root = { innerHTML: "" };
renderStageFigure(root, run);
const html = root.innerHTML;

if (expectEmpty) {
  // The fallback guards are as load-bearing as the drawing code: 113 of the 114
  // runs in the published payload cannot be drawn, so this asserts that they
  // degrade to prose rather than to a half-built figure.
  const empty = html.includes("vgr-empty") && !html.includes("<svg");
  check("renders an empty state and no figure", empty, html.slice(0, 200).replace(/\s+/g, " "));
  check("the empty state explains itself", /caliber|additivity gate|No run selected/.test(html));
  console.log(failures ? `\nRESULT: ${failures} FAILED` : "\nRESULT: all checks passed");
  process.exit(failures ? 1 : 0);
}

if (!lat) {
  console.error(`FAIL: ${summaryPath} has no results.diagnostic.latency_ms -- pick a v5-caliber run`);
  process.exit(1);
}
if (html.includes("vgr-empty")) {
  console.error("FAIL: the figure degraded to an empty state instead of drawing");
  console.error(html.slice(0, 400));
  process.exit(1);
}

const m = model(lat);
const svgs = html.match(/<svg\b[\s\S]*?<\/svg>/g) || [];
const gridXsPerSvg = svgs.map((svg) =>
  [...svg.matchAll(/<line x1="([\d.-]+)"[^/]*class="vgr-grid-line"/g)].map((x) => Number(x[1])));
const zeroXs = svgs.map((svg) =>
  Number((svg.match(/<line x1="([\d.-]+)"[^/]*class="vgr-stage-zero"/) || [])[1]));

console.log("=== structure ===");
check("three figures emitted", svgs.length === 3, `${svgs.length} svg`);
const bandARects = svgs[0] ? parseRects(svgs[0]) : [];
const missRects = svgs[1] ? parseRects(svgs[1]) : [];
const hitRects = svgs[2] ? parseRects(svgs[2]) : [];
check("Band A has 10 bars (5 segments x 2 rows)",
  bandARects.filter((r) => r.title).length === 10, `${bandARects.filter((r) => r.title).length}`);
check("miss panel has 9 bars + 1 background",
  missRects.filter((r) => r.title).length === 9 && missRects.length === 10, `${missRects.length} rect`);
check("hit panel has 9 bars + 1 background",
  hitRects.filter((r) => r.title).length === 9 && hitRects.length === 10, `${hitRects.length} rect`);
check("no CJK anywhere in the rendered figure", !/[一-鿿]/.test(html));
check("no placeholder tokens", !/undefined|NaN|Infinity|\[object Object\]/.test(html));
check("`≈` survived the encoding round trip", html.includes("≈") || html.includes("&asymp;"));

console.log("\n=== shared ruler ===");
check("three zero lines emitted", zeroXs.length === 3, `${zeroXs.length}`);
check("zero line sits at x(0)", zeroXs.every((v) => Math.abs(v - m.x(0)) < PX_TOL),
  `js ${zeroXs.join(",")} vs model ${m.x(0).toFixed(2)}`);
check("the three zero lines agree", Math.max(...zeroXs) - Math.min(...zeroXs) < PX_TOL);
const [bandAGrid, missGrid, hitGrid] = gridXsPerSvg;
check("tick grids agree across all three figures",
  bandAGrid.length > 2 && missGrid.length === bandAGrid.length && hitGrid.length === bandAGrid.length
  && bandAGrid.every((v, i) => Math.abs(v - missGrid[i]) < PX_TOL && Math.abs(v - hitGrid[i]) < PX_TOL),
  `${gridXsPerSvg.map((grid) => grid.length).join(" / ")} ticks`);
check("tick spacing is a multiple of 10 ms on the shared ruler",
  bandAGrid.every((v, i) => i === 0 || Math.abs((v - bandAGrid[i - 1]) - 10 * m.scale) < PX_TOL),
  `${bandAGrid.length} ticks, step ${bandAGrid.length > 1 ? (10 * m.scale).toFixed(2) : "n/a"}px`);

console.log("\n=== Band A geometry ===");
let cursor = m.x(0);
STATES.forEach((state, rowIndex) => {
  m.bandA[rowIndex].forEach((value, segmentIndex) => {
    const rect = bandARects[rowIndex * 5 + segmentIndex];
    if (!rect) {
      check(`band A ${state} segment ${segmentIndex} present`, false);
      return;
    }
    const expectedWidth = Math.max(value * m.scale, 0.6);
    check(`band A ${state} segment ${segmentIndex} x/width`,
      Math.abs(Number(rect.x) - cursor) < PX_TOL && Math.abs(Number(rect.width) - expectedWidth) < PX_TOL,
      `js x=${rect.x} w=${rect.width} vs ${cursor.toFixed(2)}/${expectedWidth.toFixed(2)}`);
    // fmt() without a digit count is adaptive, so the tolerance comes from the
    // number of decimals that were actually printed.
    const printed = (rect.title.match(/: ([\d.-]+) ms$/) || [])[1];
    check(`band A ${state} segment ${segmentIndex} title`,
      printed !== undefined
      && Math.abs(Number(printed) - value) <= 0.5 * 10 ** -decimalsOf(printed) + 1e-6,
      rect.title);
    cursor += expectedWidth;
  });
  const drawn = Number(bandARects[rowIndex * 5 + 4].x) + Number(bandARects[rowIndex * 5 + 4].width);
  check(`band A ${state} row closes at x(e2e)`,
    Math.abs(drawn - m.x(m.state[state].e2e)) < PX_TOL,
    `${drawn.toFixed(2)} vs ${m.x(m.state[state].e2e).toFixed(2)}`);
  cursor = m.x(0);
});

console.log("\n=== Band B geometry ===");
for (const [panelIndex, state] of STATES.entries()) {
  const rects = panelIndex === 0 ? missRects : hitRects;
  const bars = rects.filter((r) => r.title);
  check(`${state} panel viewBox height`,
    Number((svgs[panelIndex + 1].match(/viewBox="0 0 1280 ([\d.]+)"/) || [])[1]) === m.panelHeight,
    `js ${(svgs[panelIndex + 1].match(/viewBox="0 0 1280 ([\d.]+)"/) || [])[1]} vs model ${m.panelHeight}`);
  m.rows.forEach(([, name, key, start, end, projected], index) => {
    const rect = bars[index];
    if (!rect) {
      check(`${state}/${key} present`, false);
      return;
    }
    const [a, b] = m.span(state, start, end);
    const expectedWidth = Math.max((b - a) * m.scale, 1);
    check(`${state}/${key} x/width`,
      Math.abs(Number(rect.x) - m.x(a)) < PX_TOL && Math.abs(Number(rect.width) - expectedWidth) < PX_TOL,
      `js x=${rect.x} w=${rect.width} vs ${m.x(a).toFixed(2)}/${expectedWidth.toFixed(2)}`);
    check(`${state}/${key} row y`,
      Math.abs(Number(rect.y) - m.rowY[name]) < 0.01,
      `js y=${rect.y} vs model ${m.rowY[name]}`);
    check(`${state}/${key} projected flag`,
      (rect.class || "").split(/\s+/).includes("vgr-stage-projected") === projected,
      `js ${rect.class || "(none)"} vs model ${projected}`);
    const printed = (rect.title.match(/ = ([\d.-]+) ms$/) || [])[1];
    const tol = 0.5 * 10 ** -decimalsOf(printed || "0") + 1e-6;
    check(`${state}/${key} title value`,
      Math.abs(Number(printed) - m.durationOf(state, key)) <= tol, `${rect.title} vs ${m.durationOf(state, key)}`);
  });
}

console.log("\n=== additivity and clipping ===");
for (const state of STATES) {
  const st = m.state[state];
  check(`${state} prefill split closes`, Math.abs(st.p - (st.pg + st.pi)) < 1e-6);
  check(`${state} decode split closes`, Math.abs(st.dec - (st.dg + st.di)) < 1e-6);
  check(`${state} e2e closes`, Math.abs(st.e2e - (st.p + st.dec + st.ho)) < 1e-6);
}
const allRects = [...bandARects, ...missRects, ...hitRects];
const rightmost = Math.max(...allRects.map((r) => Number(r.x) + Number(r.width)));
check("nothing is drawn past the plot's right edge", rightmost <= WIDTH - RIGHT + 0.05,
  `rightmost ${rightmost.toFixed(2)} vs limit ${(WIDTH - RIGHT).toFixed(2)}`
  + (rightmost > WIDTH - RIGHT + 0.05
    ? ` -- Band A overflows the viewBox by ${(rightmost - (WIDTH - RIGHT)).toFixed(2)}px and the last segment is clipped away`
    : ""));

console.log("\n=== anchor table ===");
const tbody = (html.match(/<tbody>([\s\S]*?)<\/tbody>/) || [])[1] || "";
const anchorRows = [...tbody.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((r) => r[1]);
check("ten anchor rows", anchorRows.length === 10, `${anchorRows.length}`);
check("no script line numbers survive",
  !/脚本/.test(tbody) && !/L\d{2,4}/.test(tbody) && !/execute:\d|\bsample:\d/.test(tbody));
const anchorKeys = ["prefill_dispatch", "prefill_cpu_lead", "prefill_output_consumed", "host_overhead",
  "prefill", "prefill_gpu_compute", "prefill_device_idle", "decode", "decode_gpu_compute", "decode_device_idle"];
anchorKeys.forEach((key, index) => {
  // cells[0] is the lane; the two means follow it.
  const cells = [...anchorRows[index].matchAll(/<td>([\s\S]*?)<\/td>/g)].map((c) => c[1]);
  const ok = cells.length === 6
    && Math.abs(Number(cells[1]) - m.mean(`${key}_miss`)) < 5e-4
    && Math.abs(Number(cells[2]) - m.mean(`${key}_hit`)) < 5e-4;
  check(`anchor row ${key} means`, ok, `${cells[1]} / ${cells[2]} vs ${m.mean(`${key}_miss`)} / ${m.mean(`${key}_hit`)}`);
});

console.log(failures ? `\nRESULT: ${failures} FAILED` : "\nRESULT: all checks passed");
process.exit(failures ? 1 : 0);
