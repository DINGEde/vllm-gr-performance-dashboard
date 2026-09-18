// Harness for the V1 trend-series start date in the vllm-gr dashboard.
//
// The trend charts drop beam_search_v1 points dated before 2026-09-18: the
// pipeline was measured on 09-17 while its stage definitions were still being
// settled, and every core metric exists on that date, so the point would draw
// as a full series beside the 09-18 caliber and read as movement that never
// happened.
//
// The rule is one predicate that BOTH grids reach through lineChart, so the
// property worth pinning is not "a date appears in the source" but the three
// consequences that fail independently:
//
//   1. the 09-17 V1 marker is gone,
//   2. the 09-17 LEGACY marker survives, and
//   3. the 09-17 axis tick survives because the legacy point still holds it.
//
// A filter that dropped the whole date, or the whole V1 series, would break
// (2) or (3) while still satisfying (1) -- which is exactly the bug a naive
// "no V1 09-17 point" assertion would wave through.
//
// Runs are synthetic on purpose: no dependency on runs/, which grows daily.
//
// Usage: node tests/render_trend_series.mjs [--js=<path>]
//
// Exit code is non-zero when any check fails, so it doubles as the negative
// control: with the start date reverted to 2026-09-17 the V1 marker must come
// back, and this file asserts that it does.

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.join(HERE, "..");

const V1_API = "beam_search_v1";
const START_DATE = "2026-09-18";
// The literal the negative control rewrites in memory. Asserted present so a
// rename fails loudly here instead of silently disarming the control.
const START_DECLARATION = `const V1_TREND_START_DATE = "${START_DATE}";`;

const args = process.argv.slice(2);
const jsArg = args.find((arg) => arg.startsWith("--js="));
const jsPath = jsArg ? jsArg.slice("--js=".length) : path.join(REPO, "docs", "javascripts", "vllm-gr-dashboard.js");
const source = fs.readFileSync(jsPath, "utf8");

let failures = 0;
const check = (label, ok, detail = "") => {
  if (!ok) failures += 1;
  console.log(`  [${ok ? "OK  " : "FAIL"}] ${label}${detail ? `  -- ${detail}` : ""}`);
};

// --------------------------------------------------------------------------
// Load lineChart + the predicate out of the IIFE against a stub document.
// --------------------------------------------------------------------------
function loadInstrumented(code) {
  const marker = /(\n\}\)\(\);\s*)$/;
  if (!marker.test(code)) {
    throw new Error("the dashboard IIFE no longer ends with `})();` -- update this harness");
  }
  const instrumented = code.replace(
    marker,
    "\n  globalThis.__vgrTrend = { lineChart, trendSampleIsComparable, V1_TREND_START_DATE };\n})();\n",
  );
  const sandbox = {
    document: { readyState: "complete", getElementById: () => null, addEventListener: () => {} },
    window: { location: { href: "https://example.invalid/vllm-gr/" } },
    URL,
    console,
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(instrumented, context, { filename: path.basename(jsPath) });
  if (!sandbox.__vgrTrend) throw new Error("lineChart was not exported -- harness splice failed");
  return sandbox.__vgrTrend;
}

const trend = loadInstrumented(source);
check(
  "the start date is the one this harness assumes",
  trend.V1_TREND_START_DATE === START_DATE,
  `saw ${trend.V1_TREND_START_DATE}`,
);

// --------------------------------------------------------------------------
// Synthetic runs. Only the fields lineChart actually reads are populated:
// run.date for the axis, scenario.beam_api for the series, and the canonical
// latency table for the value.
// --------------------------------------------------------------------------
const runOn = (date, api, mean) => ({
  run: { id: `${date}-${api}`, date },
  scenario: api === V1_API ? { beam_api: V1_API, pipeline_version: "v1" } : { pipeline_version: "legacy" },
  results: { latency_ms: { e2el: { mean, p50: mean, p90: mean, p99: mean } } },
});

const META = { key: "e2el", label: "E2E miss", unit: "ms", measurement: "canonical" };

const markersOf = (svg) =>
  [...svg.matchAll(/<title>([^<]*)<\/title>/g)]
    .map((match) => match[1])
    .filter((title) => title.includes(" · "));
