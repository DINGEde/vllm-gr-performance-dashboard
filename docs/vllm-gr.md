# vllm-gr Performance

Daily offline single-batch performance on GPU `L20`. The dashboard shows only the newest serving-aligned phase definition captured on or after 2026-08-31.

<div class="vgr-dashboard" id="vgr-dashboard">
  <div class="vgr-toolbar">
    <div class="vgr-control"><label for="vgr-scenario">Scenario</label><select id="vgr-scenario"></select></div>
    <div class="vgr-control"><label for="vgr-percentile">Statistic</label><select id="vgr-percentile"></select></div>
    <label class="vgr-check"><input type="checkbox" id="vgr-qualified-only"> Qualified trend only</label>
    <p class="vgr-count" id="vgr-count"></p>
  </div>
  <div id="vgr-status"></div>
  <section class="vgr-latest" id="vgr-latest"></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Reproducibility</p><h2>Current configuration</h2></div><p>Exact parameters for the selected run.</p></div><div id="vgr-config"></div></section>
  <section class="vgr-section">
    <div class="vgr-section-head"><div><p class="vgr-kicker">Daily signals</p><h2 id="vgr-trends-title">All metric trends</h2></div><p id="vgr-trends-caption"></p></div>
    <div class="vgr-trend-grid" id="vgr-trend-grid" aria-live="polite"></div>
  </section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Measurement</p><h2>Latency profile</h2></div></div><div id="vgr-latency-grid"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Beam execution</p><h2>Prefill & Decode</h2></div><p>Serving-aligned wall-clock phases for the selected run.</p></div><div id="vgr-beam-profile"></div></section>
  <section class="vgr-section vgr-pipeline-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Async mechanism · one steady-state slot</p><h2>vLLM-gr Async Decode CPU Pipeline</h2></div><p>Complete causal chain plus low-disturbance function breakdown; parent and child values are not additive.</p></div><div id="vgr-cpu-pipeline"></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Methodology</p><h2>Metric definitions</h2></div><p>How to read and compare the values.</p></div><div class="vgr-methodology"><p><strong>Offline E2E miss/hit</strong>: wall-clock time of one direct <code>GRLLM.beam_search()</code> call. Miss resets Prefix Cache first; hit immediately repeats the identical prompt. It excludes HTTP, SSE, serialization, and network round trip.</p><p><strong>Current v3 Prefill miss/hit</strong>: internal beam token-loop start through completion of token 0, aligned with the online serving metric boundary. Prefill common pools the miss/hit observations for direct comparison with the online counter average. Entry-side prompt and initial-beam preparation remains part of E2E but is outside Prefill. <strong>Decode common</strong>: token 1 preparation through <code>beam_search</code> return, including later engine steps, beam bookkeeping, sorting, reconstruction and detokenization. Miss/hit Decode samples are pooled into one distribution; they are repeated observations, never additive components.</p><p><strong>Sort</strong>: only the final <code>sorted(completed, key=lambda x: x.cum_logprob, reverse=True)</code> operation. <strong>Total Beam</strong>: the online-compatible <code>Avg Prefill + Avg Decode + Avg Sort</code> aggregate. Because Decode already spans sorting, reconstruction and detokenization, this compatibility total counts Sort twice and must not be interpreted as a non-overlapping wall-clock total.</p><p><strong>Average (Mean)</strong>: phase wall-time sum divided by its request-observation count, equivalent to <code>Δ phase_time_seconds_total ÷ Δ requests_total</code>. With N prompts, Prefill miss and Prefill hit each divide by N; Prefill common, Decode, Sort and Total Beam each pool N miss plus N hit observations and divide by 2N. The dashboard also retains P50/P90/P95/P99.</p><p>The dashboard never mixes phase versions. Until the first complete v3 matrix is published it retains the v2 matrix, whose Prefill started at the outer call boundary; the selected run always shows its exact phase version in Current configuration.</p></div></section>
  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Evidence</p><h2>Run history</h2></div><p>Select a run to inspect its configuration and qualification.</p></div><div id="vgr-run-history"></div></section>
</div>
