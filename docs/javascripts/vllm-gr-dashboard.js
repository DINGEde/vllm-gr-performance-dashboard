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

  function metricValue(run, metric, percentile, measurement = "canonical") {
    const diagnosticMeasurement = !["canonical", "stage"].includes(measurement);
    const latency = diagnosticMeasurement
      ? (run.results?.diagnostic?.latency_ms || run.results?.latency_ms)
      : run.results?.latency_ms;
    const fallback = measurement === "canonical" || measurement === "stage"
      ? run.results?.diagnostic?.latency_ms?.[metric]?.[percentile]
      : null;
    return number(latency?.[metric]?.[percentile] ?? fallback);
  }

  function renderMissHitBreakdown(root, run, percentile) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const latency = run.results?.diagnostic?.latency_ms || {};
    const rows = [
      ["Prefill device span", "prefill_miss", "prefill_hit", "stage"],
      ["Prefill GPU compute", "prefill_gpu_compute_miss", "prefill_gpu_compute_hit", "gpu-compute"],
      ["Prefill device wait", "prefill_device_idle_miss", "prefill_device_idle_hit", "gpu-wait"],
      ["Decode device span", "decode_miss", "decode_hit", "stage"],
      ["Decode GPU compute", "decode_gpu_compute_miss", "decode_gpu_compute_hit", "gpu-compute"],
      ["Decode device wait", "decode_device_idle_miss", "decode_device_idle_hit", "gpu-wait"],
      ["Prefill output consumed", "prefill_output_consumed_miss", "prefill_output_consumed_hit", "diagnostic"],
      ["Host overhead", "host_overhead_miss", "host_overhead_hit", "diagnostic"],
    ];
    const hasGpuBreakdown = [
      "prefill_gpu_compute_miss", "prefill_gpu_compute_hit",
      "decode_gpu_compute_miss", "decode_gpu_compute_hit",
    ].some((key) => latency[key]);
    if (!hasGpuBreakdown) {
      root.innerHTML = '<div class="vgr-empty">This run predates the GPU-compute-v5 Miss/Hit breakdown. Select a newer run after the next formal benchmark.</div>';
      return;
    }
    const cell = (key) => {
      const value = number(latency[key]?.[percentile]);
      return value === null ? '<span class="vgr-na">N/A</span>' : `${escapeHtml(fmt(value))} <small>ms</small>`;
    };
    root.innerHTML = `
      <div class="vgr-breakdown-wrap">
        <table class="vgr-breakdown-table">
          <thead><tr><th scope="col">Metric</th><th scope="col">Miss</th><th scope="col">Hit</th></tr></thead>
          <tbody>${rows.map(([label, missKey, hitKey, kind]) => `
            <tr class="is-${escapeHtml(kind)}"><th scope="row">${escapeHtml(label)}<small>${escapeHtml(kind)}</small></th><td>${cell(missKey)}</td><td>${cell(hitKey)}</td></tr>
          `).join("")}</tbody>
        </table>
      </div>
      <p class="vgr-breakdown-note">${escapeHtml(percentile.toUpperCase())} · diagnostic sample only · ${escapeHtml(run.run?.date || "unknown date")} · ${escapeHtml(diagnosticPhaseVersion(run))}</p>
    `;
  }

  function scenarioKey(run) {
    return run.scenario?.key || `beam${run.scenario?.n ?? "unknown"}-legacy`;
  }

  function sourceLabel(run) {
    const prs = run.source?.change_since_previous?.pull_requests;
    if (Array.isArray(prs) && prs.length) {
      return prs.map((pr) => `PR #${pr.number} ${pr.title}`).join(" · ");
    }
    const subject = run.source?.git_subject;
    if (subject) return subject;
    return `${run.source?.branch || "source"} daily snapshot · ${run.run?.date || "unknown date"}`;
  }

  function metricSeriesVersion(run, measurement) {
    if (measurement !== "canonical") return diagnosticPhaseVersion(run);
    return phaseVersion(run);
  }

  function diagnosticPhaseVersion(run) {
    return run.results?.diagnostic?.phase_definition?.version || phaseVersion(run);
  }

  function pipelineKey(run) {
    return run.scenario?.pipeline_version || "legacy-beam-search";
  }

  function pipelineLabel(run) {
    return run.scenario?.beam_api === "beam_search_v1"
      ? "V1 beam_search_v1"
      : "Legacy beam_search";
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
    const diagnostic = run.results.diagnostic?.latency_ms || latency;
    const ttft = latency.ttft;
    const e2el = latency.e2el;
    const requests = run.results.requests;
    const primaryKpis = [
      ["Avg Offline E2E miss", e2el, "direct GRLLM call after cache reset"],
      ["Avg Offline E2E hit", latency.e2el_hit, "same prompt immediately after the measured miss"],
      [latency.prefill ? "Avg Prefill" : "Diagnostic Prefill", latency.prefill || diagnostic.prefill, latency.prefill ? "native phase timestamps" : "legacy diagnostic sample"],
      [latency.decode ? "Avg Decode" : "Diagnostic Decode", latency.decode || diagnostic.decode, latency.decode ? "native phase timestamps; includes finalization" : "legacy diagnostic sample"],
    ].map(([label, value, hint]) => kpi(label, fmt(value?.mean), "ms", value ? `P50 ${fmt(value.p50)} ms · P90 ${fmt(value.p90)} ms · ${hint}` : hint)).join("");
    root.innerHTML = `
      <div class="vgr-hero-copy">
        <div class="vgr-hero-label"><span>${escapeHtml(run.run.date)} · GPU L20</span></div>
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
      ["Beam API", scenario.beam_api || "beam_search"],
      ["Pipeline", scenario.pipeline_version || "legacy-beam-search"],
      ["GPU", "L20"],
      ["Source change", sourceLabel(run)],
      ["Exact revision", run.source.git_sha],
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
      ["Measurement", benchmark.measurement_mode || "legacy"],
      ["Canonical instrumentation", benchmark.instrumentation?.canonical || "legacy measurement"],
      ["Diagnostic requests", benchmark.diagnostic_prompts],
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
      .map((run) => ({ run, value: metricValue(run, metric, percentile, meta.measurement) }))
      .filter((point) => point.value !== null);
    if (!points.length) return '<div class="vgr-empty">No values are available for this selection.</div>';

    const width = 1040;
    const height = 360;
    const left = 72;
    const right = 28;
    const top = 28;
    const bottom = 88;
    const plotW = width - left - right;
    const plotH = height - top - bottom;
    const domain = yDomain(points.map((point) => point.value));
    const dates = [...new Set(points.map((point) => point.run.run.date))].sort();
    const dateIndex = new Map(dates.map((value, index) => [value, index]));
    const x = (date) => {
      const index = dateIndex.get(date) || 0;
      return left + (dates.length === 1 ? plotW / 2 : (index / (dates.length - 1)) * plotW);
    };
    const y = (value) => top + (1 - (value - domain.min) / (domain.max - domain.min)) * plotH;
    const grid = [];
    for (let tick = 0; tick <= 4; tick += 1) {
      const value = domain.max - ((domain.max - domain.min) * tick) / 4;
      const yy = top + (plotH * tick) / 4;
      grid.push(`<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" class="vgr-grid-line"/>`);
      grid.push(`<text x="${left - 12}" y="${yy + 4}" text-anchor="end" class="vgr-axis-label">${escapeHtml(fmt(value))}</text>`);
    }
    const grouped = new Map();
    points.forEach((point) => {
      const key = pipelineKey(point.run);
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(point);
    });
    const series = [...grouped.entries()].sort(([leftKey], [rightKey]) => {
      const leftLegacy = leftKey === "legacy-beam-search" ? 0 : 1;
      const rightLegacy = rightKey === "legacy-beam-search" ? 0 : 1;
      return leftLegacy - rightLegacy || leftKey.localeCompare(rightKey);
    });
    const segments = series.map(([, seriesPoints], seriesIndex) => {
      seriesPoints.sort((a, b) => a.run.run.date.localeCompare(b.run.run.date));
      return seriesPoints.slice(1).map((point, index) => {
        const previous = seriesPoints[index];
        const changed = metricSeriesVersion(previous.run, meta.measurement) !== metricSeriesVersion(point.run, meta.measurement);
        return `<line x1="${x(previous.run.run.date)}" y1="${y(previous.value)}" x2="${x(point.run.run.date)}" y2="${y(point.value)}" class="vgr-trend-line is-series-${seriesIndex}"${changed ? ' stroke-dasharray="6 5"' : ""}><title>${changed ? "Measurement version changed within this pipeline" : pipelineLabel(point.run)}</title></line>`;
      }).join("");
    }).join("");
    const marks = series.map(([, seriesPoints], seriesIndex) => seriesPoints.map((point) => {
      const xx = x(point.run.run.date);
      const yy = y(point.value);
      const title = `${point.run.run.date} · ${pipelineLabel(point.run)} · ${sourceLabel(point.run)} · ${metricSeriesVersion(point.run, meta.measurement)} · ${fmt(point.value)} ${meta.unit}`;
      const marker = seriesIndex === 0
        ? `<circle cx="${xx}" cy="${yy}" r="6"><title>${escapeHtml(title)}</title></circle>`
        : `<rect x="${xx - 5.5}" y="${yy - 5.5}" width="11" height="11" transform="rotate(45 ${xx} ${yy})"><title>${escapeHtml(title)}</title></rect>`;
      return `<g class="vgr-point is-series-${seriesIndex}">${marker}<text x="${xx}" y="${yy - 13}" text-anchor="middle" class="vgr-value-label">${escapeHtml(fmt(point.value))}</text></g>`;
    }).join("")).join("");
    const xLabels = dates.map((date) => `<text x="${x(date)}" y="${height - 58}" text-anchor="middle" class="vgr-axis-label">${escapeHtml(date.slice(5))}</text>`).join("");
    const legend = series.map(([, seriesPoints], index) => `<g transform="translate(${left + index * 190}, ${height - 34})" class="vgr-series-legend is-series-${index}"><line x1="0" y1="0" x2="24" y2="0" class="vgr-trend-line is-series-${index}"/><text x="31" y="4" class="vgr-axis-label">${escapeHtml(pipelineLabel(seriesPoints[0].run))}</text></g>`).join("");
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(meta.label)} ${escapeHtml(percentile)} daily trend"><text x="18" y="${top + plotH / 2}" transform="rotate(-90 18 ${top + plotH / 2})" text-anchor="middle" class="vgr-axis-title">${escapeHtml(meta.label)} (${escapeHtml(meta.unit)})</text>${grid.join("")}${segments}${marks}${xLabels}${legend}<text x="${left + plotW / 2}" y="${height - 4}" text-anchor="middle" class="vgr-axis-title">Run date</text></svg>`;
  }

  function renderTrendGrid(root, runs, percentile, metrics) {
    root.innerHTML = metrics.map((meta) => `
      <article class="vgr-trend-card">
        <div class="vgr-trend-card-head"><strong>${escapeHtml(meta.label)}</strong><span>${escapeHtml(meta.measurement || "canonical")} · ${escapeHtml(percentile.toUpperCase())}</span></div>
        <div class="vgr-chart">${lineChart(runs, meta.key, percentile, meta)}</div>
      </article>
    `).join("");
  }

  function renderLatencyGrid(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const labels = { e2el: "E2E miss", e2el_hit: "E2E hit", prefill: "Avg Prefill", prefill_miss: "Prefill miss", prefill_hit: "Prefill hit", decode: "Decode total (token 1+)", prefill_gpu_compute: "Prefill GPU compute", decode_gpu_compute: "Decode GPU compute total", prefill_device_idle: "Prefill device wait", decode_device_idle: "Decode device wait", prefill_output_consumed: "Prefill output consumed", prefill_dispatch: "Prefill dispatch wait", prefill_cpu_lead: "Prefill / Decode CPU lead", host_overhead: "Host overhead outside both stages", llm_engine_decode: "llm_engine.step() decode", engine_collect_decode: "Decode output collection", entry_preprocess: "Prompt preprocess", beam_setup: "Beam setup / pre_calc", cpu_finalize_detokenize: "Final detokenize" };
    const canonical = run.results.latency_ms || {};
    const diagnostic = run.results.diagnostic?.latency_ms || canonical;
    const available = ["e2el", "e2el_hit"].filter((key) => canonical[key]).map((key) => [key, canonical[key], "canonical"])
      .concat(["prefill_miss", "prefill_hit", "prefill", "decode"].filter((key) => canonical[key] || diagnostic[key]).map((key) => [key, canonical[key] || diagnostic[key], "stage"]))
      .concat(["prefill_gpu_compute", "decode_gpu_compute", "prefill_device_idle", "decode_device_idle", "prefill_output_consumed", "prefill_dispatch", "prefill_cpu_lead", "host_overhead", "entry_preprocess", "beam_setup", "llm_engine_decode", "engine_collect_decode", "cpu_finalize_detokenize"].filter((key) => diagnostic[key]).map((key) => [key, diagnostic[key], key.includes("gpu_compute") ? "gpu-compute" : key.includes("device_idle") ? "gpu-wait" : "diagnostic"]));
    root.innerHTML = `<div class="vgr-latency-cards">${available.map(([key, value, measurement]) => {
      return `<article class="vgr-latency-card"><div><strong>${escapeHtml(labels[key] || key)}</strong><small>${escapeHtml(measurement)}</small></div><dl><dt>Mean</dt><dd>${fmt(value.mean)} ms</dd><dt>P50</dt><dd>${fmt(value.p50)} ms</dd><dt>P90</dt><dd>${fmt(value.p90)} ms</dd><dt>P95</dt><dd>${fmt(value.p95)} ms</dd><dt>P99</dt><dd>${fmt(value.p99)} ms</dd></dl></article>`;
    }).join("")}</div>`;
  }

  function renderBeamProfile(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const latency = run.results.diagnostic?.latency_ms || run.results.latency_ms || {};
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

  function phaseVersion(run) {
    return run.scenario?.benchmark_args?.phase_definition?.version || "legacy";
  }

  function renderDailyChange(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const change = run.source?.change_since_previous || {};
    const prs = Array.isArray(change.pull_requests) ? change.pull_requests : [];
    const prHtml = prs.length
      ? prs.map((pr) => `<a class="vgr-pr-chip" href="${escapeHtml(pr.url)}" target="_blank" rel="noopener">PR #${escapeHtml(pr.number)} · ${escapeHtml(pr.title)}</a>`).join("")
      : '<span class="vgr-muted">No merged PR was detected for this daily snapshot.</span>';
    root.innerHTML = `<div class="vgr-pr-list">${prHtml}</div>`;
  }

  function renderRunHistory(root, runs, selectedId, onSelect) {
    if (!runs.length) {
      root.innerHTML = '<div class="vgr-empty">No runs match the current filters.</div>';
      return;
    }
    root.innerHTML = `<div class="vgr-run-list">${runs.slice().reverse().map((run) => {
      const active = run.run.id === selectedId ? " is-active" : "";
      return `<button type="button" class="vgr-run-row${active}" data-run-id="${escapeHtml(run.run.id)}"><span class="vgr-run-date">${escapeHtml(run.run.date)}</span><span class="vgr-run-main"><strong>${escapeHtml(run.scenario.name)}</strong><small>${escapeHtml(sourceLabel(run))} · ${escapeHtml(run.dataset.kind)} · GPU L20</small></span><span class="vgr-run-result">${escapeHtml(run.results.requests.completed)}/${escapeHtml(run.scenario.num_prompts)}</span></button>`;
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
    const count = document.getElementById("vgr-count");
    const latest = document.getElementById("vgr-latest");
    const dailyChange = document.getElementById("vgr-daily-change");
    const coreTrendGrid = document.getElementById("vgr-core-trend-grid");
    const diagnosticTrendGrid = document.getElementById("vgr-diagnostic-trend-grid");
    const coreTrendsTitle = document.getElementById("vgr-core-trends-title");
    const diagnosticTrendsTitle = document.getElementById("vgr-diagnostic-trends-title");
    const missHitTitle = document.getElementById("vgr-miss-hit-title");
    const missHitBreakdown = document.getElementById("vgr-miss-hit-breakdown");
    const latencyGrid = document.getElementById("vgr-latency-grid");
    const beamProfile = document.getElementById("vgr-beam-profile");
    const config = document.getElementById("vgr-config");
    const history = document.getElementById("vgr-run-history");
    let selectedId = data.runs.length ? data.runs[data.runs.length - 1].run.id : null;

    scenarioSelect.innerHTML = ['<option value="all">All scenarios</option>', ...(data.scenarios || []).map((scenario) => `<option value="${escapeHtml(scenario.key)}">${escapeHtml(scenario.label)}</option>`)].join("");
    if ((data.scenarios || []).length) scenarioSelect.value = data.scenarios[data.scenarios.length - 1].key;
    percentileSelect.innerHTML = data.percentiles.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item.toUpperCase())}</option>`).join("");
    percentileSelect.value = "mean";

    function filteredRuns() {
      return data.runs.filter((run) => scenarioSelect.value === "all" || scenarioKey(run) === scenarioSelect.value);
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
      count.textContent = `${runs.length} run${runs.length === 1 ? "" : "s"} shown`;
      coreTrendsTitle.textContent = `${data.core_metrics.length} core metric trends · ${statLabel}`;
      diagnosticTrendsTitle.textContent = `${data.diagnostic_metrics.length} compute and diagnostic trends · ${statLabel}`;
      missHitTitle.textContent = `Prefill and Decode Miss/Hit breakdown · ${statLabel}`;
      renderTrendGrid(coreTrendGrid, runs, percentile, data.core_metrics);
      renderTrendGrid(diagnosticTrendGrid, runs, percentile, data.diagnostic_metrics);
      renderMissHitBreakdown(missHitBreakdown, selected, percentile);
      renderLatest(latest, selected);
      renderDailyChange(dailyChange, selected);
      renderConfig(config, selected);
      renderLatencyGrid(latencyGrid, selected);
      renderBeamProfile(beamProfile, selected);
      renderRunHistory(history, runs, selectedId, (runId) => {
        selectedId = runId;
        refresh();
      });
    }

    [scenarioSelect, percentileSelect].forEach((control) => control.addEventListener("change", refresh));
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
