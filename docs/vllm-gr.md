# vllm-gr Performance

Daily offline single-batch performance on GPU `L20`. The dashboard shows only offline results captured on or after 2026-09-01 and preserves established-metric history across measurement revisions. Dashed trend segments indicate measurement or sampling changes.

<div class="vgr-dashboard" id="vgr-dashboard">
  <div class="vgr-toolbar">
    <div class="vgr-control"><label for="vgr-scenario">Scenario</label><select id="vgr-scenario"></select></div>
    <div class="vgr-control"><label for="vgr-percentile">Statistic</label><select id="vgr-percentile"></select></div>
    <label class="vgr-check"><input type="checkbox" id="vgr-qualified-only"> Qualified trend only</label>
    <p class="vgr-count" id="vgr-count"></p>
  </div>
  <div id="vgr-status"></div>
  <section class="vgr-latest" id="vgr-latest"></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Daily delivery</p><h2>PRs included in this daily snapshot</h2></div><p>Metric movement is shown only in the daily trend charts below.</p></div><div id="vgr-daily-change"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Reproducibility</p><h2>Current configuration</h2></div><p>Exact parameters for the selected run.</p></div><div id="vgr-config"></div></section>
  <section class="vgr-section">
    <div class="vgr-section-head"><div><p class="vgr-kicker">Established metrics</p><h2 id="vgr-core-trends-title">Core performance history</h2></div><p>Solid line: same measurement version. Dashed line: measurement or sampling changed; compare with caution.</p></div>
    <div class="vgr-trend-grid" id="vgr-core-trend-grid" aria-live="polite"></div>
  </section>
  <section class="vgr-section">
    <div class="vgr-section-head"><div><p class="vgr-kicker">New diagnostics</p><h2 id="vgr-diagnostic-trends-title">Stage timing history</h2></div><p>Post-canonical diagnostic samples; useful for localization, not the official E2E baseline.</p></div>
    <div class="vgr-trend-grid" id="vgr-diagnostic-trend-grid" aria-live="polite"></div>
  </section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Measurement</p><h2>Latency profile</h2></div></div><div id="vgr-latency-grid"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Beam execution</p><h2>Prefill & Decode</h2></div><p>Serving-aligned wall-clock phases for the selected run.</p></div><div id="vgr-beam-profile"></div></section>
  <section class="vgr-section vgr-pipeline-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Async mechanism · one steady-state slot</p><h2>vLLM-gr Async Decode CPU Pipeline</h2></div><p>Complete causal chain plus low-disturbance function breakdown; parent and child values are not additive.</p></div><div id="vgr-cpu-pipeline"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Methodology</p><h2>Metric definitions</h2></div><p>How to read and compare the values.</p></div><div class="vgr-methodology"><p><strong>Canonical Offline E2E hit/miss</strong>: the official daily trend. Each measured pair is <code>reset → measured miss → identical measured hit</code>. The timed call uses one outer monotonic clock and a final CUDA completion fence; token digesting happens after the clock stops.</p><p><strong>Prefill / Decode</strong>: collected in a smaller post-canonical pass after every official E2E sample has finished, with Worker probes disabled. Legacy points use the legacy token-loop boundary. V1 Prefill runs from <code>submit_once</code> to entry into the first Decode stage, before Decode CPU preparation; Decode runs from that boundary through terminal <code>wait_final</code>. These are host control-flow intervals aligned at token 1, so they are comparable across the two pipelines but do not isolate GPU kernel time. <strong>Prefill output consumed</strong> is a V1-only diagnostic that extends through completion of the output-producing Prefill result and exposes look-ahead queue delay.</p><p><strong>Pipeline series</strong>: legacy <code>beam_search</code> and V1 <code>beam_search_v1</code> remain on the same metric chart with distinct lines and markers. Lines never connect different pipeline versions.</p><p><strong>Average (Mean)</strong>: arithmetic mean over the relevant canonical or stage observations. The measurement source is shown beside every chart and comparison card.</p><p><strong>Daily trend and PRs</strong>: each date runs only that day's latest <code>decode_graph</code> snapshot; the system does not rerun the preceding SHA. Trend points show PRs merged since the preceding published daily snapshot.</p></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Evidence</p><h2>Run history</h2></div><p>Select a run to inspect its configuration and qualification.</p></div><div id="vgr-run-history"></div></section>
</div>
