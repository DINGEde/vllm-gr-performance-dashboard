# vllm-gr NPU Performance (v2)

Daily offline beam-search performance on Ascend `910B1` NPU. The dashboard shows only raw.v2 results (the canonical paired cold-miss / warm-hit protocol) and aligns with the GPU dashboard's sections and metric wording. NPU device spans are a hardware-unvalidated first draft of `torch.npu.Event` timing; compare them with the GPU columns with caution.

<div class="vgr-dashboard" id="npu-v2-dashboard">
  <div class="vgr-toolbar">
    <div class="vgr-control"><label for="npu-v2-scenario">Scenario</label><select id="npu-v2-scenario"></select></div>
    <div class="vgr-control"><label for="npu-v2-percentile">Statistic</label><select id="npu-v2-percentile"></select></div>
    <p class="vgr-count" id="npu-v2-count"></p>
  </div>
  <div id="npu-v2-status"></div>
  <section class="vgr-latest" id="npu-v2-latest"></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Daily delivery</p><h2>PRs included in this daily snapshot</h2></div><p>Metric movement is shown only in the daily trend charts below.</p></div><div id="npu-v2-daily-change"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Reproducibility</p><h2>Current configuration</h2></div><p>Exact parameters for the selected run.</p></div><div id="npu-v2-config"></div></section>
  <section class="vgr-section">
    <div class="vgr-section-head"><div><p class="vgr-kicker">Selected run · NPU timeline</p><h2 id="npu-v2-miss-hit-title">Prefill and Decode Miss/Hit breakdown</h2></div><p>The selected statistic is shown for the diagnostic sample; device wait equals device span minus measured NPU compute.</p></div>
    <div id="npu-v2-miss-hit-breakdown" aria-live="polite"></div>
  </section>
  <section class="vgr-section">
    <div class="vgr-section-head"><div><p class="vgr-kicker">Established metrics</p><h2 id="npu-v2-core-trends-title">Core performance history</h2></div><p>Solid line: same measurement version. Dashed line: measurement or sampling changed; compare with caution.</p></div>
    <div class="vgr-trend-grid" id="npu-v2-core-trend-grid" aria-live="polite"></div>
  </section>
  <section class="vgr-section">
    <div class="vgr-section-head"><div><p class="vgr-kicker">NPU computation first</p><h2 id="npu-v2-diagnostic-trends-title">Compute and wait history</h2></div><p>NPU compute and device-wait metrics appear first; all earlier diagnostics remain available after them.</p></div>
    <div class="vgr-trend-grid" id="npu-v2-diagnostic-trend-grid" aria-live="polite"></div>
  </section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Measurement</p><h2>Latency profile</h2></div></div><div id="npu-v2-latency-grid"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Beam execution</p><h2>Prefill & Decode</h2></div><p>NPU device-timeline phases for the selected run.</p></div><div id="npu-v2-beam-profile"></div></section>
  <section class="vgr-section">
    <div class="vgr-section-head"><div><p class="vgr-kicker">Stage execution</p><h2>Additive stage decomposition · mean only</h2></div><p>Arithmetic means over the diagnostic sample: the decomposition is additive for means, so the Statistic selector above does not apply to this section.</p></div>
    <div id="npu-v2-stage-figure" aria-live="polite"></div>
  </section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Methodology</p><h2>Metric definitions</h2></div><p>How to read and compare the values.</p></div><div class="vgr-methodology"><p><strong>Canonical Offline E2E miss/hit</strong>: the official daily trend. Each measured pair is <code>reset → measured miss → identical measured hit</code>. The timed call uses one outer monotonic clock and a final <code>torch.npu.synchronize()</code> completion fence; output materialization happens after the clock stops. This is the difference from the legacy <em>Batch latency</em> metric, which stopped at output-object return without a device fence.</p><p><strong>NPU compute</strong>: Prefill NPU compute brackets the model-forward plus compute-logits window; Decode NPU compute brackets each Decode forward plus the sample/beam-advance window. These are a hardware-unvalidated first draft of <code>torch.npu.Event</code> timing modeled on the GPU CUDA-event code, so treat the split as indicative rather than calibrated.</p><p><strong>Prefill / Decode device spans</strong>: recorded in a post-canonical diagnostic pass after every official E2E sample has finished. The spans are additive and sum with <strong>Host overhead</strong> back to diagnostic E2E. <strong>Prefill/Decode device wait</strong> is the corresponding span minus NPU compute. <strong>Prefill dispatch wait</strong> is submit to Prefill stage entry, <strong>Prefill / Decode CPU lead</strong> is how far CPU Decode preparation leads NPU Prefill completion, and <strong>Prefill output consumed</strong> marks the later output-consumption boundary.</p><p><strong>Beam graph</strong>: the NPU beam decode graph captures fixed (batch, beam) shapes ahead of time; fallbacks count the calls that missed a captured graph and ran eager instead. A healthy run reports 0 fallbacks.</p><p><strong>Daily trend</strong>: each calendar day keeps its latest measurement per scenario. The snapshot retains 30 days; older points are dropped automatically when the snapshot is rebuilt.</p></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Evidence</p><h2>Run history</h2></div><p>Select a run to inspect its configuration.</p></div><div id="npu-v2-run-history"></div></section>
</div>
