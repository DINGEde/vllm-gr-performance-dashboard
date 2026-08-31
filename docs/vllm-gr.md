# vllm-gr Performance

Daily offline model performance for `vllm-gr`. Runs that fail dataset, warmup, environment, or metric-semantics qualification remain visible and can be excluded with the qualified-only trend filter.

<div class="vgr-dashboard" id="vgr-dashboard">
  <div class="vgr-toolbar">
    <div class="vgr-control"><label for="vgr-host">Host</label><select id="vgr-host"></select></div>
    <div class="vgr-control"><label for="vgr-scenario">Scenario</label><select id="vgr-scenario"></select></div>
    <div class="vgr-control"><label for="vgr-metric">Metric</label><select id="vgr-metric"></select></div>
    <div class="vgr-control"><label for="vgr-percentile">Statistic</label><select id="vgr-percentile"></select></div>
    <label class="vgr-check"><input type="checkbox" id="vgr-qualified-only"> Qualified trend only</label>
    <p class="vgr-count" id="vgr-count"></p>
  </div>
  <div id="vgr-status"></div>
  <section class="vgr-latest" id="vgr-latest"></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Reproducibility</p><h2>Current configuration</h2></div><p>Exact parameters for the selected run.</p></div><div id="vgr-config"></div></section>
  <section class="vgr-section">
    <div class="vgr-section-head"><div><p class="vgr-kicker">Daily signal</p><h2 id="vgr-trend-title">Trend</h2></div><p id="vgr-trend-caption"></p></div>
    <div class="vgr-chart" id="vgr-trend-chart" aria-live="polite"></div>
  </section>
  <div class="vgr-split">
    <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Distribution</p><h2>Per-request primary E2E</h2></div></div><div class="vgr-chart" id="vgr-primary-chart"></div></section>
    <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Measurement</p><h2>Latency profile</h2></div></div><div id="vgr-latency-grid"></div></section>
  </div>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Beam execution</p><h2>Beam phase & prefix cache</h2></div><p>Serving-aligned wall-clock phases for the selected run.</p></div><div id="vgr-beam-profile"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Methodology</p><h2>Metric definitions</h2></div><p>How to read and compare the values.</p></div><div class="vgr-methodology"><p><strong>Offline E2E miss/hit</strong>: wall-clock time of one direct <code>GRLLM.beam_search()</code> call. Miss resets Prefix Cache first; hit immediately repeats the identical prompt. It excludes HTTP, SSE, serialization, and network round trip.</p><p><strong>Prefill miss/hit</strong>: call start through completion of token 0, matching the vllm-gr serving boundary. This includes first-step beam-session setup such as the <code>begin_session</code> path changed by PR #317. <strong>Decode common</strong>: token 1 preparation through <code>beam_search</code> return, including later engine steps, beam bookkeeping, sorting, reconstruction and detokenization. Miss/hit Decode samples are pooled into one distribution; they are repeated observations, never additive components.</p><p><strong>Decode overhead</strong>: Decode wall time minus token&gt;0 engine-step time; it is already included in Decode and is shown only as a diagnostic. <strong>Statistics</strong>: P50/P90/P95/P99 are computed across measured single-request samples. Compare runs only when phase-definition version, beam width, input length, model revision and cache state match.</p></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Evidence</p><h2>Run history</h2></div><p>Select a run to inspect its configuration and qualification.</p></div><div id="vgr-run-history"></div></section>
</div>
