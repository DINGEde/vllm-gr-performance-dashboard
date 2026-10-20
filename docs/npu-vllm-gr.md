# vllm-gr NPU Performance

Daily offline single-batch performance on Ascend `910B1` NPU, measured with `offline_beam_search_v1.py` (`--constraint-backend npu --execution graph`). Each scenario keeps one point per calendar day and the snapshot retains the most recent 30 days, so the trend charts show how batch latency fluctuates across days.

<div class="vgr-dashboard" id="npu-dashboard">
  <div class="vgr-toolbar">
    <div class="vgr-control"><label for="npu-scenario">Scenario</label><select id="npu-scenario"></select></div>
    <div class="vgr-control"><label for="npu-percentile">Statistic</label><select id="npu-percentile"></select></div>
    <p class="vgr-count" id="npu-count"></p>
  </div>
  <div id="npu-status"></div>
  <section class="vgr-latest" id="npu-latest"></section>
  <section class="vgr-section">
    <div class="vgr-section-head"><div><p class="vgr-kicker">Established metrics · 30-day window</p><h2 id="npu-trends-title">Daily latency history</h2></div><p>One point per calendar day for the selected scenario; days without a measurement are simply absent.</p></div>
    <div class="vgr-trend-grid" id="npu-trend-grid" aria-live="polite"></div>
  </section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Measurement</p><h2>Latency profile</h2></div><p>Distribution of the timed beam_search_v1 call for the selected run.</p></div><div id="npu-latency-grid"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Repeatability</p><h2>Repeat spread</h2></div><p>Each timed repetition of the selected run, in measurement order.</p></div><div id="npu-repeat-figure"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Reproducibility</p><h2>Current configuration</h2></div><p>Exact parameters for the selected run.</p></div><div id="npu-config"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Methodology</p><h2>Metric definitions</h2></div><p>How to read and compare the values.</p></div><div class="vgr-methodology"><p><strong>Batch latency</strong>: wall time of one beam_search_v1 API call serving the whole batch, from call entry until output objects return. Engine startup, warmup runs, pre-call prompt preparation and post-call result inspection are excluded. The reported mean, p50, p95, min and max are taken over the timed repetitions of the run (default 5 repeats after 1 warmup).</p><p><strong>Cold prefill</strong>: prefix caching is enabled with an isolated salt per row and run, so every timed call pays a cold Prefill; latency values are not comparable to warm-cache measurements.</p><p><strong>Beam graph</strong>: the NPU beam decode graph captures fixed (batch, beam) shapes ahead of time; fallbacks count the calls that missed a captured graph and ran eager instead. A healthy run reports 0 fallbacks.</p><p><strong>Daily trend</strong>: each calendar day keeps its latest measurement per scenario. The snapshot retains 30 days; older points are dropped automatically when the snapshot is rebuilt.</p></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Evidence</p><h2>Run history</h2></div><p>Select a run to inspect its configuration.</p></div><div id="npu-run-history"></div></section>
</div>
