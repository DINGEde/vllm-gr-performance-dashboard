# vllm-gr Performance

Daily model-serving performance for `vllm-gr`. Runs that fail dataset, warmup, environment, or metric-semantics qualification remain visible and can be excluded with the qualified-only trend filter.

<div class="vgr-dashboard" id="vgr-dashboard">
  <div class="vgr-toolbar">
    <div class="vgr-control"><label for="vgr-host">Host</label><select id="vgr-host"></select></div>
    <div class="vgr-control"><label for="vgr-metric">Metric</label><select id="vgr-metric"></select></div>
    <div class="vgr-control"><label for="vgr-percentile">Statistic</label><select id="vgr-percentile"></select></div>
    <label class="vgr-check"><input type="checkbox" id="vgr-qualified-only"> Qualified trend only</label>
    <p class="vgr-count" id="vgr-count"></p>
  </div>
  <div id="vgr-status"></div>
  <section class="vgr-latest" id="vgr-latest"></section>
  <section class="vgr-section">
    <div class="vgr-section-head"><div><p class="vgr-kicker">Daily signal</p><h2 id="vgr-trend-title">Trend</h2></div><p id="vgr-trend-caption"></p></div>
    <div class="vgr-chart" id="vgr-trend-chart" aria-live="polite"></div>
  </section>
  <div class="vgr-split">
    <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Distribution</p><h2>Per-request TTFT</h2></div></div><div class="vgr-chart" id="vgr-ttft-chart"></div></section>
    <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Measurement</p><h2>Latency profile</h2></div></div><div id="vgr-latency-grid"></div></section>
  </div>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Evidence</p><h2>Run history</h2></div><p>Select a run to inspect its configuration and qualification.</p></div><div id="vgr-run-history"></div></section>
</div>