const draws = (svg, prefix) => markersOf(svg).some((title) => title.startsWith(prefix));
const emptyState = (svg) => svg.includes("vgr-empty");

console.log("\n=== the predicate itself ===");
for (const [date, api, expected] of [
  ["2026-09-17", V1_API, false],
  ["2026-09-16", V1_API, false],
  [START_DATE, V1_API, true],
  ["2026-09-19", V1_API, true],
  ["2026-09-01", "beam_search", true],
  ["2026-09-17", "beam_search", true],
  [START_DATE, "beam_search", true],
]) {
  const actual = trend.trendSampleIsComparable(runOn(date, api, 10));
  check(`${date} ${api} -> comparable`, actual === expected, `expected ${expected}, got ${actual}`);
}

// --------------------------------------------------------------------------
// The three consequences, on one chart that carries both pipelines across the
// same three dates.
// --------------------------------------------------------------------------
const runs = [
  runOn("2026-09-16", "beam_search", 60.0),
  runOn("2026-09-17", "beam_search", 61.0),
  runOn(START_DATE, "beam_search", 62.0),
  runOn("2026-09-17", V1_API, 900.0),
  runOn(START_DATE, V1_API, 63.0),
];

console.log("\n=== the drawn chart ===");
const svg = trend.lineChart(runs, "e2el", "mean", META);
check("a chart was produced", !emptyState(svg) && svg.startsWith("<svg"), svg.slice(0, 60));
check("the 09-17 V1 marker is gone", !draws(svg, `2026-09-17 · V1 ${V1_API}`));
check("the 09-17 legacy marker survives", draws(svg, "2026-09-17 · Legacy beam_search"));
check("the 09-18 V1 marker survives", draws(svg, `${START_DATE} · V1 ${V1_API}`));
check(
  "the 09-17 axis tick survives",
  svg.includes(">09-17</text>"),
  "the legacy point must keep the date on the axis",
);
check(
  "the 09-17 legacy value is the legacy one, not the V1 one",
  !svg.includes("900.0"),
  "a collapsed V1 value would mean the two series were merged",
);
check(
  "the V1 series draws no segment",
  markersOf(svg).filter((title) => title === `V1 ${V1_API}`).length <= 1,
  "one V1 point cannot make a line",
);

console.log("\n=== a scenario whose only V1 points are all too early ===");
const onlyEarly = [
  runOn("2026-09-17", V1_API, 900.0),
  runOn("2026-09-16", V1_API, 901.0),
];
const earlySvg = trend.lineChart(onlyEarly, "e2el", "mean", META);
check("falls back to the empty state", emptyState(earlySvg), earlySvg.slice(0, 70));
check("draws no marker", markersOf(earlySvg).length === 0);

console.log("\n=== legacy-only history is untouched ===");
const legacyOnly = [
  runOn("2026-09-16", "beam_search", 60.0),
  runOn("2026-09-17", "beam_search", 61.0),
  runOn(START_DATE, "beam_search", 62.0),
];
const legacySvg = trend.lineChart(legacyOnly, "e2el", "mean", META);
check("still draws", !emptyState(legacySvg));
check("keeps all three legacy markers", markersOf(legacySvg).length === 3, `${markersOf(legacySvg).length} markers`);

// --------------------------------------------------------------------------
// Negative control: revert the start date and the V1 point must reappear.
// Without this the checks above could pass on a chart that never had the point
// to begin with.
// --------------------------------------------------------------------------
console.log("\n=== negative control: start date reverted ===");
check("the declaration the control rewrites is still present", source.includes(START_DECLARATION));
const reverted = source.replace(
  START_DECLARATION,
  `const V1_TREND_START_DATE = "2026-09-17";`,
);
check("the revert actually changed the source", reverted !== source);
const revertedTrend = loadInstrumented(reverted);
const revertedSvg = revertedTrend.lineChart(runs, "e2el", "mean", META);
check(
  "the 09-17 V1 marker comes back",
  draws(revertedSvg, `2026-09-17 · V1 ${V1_API}`),
  "if it does not, the checks above were vacuous",
);
check(
  "and the 09-17 V1 value is the one that made this necessary",
  revertedSvg.includes("900.0"),
);

console.log(failures ? `\nRESULT: ${failures} FAILED` : "\nRESULT: all checks passed");
process.exit(failures ? 1 : 0);
