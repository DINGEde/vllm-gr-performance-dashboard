(function () {
  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function number(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function fmt(value, digits) {
    const numeric = number(value);
    if (numeric === null) return "N/A";
    if (digits !== undefined) return numeric.toFixed(digits);
    if (Math.abs(numeric) >= 1000) return numeric.toFixed(0);
    if (Math.abs(numeric) >= 100) return numeric.toFixed(1);
    if (Math.abs(numeric) >= 10) return numeric.toFixed(2);
    return numeric.toFixed(3);
  }

  function metricValue(run, metric, percentile) {
    return number(run.results?.latency_ms?.[metric]?.[percentile]);
  }

  function scenarioKey(run) {
    return run.scenario?.key || `beam${run.scenario?.n ?? "unknown"}-legacy`;
  }

  function statusBadge(run) {
    if (run.run.trend_eligible) return '<span class="vgr-badge is-good">Trend qualified</span>';
    if (run.run.status === "success") return '<span class="vgr-badge is-warning">Visible, not qualified</span>';
    return '<span class="vgr-badge is-danger">Invalid run</span>';
  }

  function kpi(label, value, suffix, hint) {
    return `<div class="vgr-kpi"><p>${escapeHtml(label)}</p><strong>${escapeHtml(value)}${suffix ? ` <small>${escapeHtml(suffix)}</small>` : ""}</strong>${hint ? `<span>${escapeHtml(hint)}</span>` : ""}</div>`;
  }

  function renderLatest(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No runs match the current filters.</div>';
      return;
    }
    const mode = run.scenario.execution_mode || "offline";
    const latency = run.results.latency_ms || {};
    const ttft = latency.ttft;
    const e2el = latency.e2el;
    const requests = run.results.requests;
    const reasons = run.run.qualification_reasons || [];
    const primaryKpis = [
      ["Avg Offline E2E miss", e2el, "direct GRLLM call after cache reset"],
      ["Avg Offline E2E hit", latency.e2el_hit, "identical prompt repeated immediately"],
      ["Avg Prefill Time", latency.prefill, "all miss/hit Prefill observations"],
      ["Avg Prefill miss", latency.prefill_miss, "internal Prefill boundary, cold prefix"],
      ["Avg Prefill hit", latency.prefill_hit, "internal Prefill boundary, warm prefix"],
      ["Avg Decode common", latency.decode || latency.decode_miss, "all miss/hit Decode observations"],
      ["Avg Sort Time", latency.sort, "final completed-beam sorted() call only"],
      ["Total Beam Time", latency.total_beam, "compatible Prefill + Decode + Sort aggregate"],
    ].map(([label, value, hint]) => kpi(label, fmt(value?.mean), "ms", value ? `P50 ${fmt(value.p50)} ms · P90 ${fmt(value.p90)} ms · ${hint}` : hint)).join("");
    root.innerHTML = `
      <div class="vgr-hero-copy">
        <div class="vgr-hero-label">${statusBadge(run)}<span>${escapeHtml(run.run.date)} · GPU L20</span></div>
        <h2>${escapeHtml(run.scenario.name)}</h2>
        <p>${escapeHtml(run.model.id)} · ${escapeHtml(run.dataset.name)}</p>
        <div class="vgr-tags">
          <span>${escapeHtml(run.dataset.kind)}</span>
          <span>${escapeHtml(mode)} mode</span>
          <span>concurrency ${escapeHtml(run.scenario.max_concurrency)}</span>
          <span>beam n=${escapeHtml(run.scenario.n)}</span>
          <span>input ${escapeHtml(run.scenario.input_tokens_target ?? "dataset")} tokens</span>
          <span>${escapeHtml(requests.completed)} passed / ${escapeHtml(requests.failed)} failed</span>
        </div>
      </div>
      <div class="vgr-kpi-grid">
        ${primaryKpis}
      </div>
      ${reasons.length ? `<div class="vgr-qualification"><strong>Excluded from baseline</strong><ul>${reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul></div>` : ""}
    `;
  }

  function renderConfig(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const scenario = run.scenario || {};
    const args = scenario.server_args || {};
    const benchmark = scenario.benchmark_args || {};
    const rows = [
      ["Execution", scenario.execution_mode || "online"],
      ["GPU", "L20"],
      ["Source", `${run.source.branch || "unknown"} @ ${run.source.git_sha.slice(0, 8)}`],
      ["Model", run.model.id],
      ["Dataset", `${run.dataset.name} / ${run.dataset.task}`],
      ["Beam width", scenario.n],
      ["Input length", `${scenario.input_tokens_target} tokens`],
      ["Output length", benchmark.max_tokens == null ? "model config" : `${benchmark.max_tokens} tokens per returned beam`],
      ["Measured / warmup", `${scenario.num_prompts} / ${scenario.warmup_requests}`],
      ["Concurrency", scenario.max_concurrency],
      ["Attention backend", args.attention_backend || "server default"],
      ["Beam decode graph", args.beam_graph_enabled === true ? `enabled · exact width ${args.beam_max_width}` : "disabled / eager"],
      ["Max sequences", args.max_num_seqs],
      ["Max batched tokens", args.max_num_batched_tokens],
      ["Cache protocol", benchmark.cache_protocol || "reset once after warmup"],
      ["Phase definition", benchmark.phase_definition?.version || "legacy"],
      ["GPU", `${run.environment.gpu.name} · ${run.environment.gpu.memory_mib} MiB`],
    ].filter(([, value]) => value !== undefined && value !== null);
    root.innerHTML = `<dl class="vgr-config-grid">${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
  }

  function yDomain(values) {
    if (!values.length) return { min: 0, max: 1 };
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (min === max) {
      const pad = Math.abs(min) * 0.15 || 1;
      min = Math.max(0, min - pad);
      max += pad;
    } else {
      const pad = (max - min) * 0.12;
      min = Math.max(0, min - pad);
      max += pad;
    }
    return { min, max };
  }

  function lineChart(runs, metric, percentile, meta) {
    const points = runs
      .map((run) => ({ run, value: metricValue(run, metric, percentile) }))
      .filter((point) => point.value !== null);
    if (!points.length) return '<div class="vgr-empty">No values are available for this selection.</div>';

    const width = 1040;
    const height = 330;
    const left = 72;
    const right = 28;
    const top = 28;
    const bottom = 58;
    const plotW = width - left - right;
    const plotH = height - top - bottom;
    const domain = yDomain(points.map((point) => point.value));
    const x = (index) => left + (points.length === 1 ? plotW / 2 : (index / (points.length - 1)) * plotW);
    const y = (value) => top + (1 - (value - domain.min) / (domain.max - domain.min)) * plotH;
    const grid = [];
    for (let tick = 0; tick <= 4; tick += 1) {
      const value = domain.max - ((domain.max - domain.min) * tick) / 4;
      const yy = top + (plotH * tick) / 4;
      grid.push(`<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" class="vgr-grid-line"/>`);
      grid.push(`<text x="${left - 12}" y="${yy + 4}" text-anchor="end" class="vgr-axis-label">${escapeHtml(fmt(value))}</text>`);
    }
    const polyline = points.length > 1
      ? `<polyline points="${points.map((point, index) => `${x(index)},${y(point.value)}`).join(" ")}" class="vgr-trend-line"/>`
      : "";
    const marks = points.map((point, index) => {
      const label = point.run.run.date.slice(5);
      return `<g class="vgr-point"><circle cx="${x(index)}" cy="${y(point.value)}" r="6"><title>${escapeHtml(point.run.run.date)} · ${escapeHtml(fmt(point.value))} ${escapeHtml(meta.unit)}</title></circle><text x="${x(index)}" y="${height - 28}" text-anchor="middle" class="vgr-axis-label">${escapeHtml(label)}</text><text x="${x(index)}" y="${y(point.value) - 13}" text-anchor="middle" class="vgr-value-label">${escapeHtml(fmt(point.value))}</text></g>`;
    });
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(meta.label)} ${escapeHtml(percentile)} daily trend"><text x="18" y="${top + plotH / 2}" transform="rotate(-90 18 ${top + plotH / 2})" text-anchor="middle" class="vgr-axis-title">${escapeHtml(meta.label)} (${escapeHtml(meta.unit)})</text>${grid.join("")}${polyline}${marks.join("")}<text x="${left + plotW / 2}" y="${height - 4}" text-anchor="middle" class="vgr-axis-title">Run date</text></svg>`;
  }

  function renderTrendGrid(root, runs, percentile, metrics) {
    root.innerHTML = metrics.map((meta) => `
      <article class="vgr-trend-card">
        <div class="vgr-trend-card-head"><strong>${escapeHtml(meta.label)}</strong><span>${escapeHtml(percentile.toUpperCase())} · ${escapeHtml(meta.unit)}</span></div>
        <div class="vgr-chart">${lineChart(runs, meta.key, percentile, meta)}</div>
      </article>
    `).join("");
  }

  function renderLatencyGrid(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const labels = { e2el: "E2E miss", e2el_hit: "E2E hit", prefill: "Prefill common (miss/hit)", prefill_miss: "Prefill miss", prefill_hit: "Prefill hit", decode: "Decode common (token 1+)", sort: "Sort (final completed beams)", total_beam: "Total Beam (compatible sum)" };
    const preferred = ["e2el", "e2el_hit", "prefill", "prefill_miss", "prefill_hit", "decode", "sort", "total_beam"];
    const available = preferred.filter((key) => run.results.latency_ms[key]);
    root.innerHTML = `<div class="vgr-latency-cards">${available.map((key) => {
      const value = run.results.latency_ms[key];
      return `<article class="vgr-latency-card"><div><strong>${escapeHtml(labels[key] || key)}</strong></div><dl><dt>Mean</dt><dd>${fmt(value.mean)} ms</dd><dt>P50</dt><dd>${fmt(value.p50)} ms</dd><dt>P90</dt><dd>${fmt(value.p90)} ms</dd><dt>P95</dt><dd>${fmt(value.p95)} ms</dd><dt>P99</dt><dd>${fmt(value.p99)} ms</dd></dl></article>`;
    }).join("")}</div>`;
  }

  function renderBeamProfile(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const latency = run.results.latency_ms || {};
    const states = [
        ["Cold / miss average", latency.prefill_miss, latency.decode, latency.e2el],
        ["Warm / hit average", latency.prefill_hit, latency.decode, latency.e2el_hit],
      ];
      root.innerHTML = `<div class="vgr-beam-profile-grid">${states.map(([label, prefill, decode, e2e]) => {
        const parts = [["Avg Prefill", prefill?.mean, "is-prefill"], ["Avg Decode common", decode?.mean, "is-decode"]];
        const total = parts.reduce((sum, part) => sum + (number(part[1]) || 0), 0);
        const segments = parts.map(([partLabel, value, className]) => `<span class="${className}" style="width:${total ? 100 * value / total : 0}%" title="${escapeHtml(partLabel)}: ${fmt(value)} ms"></span>`).join("");
        return `<article class="vgr-profile-card"><div class="vgr-profile-title"><strong>${escapeHtml(label)}</strong><span>${fmt(e2e?.mean)} ms Avg E2E</span></div><div class="vgr-stack-bar">${segments}</div><div class="vgr-profile-legend">${parts.map(([partLabel, value, className]) => `<span><i class="${className}"></i>${escapeHtml(partLabel)} <strong>${fmt(value)} ms</strong></span>`).join("")}</div></article>`;
      }).join("")}</div>`;
  }

  function renderCpuPipeline(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const latency = run.results.latency_ms || {};
    const stageKeys = ["cpu_prepare", "cpu_decision", "cpu_eos", "cpu_topk", "cpu_materialize"];
    if (!stageKeys.some((key) => number(latency[key]?.mean) !== null)) {
      root.innerHTML = '<div class="vgr-empty">This run predates CPU-stage instrumentation. Select a newer run to inspect the pipeline.</div>';
      return;
    }
    const mean = (key) => number(latency[key]?.mean) || 0;
    const pairMean = (left, right) => {
      const values = [number(latency[left]?.mean), number(latency[right]?.mean)].filter((value) => value !== null);
      return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
    };
    const entry = pairMean("beam_entry_overhead_miss", "beam_entry_overhead_hit");
    const prefill = mean("prefill");
    const decode = mean("decode");
    const enginePrefillMiss = mean("engine_prefill_miss");
    const enginePrefillHit = mean("engine_prefill_hit");
    const engineDecode = pairMean("engine_decode_miss", "engine_decode_hit");
    const sort = mean("sort");
    const detail = run.results.cpu_pipeline_detail;
    const detailMetrics = detail?.metrics || {};
    const detailMetric = (key) => detailMetrics[key] || null;
    const detailMean = (key) => number(detailMetric(key)?.wall_mean_ms);
    const detailCpuMean = (key) => number(detailMetric(key)?.thread_cpu_mean_ms);
    const detailCount = (key) => number(detailMetric(key)?.count);
    const stages = [
      ["Prepare next step", mean("cpu_prepare"), "Build EngineCoreRequest / BeamRequestStepUpdate"],
      ["Decision", mean("cpu_decision"), "Worker decision extraction or flat-logprobs parsing"],
      ["EOS", mean("cpu_eos"), "Materialize completed EOS candidates"],
      ["Top-k", mean("cpu_topk"), mean("cpu_topk") === 0 ? "Worker-decision path: frontend top-k skipped" : "Frontend top-k selection"],
      ["Materialize", mean("cpu_materialize"), "Build surviving beams and fork mapping"],
    ];
    const measuredCpu = stages.reduce((sum, stage) => sum + stage[1], 0) + sort;
    const decodeOverhead = Math.max(0, decode - engineDecode);
    const residual = Math.max(0, decodeOverhead - measuredCpu);
    const coverage = decodeOverhead > 0 ? 100 * measuredCpu / decodeOverhead : 0;
    const stageNode = ([label, value, hint], className = "") => `<div class="vgr-pipe-node ${className}" title="${escapeHtml(hint)}"><span>${escapeHtml(label)}</span><strong>${fmt(value)} ms</strong><small>${escapeHtml(hint)}</small></div>`;
    const executeChildren = [
      ["prepare_inputs", "prepare_inputs", "Request ordering, index maps, positions and async H2D copies"],
      ["prepare_attn_buffers", "prepare_attn buffers", "Gather block tables and compute slot mappings"],
      ["prepare_attn_metadata", "prepare_attn metadata", "Build backend attention metadata"],
      ["model_state_prepare_inputs", "model-state inputs", "Build model-specific input kwargs"],
      ["run_fullgraph", "run_fullgraph", "CPU CUDA-graph launch/replay wrapper; not GPU kernel time"],
    ];
    const hottestExecuteKey = executeChildren.reduce((best, row) => (detailMean(row[0]) || -1) > (detailMean(best) || -1) ? row[0] : best, executeChildren[0][0]);
    const executeDetailNode = ([key, label, hint]) => {
      const wall = detailMean(key);
      const cpu = detailCpuMean(key);
      const count = detailCount(key);
      const hot = key === hottestExecuteKey && wall !== null ? " is-hot" : "";
      return `<div class="vgr-pipe-node is-detail${hot}" title="${escapeHtml(hint)}"><span>${escapeHtml(label)}${hot ? '<b>optimization focus</b>' : ''}</span><strong>${wall === null ? "n/a" : `${fmt(wall)} ms`}</strong><small>thread CPU ${cpu === null ? "n/a" : `${fmt(cpu)} ms`} · n=${count === null ? "0" : count}</small></div>`;
    };
    const executeParent = detailMean("execute_model");
    const executeResidual = number(detail?.execute_model?.residual_wall_mean_ms);
    const executeCoverage = number(detail?.execute_model?.coverage_percent);
    const sampleParent = detailMean("sample_tokens");
    const sampleChildrenTotal = ["sample", "postprocess"].reduce((sum, key) => sum + (detailMean(key) || 0), 0);
    const sampleResidual = sampleParent === null ? null : Math.max(0, sampleParent - sampleChildrenTotal);

    root.innerHTML = `
      <div class="vgr-pipeline-summary">
        ${kpi("Decode wall time", fmt(decode), "ms", "token 1 through return")}
        ${kpi("Engine-step envelope", fmt(engineDecode), "ms", "miss/hit Mean averaged")}
        ${kpi("Measured CPU control", fmt(measuredCpu), "ms", "five control stages + final Sort")}
        ${kpi(detail ? "execute_model coverage" : "CPU coverage", fmt(detail ? executeCoverage : coverage, 1), "%", detail ? "listed child wall total / execute_model parent" : "measured CPU / Decode overhead")}
      </div>
      <div class="vgr-pipeline-scroll">
        <div class="vgr-pipeline">
          <div class="vgr-pipe-lane-label"><strong>GRLLM driver</strong><span>request i</span></div>
          <div class="vgr-pipe-lane vgr-pipe-lane-driver">
            ${stageNode(["Beam entry", entry, "Direct call to internal token loop"], "is-entry")}
            ${stageNode(["Prefill token 0", prefill, "Common miss/hit Prefill wall time"], "is-prefill")}
            <div class="vgr-pipe-loop-label">Decode loop · token 1+</div>
          </div>

          <div class="vgr-pipe-lane-label"><strong>EngineCore / GPU</strong><span>execution envelope</span></div>
          <div class="vgr-pipe-lane vgr-pipe-lane-engine">
            ${stageNode(["ADD_BATCH / Prefill", enginePrefillMiss, `Miss ${fmt(enginePrefillMiss)} ms · Hit ${fmt(enginePrefillHit)} ms`], "is-engine")}
            ${stageNode(["Engine decode steps", engineDecode, "Engine-step wall-time envelope; not pure GPU kernel time"], "is-engine is-wide")}
          </div>

          <div class="vgr-pipe-lane-label"><strong>execute_model CPU parent</strong><span>${detail ? `${fmt(executeParent)} ms / call` : "awaiting lightweight probe"}</span></div>
          <div class="vgr-pipe-lane vgr-pipe-lane-detail">
            ${detail ? executeChildren.map(executeDetailNode).join("") + stageNode(["Residual", executeResidual, "execute_model parent minus listed sequential child totals"], "is-residual") : '<div class="vgr-pipe-detail-empty">Fine-grained worker timing starts with the next instrumented run. No profiler is used.</div>'}
          </div>

          <div class="vgr-pipe-lane-label"><strong>sample_tokens CPU parent</strong><span>${detail ? `${fmt(sampleParent)} ms / call` : "awaiting lightweight probe"}</span></div>
          <div class="vgr-pipe-lane vgr-pipe-lane-detail">
            ${detail ? executeDetailNode(["sample", "sample", "compute_logits and beam sampler wrapper"]) + executeDetailNode(["postprocess", "postprocess", "Update request state after sampling"]) + stageNode(["Residual", sampleResidual, "sample_tokens parent minus sample and postprocess wrappers; includes async output setup and prompt logprobs"], "is-residual is-wide") : '<div class="vgr-pipe-detail-empty">The same aggregate-only probe will split sample and postprocess after the next run.</div>'}
          </div>

          <div class="vgr-pipe-lane-label"><strong>Beam CPU control</strong><span>accumulated over token 1+</span></div>
          <div class="vgr-pipe-lane vgr-pipe-lane-cpu">
            ${stages.map((stage) => stageNode(stage, "is-cpu")).join("")}
          </div>

          <div class="vgr-pipe-lane-label"><strong>Finalization</strong><span>request i result</span></div>
          <div class="vgr-pipe-lane vgr-pipe-lane-final">
            ${stageNode(["Final Sort", sort, "sorted(completed, key=cum_logprob, reverse=True)"], "is-sort")}
            ${stageNode(["Residual / uninstrumented", residual, "Decode overhead not covered by current CPU stage probes"], "is-residual is-wide")}
            <div class="vgr-pipe-node is-output"><span>Return beams</span><strong>ready</strong><small>includes reconstruction and detokenize</small></div>
          </div>
        </div>
      </div>
      <div class="vgr-pipeline-legend">
        <span><i class="is-engine"></i>Engine wall-time envelope</span>
        <span><i class="is-cpu"></i>Direct CPU timer</span>
        <span><i class="is-residual"></i>Not yet attributed</span>
        <span>Detailed values are per-call Mean wall time; thread CPU is shown separately. ${detail ? `Probe files ${detail.source_files} · hot-path I/O ${detail.hot_path_io ? "on" : "off"} · perturbation gate ${detail.perturbation_validation?.status || "unknown"}.` : ""} Box widths are schematic.</span>
      </div>
    `;
  }

  function renderRunHistory(root, runs, selectedId, onSelect) {
    if (!runs.length) {
      root.innerHTML = '<div class="vgr-empty">No runs match the current filters.</div>';
      return;
    }
    root.innerHTML = `<div class="vgr-run-list">${runs.slice().reverse().map((run) => {
      const active = run.run.id === selectedId ? " is-active" : "";
      const reasons = run.run.qualification_reasons || [];
      return `<button type="button" class="vgr-run-row${active}" data-run-id="${escapeHtml(run.run.id)}"><span class="vgr-run-date">${escapeHtml(run.run.date)}</span><span class="vgr-run-main"><strong>${escapeHtml(run.scenario.name)}</strong><small>${escapeHtml(run.source.git_sha.slice(0, 8))} · ${escapeHtml(run.dataset.kind)} · GPU L20</small></span><span class="vgr-run-result">${escapeHtml(run.results.requests.completed)}/${escapeHtml(run.scenario.num_prompts)}<small>${reasons.length ? `${reasons.length} qualification flags` : "qualified"}</small></span>${statusBadge(run)}</button>`;
    }).join("")}</div>`;
    root.querySelectorAll(".vgr-run-row").forEach((button) => {
      button.addEventListener("click", () => onSelect(button.getAttribute("data-run-id")));
    });
  }

  function initDashboard(data) {
    const root = document.getElementById("vgr-dashboard");
    if (!root) return;
    const scenarioSelect = document.getElementById("vgr-scenario");
    const percentileSelect = document.getElementById("vgr-percentile");
    const qualifiedOnly = document.getElementById("vgr-qualified-only");
    const count = document.getElementById("vgr-count");
    const latest = document.getElementById("vgr-latest");
    const trendGrid = document.getElementById("vgr-trend-grid");
    const trendsTitle = document.getElementById("vgr-trends-title");
    const trendsCaption = document.getElementById("vgr-trends-caption");
    const latencyGrid = document.getElementById("vgr-latency-grid");
    const beamProfile = document.getElementById("vgr-beam-profile");
    const cpuPipeline = document.getElementById("vgr-cpu-pipeline");
    const config = document.getElementById("vgr-config");
    const history = document.getElementById("vgr-run-history");
    let selectedId = data.runs.length ? data.runs[data.runs.length - 1].run.id : null;

    scenarioSelect.innerHTML = ['<option value="all">All scenarios</option>', ...(data.scenarios || []).map((scenario) => `<option value="${escapeHtml(scenario.key)}">${escapeHtml(scenario.label)}</option>`)].join("");
    if ((data.scenarios || []).length) scenarioSelect.value = data.scenarios[data.scenarios.length - 1].key;
    percentileSelect.innerHTML = data.percentiles.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item.toUpperCase())}</option>`).join("");
    percentileSelect.value = "mean";

    function filteredRuns() {
      return data.runs.filter((run) => {
        if (scenarioSelect.value !== "all" && scenarioKey(run) !== scenarioSelect.value) return false;
        if (qualifiedOnly.checked && !run.run.trend_eligible) return false;
        return true;
      });
    }

    function selectedRun(runs) {
      return runs.find((run) => run.run.id === selectedId) || runs[runs.length - 1] || null;
    }

    function refresh() {
      const runs = filteredRuns();
      const selected = selectedRun(runs);
      if (selected) selectedId = selected.run.id;
      const percentile = percentileSelect.value;
      const statLabel = percentile.toUpperCase();
      count.textContent = `${runs.length} run${runs.length === 1 ? "" : "s"} shown · ${data.trend_runs.length} trend qualified`;
      trendsTitle.textContent = `${data.metrics.length} metric trends · ${statLabel}`;
      trendsCaption.textContent = qualifiedOnly.checked ? "Qualified daily runs only" : "All metrics shown together for the selected scenario";
      renderTrendGrid(trendGrid, runs, percentile, data.metrics);
      renderLatest(latest, selected);
      renderConfig(config, selected);
      renderLatencyGrid(latencyGrid, selected);
      renderBeamProfile(beamProfile, selected);
      renderCpuPipeline(cpuPipeline, selected);
      renderRunHistory(history, runs, selectedId, (runId) => {
        selectedId = runId;
        refresh();
      });
    }

    [scenarioSelect, percentileSelect, qualifiedOnly].forEach((control) => control.addEventListener("change", refresh));
    refresh();
  }

  async function boot() {
    const root = document.getElementById("vgr-dashboard");
    if (!root) return;
    try {
      const url = new URL("../vllm-gr-dashboard-data.json", window.location.href);
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load vllm-gr-dashboard-data.json (${response.status})`);
      initDashboard(await response.json());
    } catch (error) {
      root.insertAdjacentHTML("afterbegin", `<div class="vgr-empty is-error">${escapeHtml(error.message)}</div>`);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
