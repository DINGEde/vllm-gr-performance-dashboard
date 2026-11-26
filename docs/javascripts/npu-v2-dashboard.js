(function () {
  // ------------------------------------------------------ device toggle ----
  // Three-button GPU / NPU / NPU V2 switch injected on the v2 NPU page only.
  // The GPU and legacy NPU pages render the same three-button toggle from
  // npu-dashboard.js, so the two NPU dashboards never overwrite one another.
  function siblingPageUrl(name) {
    const path = window.location.pathname;
    if (/\/[^/]*\.html$/i.test(path)) return name + ".html";
    return "../" + name + "/";
  }

  function injectDeviceToggle() {
    const root = document.getElementById("npu-v2-dashboard");
    if (!root) return false;
    const buttons = [
      ["gpu", "vllm-gr", "GPU"],
      ["npu", "npu-vllm-gr", "NPU"],
      ["npu-v2", "npu-vllm-gr-v2", "NPU V2"],
    ].map(
      ([device, page, label]) =>
        `<button type="button" class="npu-device-btn${device === "npu-v2" ? " is-active" : ""}" data-page="${page}">${label}</button>`
    ).join("");
    root.insertAdjacentHTML(
      "afterbegin",
      `<div class="npu-device-toggle" role="group" aria-label="Device">${buttons}</div>`
    );
    root.querySelectorAll(".npu-device-btn").forEach((button) => {
      button.addEventListener("click", () => {
        window.location.href = siblingPageUrl(button.getAttribute("data-page"));
      });
    });
    return true;
  }

  // ------------------------------------------------------------ helpers ----
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

  function kpi(label, value, suffix, hint) {
    return `<div class="vgr-kpi"><p>${escapeHtml(label)}</p><strong>${escapeHtml(value)}${suffix ? ` <small>${escapeHtml(suffix)}</small>` : ""}</strong>${hint ? `<span>${escapeHtml(hint)}</span>` : ""}</div>`;
  }

  function cachedDistributions(run, measurement, name) {
    const source =
      measurement === "canonical"
        ? run.distributions
        : run.diagnostic_distributions;
    return source?.[name] || {};
  }

  // metric = { key, label, unit, measurement }. cacheState = "miss" | "hit".
  function metricValue(run, metric, cacheState, percentile) {
    const name = `${metric.key}_${cacheState}`;
    return number(cachedDistributions(run, metric.measurement, name)[percentile]);
  }

  function distMean(run, measurement, name) {
    return number(cachedDistributions(run, measurement, name).mean);
  }

  // Measurement/phase changes break a series into a dashed segment (see the
  // "Solid line: same measurement version" note in the page).
  function measurementVersion(run) {
    return `${run.pipeline_version || "unversioned"}@${run.measurement_mode || "legacy"}`;
  }

  const CACHE_SERIES = [
    { state: "miss", label: "Cold / miss" },
    { state: "hit", label: "Warm / hit" },
  ];

  // ------------------------------------------------------------ latest -----
  function renderLatest(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No runs match the current filters.</div>';
      return;
    }
    const scenario = run.scenario || {};
    const config = run.config || {};
    const graph = run.graph || {};
    const e2eMiss = cachedDistributions(run, "canonical", "e2e_ms_miss");
    const e2eHit = cachedDistributions(run, "canonical", "e2e_ms_hit");
    const prefill = cachedDistributions(run, "diagnostic", "prefill_ms_miss");
    const decode = cachedDistributions(run, "diagnostic", "decode_ms_miss");
    const distHint = (dist) => (dist.mean != null ? `P50 ${fmt(dist.p50)} ms · P90 ${fmt(dist.p90)} ms` : "not collected");
    const kpis = [
      ["Avg Offline E2E miss", e2eMiss.mean, "ms", `${distHint(e2eMiss)} · reset → measured miss`],
      ["Avg Offline E2E hit", e2eHit.mean, "ms", `${distHint(e2eHit)} · identical prompt after the miss`],
      ["Avg Prefill", prefill.mean, "ms", `${distHint(prefill)} · post-canonical diagnostic sample`],
      ["Avg Decode", decode.mean, "ms", `${distHint(decode)} · post-canonical diagnostic sample`],
    ].map(([label, value, suffix, hint]) => kpi(label, fmt(value), suffix, hint)).join("");
    const tags = [
      `<span>batch ${escapeHtml(scenario.batch_size ?? "?")}</span>`,
      `<span>beam ${escapeHtml(scenario.beam_width ?? "?")}</span>`,
      `<span>input ${escapeHtml(scenario.input_len ?? "?")} tokens</span>`,
      `<span>${escapeHtml(config.execution || "graph")} execution</span>`,
      `<span>constraint ${escapeHtml(config.constraint_table_backend || "npu")}</span>`,
      `<span>${escapeHtml(graph.fallbacks == null ? "beam graph" : graph.fallbacks === 0 ? "beam graph · 0 fallbacks" : `beam graph · ${graph.fallbacks} fallback(s)`)}</span>`,
      `<span>${escapeHtml(run.completed ?? 0)} passed / ${escapeHtml(run.failed ?? 0)} failed</span>`,
    ].join("");
    root.innerHTML = `
      <div class="vgr-hero-copy">
        <div class="vgr-hero-label"><span>${escapeHtml(run.date)} · Ascend 910B1 NPU</span></div>
        <h2>${escapeHtml(scenario.label || run.scenario_key)}</h2>
        <p>${escapeHtml(run.model)} · ${escapeHtml(run.pipeline_version || "unversioned")} · ${escapeHtml(run.measurement_mode || "legacy")} measurement</p>
        <div class="vgr-tags">${tags}</div>
      </div>
      <div class="vgr-kpi-grid">${kpis}</div>
    `;
  }

  // ------------------------------------------------------ config -----------
  function renderConfig(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const scenario = run.scenario || {};
    const config = run.config || {};
    const graph = run.graph || {};
    const rows = [
      ["Device", "Ascend 910B1 NPU"],
      ["Execution", config.execution],
      ["Constraint table backend", config.constraint_table_backend],
      ["Pipeline version", run.pipeline_version],
      ["Measurement mode", run.measurement_mode],
      ["Model", config.model || run.model],
      ["Catalog", config.catalog],
      ["Prompt", config.prompt],
      ["Batch size", scenario.batch_size ?? config.batch_size],
      ["Beam width", scenario.beam_width],
      ["Input length", `${scenario.input_len} tokens`],
      ["Generated steps", config.generated_steps],
      ["Max tokens", config.max_tokens],
      ["Max model len", config.max_model_len],
      ["Memory utilization", config.gpu_memory_utilization],
      ["Single request", config.single_request],
      ["Measured / warmup", `${run.num_prompts} / ${run.warmup_requests}`],
      ["Completed / failed", `${run.completed} / ${run.failed}`],
      ["Aggregate output tokens", run.aggregate_output_tokens],
      ["Wall duration", run.duration_seconds != null ? `${fmt(run.duration_seconds)} s` : null],
      ["Beam decode graph", graph.fallbacks == null ? null : `fallbacks ${graph.fallbacks} · max width ${graph.beam_max_width}`],
      ["Captured shapes", (graph.captured_shapes || []).map((shape) => `${shape[0]}x${shape[1]}`).join(", ") || "N/A"],
      ["Full dispatches", graph.full_dispatches],
      ["Started / finished", config.started_at && config.finished_at ? `${config.started_at} → ${config.finished_at}` : null],
      ["Result file", run.source],
    ].filter(([, value]) => value !== undefined && value !== null && value !== "");
    root.innerHTML = `<dl class="vgr-config-grid">${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
  }

  // ------------------------------------------------ miss/hit breakdown ------
  const MISS_HIT_ROWS = [
    ["Prefill device span", "prefill_ms_miss", "prefill_ms_hit", "stage"],
    ["Prefill NPU compute", "prefill_gpu_compute_ms_miss", "prefill_gpu_compute_ms_hit", "npu-compute"],
    ["Prefill device wait", "prefill_device_idle_ms_miss", "prefill_device_idle_ms_hit", "npu-wait"],
    ["Decode device span", "decode_ms_miss", "decode_ms_hit", "stage"],
    ["Decode NPU compute", "decode_gpu_compute_ms_miss", "decode_gpu_compute_ms_hit", "npu-compute"],
    ["Decode device wait", "decode_device_idle_ms_miss", "decode_device_idle_ms_hit", "npu-wait"],
    ["Prefill output consumed", "prefill_output_consumed_ms_miss", "prefill_output_consumed_ms_hit", "diagnostic"],
    ["Host overhead", "host_overhead_ms_miss", "host_overhead_ms_hit", "diagnostic"],
  ];

  function renderMissHitBreakdown(root, run, percentile) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const latency = run.diagnostic_distributions || {};
    const hasBreakdown = ["prefill_gpu_compute_ms_miss", "decode_gpu_compute_ms_hit"].some((key) => latency[key]);
    if (!hasBreakdown) {
      root.innerHTML = '<div class="vgr-empty">No NPU-compute Miss/Hit breakdown was collected for this run. Select a run with the diagnostic stage sample for the full split.</div>';
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
          <tbody>${MISS_HIT_ROWS.map(([label, missKey, hitKey, kind]) => `
            <tr class="is-${escapeHtml(kind)}"><th scope="row">${escapeHtml(label)}<small>${escapeHtml(kind)}</small></th><td>${cell(missKey)}</td><td>${cell(hitKey)}</td></tr>
          `).join("")}</tbody>
        </table>
      </div>
      <p class="vgr-breakdown-note">${escapeHtml(percentile.toUpperCase())} · diagnostic sample only · ${escapeHtml(run.date)} · device wait = device span − measured NPU compute</p>
    `;
  }

  // ------------------------------------------------------ trend chart ------
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

  function lineChart(runs, metric, percentile) {
    // A metric may pin a single cache state (the split "E2E miss" / "E2E hit"
    // cards); otherwise both miss and hit are drawn together. seriesIndex keeps
    // the original position in CACHE_SERIES so miss stays a circle and hit stays
    // a diamond even when only one of them is rendered.
    const seriesPoints = CACHE_SERIES
      .map((series, seriesIndex) => ({ series, seriesIndex }))
      .filter(({ series }) => !metric.cache_state || series.state === metric.cache_state)
      .map(({ series, seriesIndex }) => ({
        series,
        seriesIndex,
        points: runs
          .map((run) => ({ run, value: metricValue(run, metric, series.state, percentile) }))
          .filter((point) => point.value !== null),
      }));
    const allValues = seriesPoints.flatMap((entry) => entry.points.map((point) => point.value));
    if (!allValues.length) return '<div class="vgr-empty">No values are available for this selection.</div>';

    const width = 1040;
    const height = 360;
    const left = 72;
    const right = 28;
    const top = 28;
    const bottom = 88;
    const plotW = width - left - right;
    const plotH = height - top - bottom;
    const domain = yDomain(allValues);
    const dates = [...new Set(seriesPoints.flatMap((entry) => entry.points.map((point) => point.run.date)))].sort();
    const dateIndex = new Map(dates.map((value, index) => [value, index]));
    const x = (date) => {
      const index = dateIndex.get(date) || 0;
      return left + (dates.length === 1 ? plotW / 2 : (index / (dates.length - 1)) * plotW);
    };
    const labelStep = Math.max(1, Math.ceil(dates.length / Math.floor(plotW / 46)));
    const labelIndexes = dates.map((_, index) => index).filter((index) => index % labelStep === 0);
    const lastIndex = dates.length - 1;
    if (labelIndexes[labelIndexes.length - 1] !== lastIndex) {
      if (labelIndexes.length > 1 && lastIndex - labelIndexes[labelIndexes.length - 1] < labelStep) labelIndexes.pop();
      labelIndexes.push(lastIndex);
    }
    const labeledDates = new Set(labelIndexes.map((index) => dates[index]));
    const y = (value) => top + (1 - (value - domain.min) / (domain.max - domain.min)) * plotH;
    const grid = [];
    for (let tick = 0; tick <= 4; tick += 1) {
      const value = domain.max - ((domain.max - domain.min) * tick) / 4;
      const yy = top + (plotH * tick) / 4;
      grid.push(`<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" class="vgr-grid-line"/>`);
      grid.push(`<text x="${left - 12}" y="${yy + 4}" text-anchor="end" class="vgr-axis-label">${escapeHtml(fmt(value))}</text>`);
    }
    // Connect consecutive dated points within one cache-state series only, so a
    // miss and its paired hit never zig-zag together.
    const segments = seriesPoints.map(({ series, points, seriesIndex }) => {
      points.sort((a, b) => a.run.date.localeCompare(b.run.date));
      return points.slice(1).map((point, index) => {
        const previous = points[index];
        const changed = measurementVersion(previous.run) !== measurementVersion(point.run);
        return `<line x1="${x(previous.run.date)}" y1="${y(previous.value)}" x2="${x(point.run.date)}" y2="${y(point.value)}" class="vgr-trend-line is-series-${seriesIndex}"${changed ? ' stroke-dasharray="6 5"' : ""}><title>${changed ? "Measurement version changed within this series" : series.label}</title></line>`;
      }).join("");
    }).join("");
    const marks = seriesPoints.map(({ series, points, seriesIndex }) => points.map((point) => {
      const xx = x(point.run.date);
      const yy = y(point.value);
      const title = `${point.run.date} · ${series.label} · ${fmt(point.value)} ${metric.unit} · ${measurementVersion(point.run)} · ${point.run.source || ""}`;
      const marker = seriesIndex === 0
        ? `<circle cx="${xx}" cy="${yy}" r="6"><title>${escapeHtml(title)}</title></circle>`
        : `<rect x="${xx - 5.5}" y="${yy - 5.5}" width="11" height="11" transform="rotate(45 ${xx} ${yy})"><title>${escapeHtml(title)}</title></rect>`;
      const yOffset = seriesIndex === 0 ? -13 : 22;
      const valueLabel = labeledDates.has(point.run.date)
        ? `<text x="${xx}" y="${yy + yOffset}" text-anchor="middle" class="vgr-value-label">${escapeHtml(fmt(point.value))}</text>`
        : "";
      return `<g class="vgr-point is-series-${seriesIndex}">${marker}${valueLabel}</g>`;
    }).join("")).join("");
    const xLabels = labelIndexes.map((index) => `<text x="${x(dates[index])}" y="${height - 58}" text-anchor="middle" class="vgr-axis-label">${escapeHtml(dates[index].slice(5))}</text>`).join("");
    const legend = seriesPoints.map(({ series, seriesIndex }, position) => `<g transform="translate(${left + position * 190}, ${height - 34})" class="vgr-series-legend is-series-${seriesIndex}"><line x1="0" y1="0" x2="24" y2="0" class="vgr-trend-line is-series-${seriesIndex}"/><text x="31" y="4" class="vgr-axis-label">${escapeHtml(series.label)}</text></g>`).join("");
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(metric.label)} ${escapeHtml(percentile)} daily trend"><text x="18" y="${top + plotH / 2}" transform="rotate(-90 18 ${top + plotH / 2})" text-anchor="middle" class="vgr-axis-title">${escapeHtml(metric.label)} (${escapeHtml(metric.unit)})</text>${grid.join("")}${segments}${marks}${xLabels}${legend}<text x="${left + plotW / 2}" y="${height - 4}" text-anchor="middle" class="vgr-axis-title">Run date</text></svg>`;
  }

  function renderTrendGrid(root, runs, percentile, metrics) {
    root.innerHTML = metrics.map((meta) => `
      <article class="vgr-trend-card">
        <div class="vgr-trend-card-head"><strong>${escapeHtml(meta.label)}</strong><span>${escapeHtml(meta.measurement || "canonical")} · ${escapeHtml(percentile.toUpperCase())}</span></div>
        <div class="vgr-chart">${lineChart(runs, meta, percentile)}</div>
      </article>
    `).join("");
  }

  // ------------------------------------------------------ latency profile --
  const LATENCY_KEYS = [
    ["e2e_ms_miss", "E2E miss", "canonical"],
    ["e2e_ms_hit", "E2E hit", "canonical"],
    ["prefill_ms_miss", "Prefill miss", "stage"],
    ["decode_ms_miss", "Decode miss", "stage"],
    ["prefill_gpu_compute_ms_miss", "Prefill NPU compute", "npu-compute"],
    ["decode_gpu_compute_ms_miss", "Decode NPU compute", "npu-compute"],
    ["prefill_device_idle_ms_miss", "Prefill device wait", "npu-wait"],
    ["decode_device_idle_ms_miss", "Decode device wait", "npu-wait"],
    ["prefill_output_consumed_ms_miss", "Prefill output consumed", "diagnostic"],
    ["prefill_dispatch_ms_miss", "Prefill dispatch wait", "diagnostic"],
    ["prefill_cpu_lead_ms_miss", "Prefill / Decode CPU lead", "diagnostic"],
    ["host_overhead_ms_miss", "Host overhead", "diagnostic"],
  ];

  function renderLatencyGrid(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const available = LATENCY_KEYS
      .map(([key, label, measurement]) => [key, label, measurement, cachedDistributions(run, measurement, key)])
      .filter(([, , , dist]) => dist.mean != null);
    if (!available.length) {
      root.innerHTML = '<div class="vgr-empty">No latency distributions are available for this run.</div>';
      return;
    }
    root.innerHTML = `<div class="vgr-latency-cards">${available.map(([, label, measurement, dist]) => `
      <article class="vgr-latency-card"><div><strong>${escapeHtml(label)}</strong><small>${escapeHtml(measurement)}</small></div><dl><dt>Mean</dt><dd>${fmt(dist.mean)} ms</dd><dt>P50</dt><dd>${fmt(dist.p50)} ms</dd><dt>P90</dt><dd>${fmt(dist.p90)} ms</dd><dt>P95</dt><dd>${fmt(dist.p95)} ms</dd><dt>P99</dt><dd>${fmt(dist.p99)} ms</dd></dl></article>
    `).join("")}</div>`;
  }

  // ------------------------------------------------------ beam execution ---
  function renderBeamProfile(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const latency = run.diagnostic_distributions || {};
    if (!latency.prefill_ms_miss || !latency.decode_ms_miss) {
      root.innerHTML = '<div class="vgr-empty">E2E only: no stage breakdown was collected for this run.</div>';
      return;
    }
    const states = [
      ["Cold / miss average", "prefill_ms_miss", "decode_ms_miss", "e2e_ms_miss"],
      ["Warm / hit average", "prefill_ms_hit", "decode_ms_hit", "e2e_ms_hit"],
    ];
    root.innerHTML = `<div class="vgr-beam-profile-grid">${states.map(([label, prefillKey, decodeKey, e2eKey]) => {
      const prefill = latency[prefillKey]?.mean;
      const decode = latency[decodeKey]?.mean;
      const e2e = latency[e2eKey]?.mean;
      const parts = [["Avg Prefill", prefill, "is-prefill"], ["Avg Decode", decode, "is-decode"]];
      const total = parts.reduce((sum, part) => sum + (number(part[1]) || 0), 0);
      const segments = parts.map(([partLabel, value, className]) => `<span class="${className}" style="width:${total ? 100 * value / total : 0}%" title="${escapeHtml(partLabel)}: ${fmt(value)} ms"></span>`).join("");
      return `<article class="vgr-profile-card"><div class="vgr-profile-title"><strong>${escapeHtml(label)}</strong><span>${fmt(e2e)} ms Avg E2E</span></div><div class="vgr-stack-bar">${segments}</div><div class="vgr-profile-legend">${parts.map(([partLabel, value, className]) => `<span><i class="${className}"></i>${escapeHtml(partLabel)} <strong>${fmt(value)} ms</strong></span>`).join("")}</div></article>`;
    }).join("")}</div>`;
  }

  // ------------------------------------------------ stage execution --------
  // Additive decomposition of the diagnostic sample: prefill + decode + host
  // overhead closes to E2E, and each device span splits into NPU compute plus
  // device wait. The device spans are a hardware-unvalidated first draft of
  // torch.npu.Event timing, so this figure is indicative rather than calibrated.
  const STAGE_STATES = ["miss", "hit"];
  const STAGE_E2E_KEY = { miss: "e2e_ms_miss", hit: "e2e_ms_hit" };
  const STAGE_SEGMENTS = [
    ["prefill-gpu", "Prefill NPU compute", "prefill_gpu_compute_ms"],
    ["prefill-idle", "Prefill device idle", "prefill_device_idle_ms"],
    ["decode-gpu", "Decode NPU compute", "decode_gpu_compute_ms"],
    ["decode-idle", "Decode device idle", "decode_device_idle_ms"],
    ["host", "Host overhead", "host_overhead_ms"],
  ];
  const STAGE_BASE_KEYS = [
    "prefill_ms", "prefill_gpu_compute_ms", "prefill_device_idle_ms",
    "decode_ms", "decode_gpu_compute_ms", "decode_device_idle_ms",
    "host_overhead_ms",
  ];
  // Host-side spans the timeline panels also draw; present on every raw.v2
  // diagnostic sample. stageComplete requires them so the panels never render
  // half a timeline.
  const STAGE_PANEL_KEYS = [
    "prefill_dispatch_ms", "prefill_cpu_lead_ms", "prefill_output_consumed_ms",
  ];
  const STAGE_ANCHOR_TEXT = {
    e2e_ms: [
      "request_started, the instrumented beam_search_v1 call start",
      "request_finished, the return after the NPU-complete fence",
      "host wall over the whole request: frontend dispatch, Prefill, Decode and terminal collection; the outer monotonic clock with a final torch.npu.synchronize()",
    ],
    prefill_dispatch_ms: [
      "beam_started, after the post-synchronize submit anchor",
      "prefill_worker_started, entry to the PREFILL execute",
      "host wall from submit through PREFILL NPUEagerBeamStageRunner.execute entry; a subset of host_overhead",
    ],
    prefill_cpu_lead_ms: [
      "first_decode_started, the first DECODE execute entry on the host clock",
      "prefill_gpu_ready = prefill_worker_started + prefill_ms",
      "how far the CPU advanced into first-Decode preparation before the NPU finished Prefill",
    ],
    prefill_output_consumed_ms: [
      "beam_started",
      "prefill_output_consumed, output-producing PREFILL sample completion",
      "submit boundary through the output-producing Prefill sample; the NPU runner has no separate async output queue, so this marks the Prefill sample boundary",
    ],
    prefill_ms: [
      "prefill_stage_start_event",
      "prefill_end_event, after the output-producing PREFILL sample",
      "entry to the PREFILL NPUEagerBeamStageRunner.execute → sample completion; excludes frontend dispatch",
    ],
    prefill_gpu_compute_ms: [
      "prefill_model_start_event + prefill_sample_pair[0]",
      "prefill_execute_end_event + prefill_sample_pair[1]",
      "execute (model forward + compute_logits) + output-producing sample window; excludes dispatch and the execute-to-sample gap",
    ],
    prefill_device_idle_ms: [
      "residual prefill_ms − prefill_gpu_compute_ms",
      "same, not an independent event",
      "CPU launch bubbles and queue waits inside the Prefill device span",
    ],
    decode_ms: [
      "decode_start_event, recorded at the first DECODE execute entry",
      "decode_end_event, after the last Decode sample",
      "entry to the first DECODE execute → last Decode sample completion; excludes Prefill by stream ordering",
    ],
    decode_gpu_compute_ms: [
      "start of each decode compute pair",
      "end of each such pair",
      "sum of per-stage execute + sample/beam-advance windows on the NPU",
    ],
    decode_device_idle_ms: [
      "residual decode_ms − decode_gpu_compute_ms",
      "same, not an independent event",
      "CPU prepare and inter-stage queue gaps inside the Decode device span",
    ],
    host_overhead_ms: [
      "e2e − prefill − decode",
      "same, not an independent event",
      "frontend dispatch, EngineCore queueing, and terminal collection outside both device spans",
    ],
  };
  // Anchor-table rows in draw order: the container first, then host lanes, then
  // device lanes. The lane column tells the reader which clock each row lives on.
  const STAGE_ANCHOR_ROWS = [
    ["e2e_ms", "both"],
    ["prefill_dispatch_ms", "host"],
    ["prefill_cpu_lead_ms", "host"],
    ["prefill_output_consumed_ms", "host"],
    ["host_overhead_ms", "host"],
    ["prefill_ms", "device"],
    ["prefill_gpu_compute_ms", "device"],
    ["prefill_device_idle_ms", "device"],
    ["decode_ms", "device"],
    ["decode_gpu_compute_ms", "device"],
    ["decode_device_idle_ms", "device"],
  ];
  // (lane, row label, [bar spec]) where a bar spec is
  // (duration key, start endpoint, end endpoint, fill slot, projected?).
  const STAGE_LAYER_ROWS = [
    ["host", "dispatch", [["prefill_dispatch_ms", "dispatch_end", 0, "host", false]]],
    ["host", "look-ahead", [["prefill_cpu_lead_ms", "lead_start", "prefill", "host", false]]],
    ["host", "output", [["prefill_output_consumed_ms", "dispatch_end", "consumed", "host", false]]],
    ["host", "overhead", [
      ["host_overhead_head", "dispatch_end", 0, "host", true],
      ["host_overhead_tail", "decode", "overhead_tail", "host", true],
    ]],
    ["device", "prefill", [
      ["prefill_gpu_compute_ms", 0, "prefill_gpu_compute", "prefill-gpu", false],
      ["prefill_device_idle_ms", "prefill_gpu_compute", "prefill", "prefill-idle", false],
    ]],
    ["device", "decode", [
      ["decode_gpu_compute_ms", "prefill", "decode_gpu_compute", "decode-gpu", false],
      ["decode_device_idle_ms", "decode_gpu_compute", "decode", "decode-idle", false],
    ]],
  ];
  // The head/tail split of host_overhead needs the offset of beam_started from
  // request_started, which is never instrumented, so the measured dispatch span
  // is substituted for the head and the remainder becomes the tail.
  const STAGE_BAR_ALIASES = {
    host_overhead_head: "head ≈dispatch",
    host_overhead_tail: "tail ≈wrap-up",
  };
  const STAGE_NOTES = {
    bandA: "Each row is additive: prefill NPU compute + prefill device idle + decode NPU compute + decode device idle + host overhead closes to e2e_ms. Hit mean and miss mean are the two per-cache-state distributions. The pale segments are the part of the same device span where the NPU is not running a measured kernel.",
    sharedAxis: "Both lanes share one horizontal axis: device-time offset from the PREFILL execute entry (0), the instant prefill_stage_start_event is recorded. Span start and end are directly comparable: the two device spans are contiguous on a single FIFO compute stream, prefill_dispatch ends at 0 by construction, and prefill_cpu_lead ends at prefill exactly. Miss and hit share one px/ms, so equal bar length means equal duration.",
    deviceIdle: "*_device_idle is measured with torch.npu.Event on the device clock, so it can only live on the device lane; but that stretch of NPU inactivity is caused by the CPU, which has not finished feeding kernels. Read a pale device segment against the host bar that spans it: prefill_cpu_lead or prefill_output_consumed.",
    projected: "Semi-transparent bars are projections, not measurements: host_overhead is a head plus a tail, and splitting it needs the offset of beam_started from request_started, the cost of entering submit_once, which is never instrumented. The measured dispatch span is substituted for the head and the remainder becomes the tail, so when host_overhead <= prefill_dispatch the two drawn pieces no longer sum to host_overhead, hence the ≈. Start and end anchors for every bar are in the anchor table below.",
  };
  const STAGE_GEOM = {
    width: 1040, labelW: 150, right: 26,
    bandATop: 52, bandARowH: 46, bandAGap: 34,
    rowH: 34, gap: 10, groupGap: 24, titleH: 30,
    barInlineMin: 46, inlineMin: 96, leaderMin: 10,
    residualLimit: 1e-6,
  };

  function stageMean(run, measurement, key) {
    return number(cachedDistributions(run, measurement, key).mean);
  }

  function stageComplete(run) {
    const latency = run.diagnostic_distributions || {};
    if (!latency) return false;
    for (const state of STAGE_STATES) {
      if (stageMean(run, "diagnostic", STAGE_E2E_KEY[state]) === null) return false;
      for (const base of STAGE_BASE_KEYS) {
        if (stageMean(run, "diagnostic", `${base}_${state}`) === null) return false;
      }
      for (const base of STAGE_PANEL_KEYS) {
        if (stageMean(run, "diagnostic", `${base}_${state}`) === null) return false;
      }
    }
    return true;
  }

  function stageResidual(run) {
    let worst = 0;
    for (const state of STAGE_STATES) {
      const values = {};
      for (const base of STAGE_BASE_KEYS) {
        const value = stageMean(run, "diagnostic", `${base}_${state}`);
        if (value === null) return Infinity;
        values[base] = value;
      }
      const e2e = stageMean(run, "diagnostic", STAGE_E2E_KEY[state]);
      if (e2e === null) return Infinity;
      worst = Math.max(
        worst,
        Math.abs(values.prefill_ms - (values.prefill_gpu_compute_ms + values.prefill_device_idle_ms)),
        Math.abs(values.decode_ms - (values.decode_gpu_compute_ms + values.decode_device_idle_ms)),
        Math.abs(e2e - (values.prefill_ms + values.decode_ms + values.host_overhead_ms)),
      );
    }
    return worst;
  }

  function stageFrame(run) {
    let lo = Infinity;
    let hi = -Infinity;
    for (const state of STAGE_STATES) {
      const dispatch = stageMean(run, "diagnostic", `prefill_dispatch_ms_${state}`);
      const prefill = stageMean(run, "diagnostic", `prefill_ms_${state}`);
      const decode = stageMean(run, "diagnostic", `decode_ms_${state}`);
      const overhead = stageMean(run, "diagnostic", `host_overhead_ms_${state}`);
      const e2e = stageMean(run, "diagnostic", STAGE_E2E_KEY[state]);
      lo = Math.min(lo, -dispatch);
      hi = Math.max(hi, prefill + decode + Math.max(overhead - dispatch, 0), e2e);
    }
    const scale = (STAGE_GEOM.width - STAGE_GEOM.labelW - STAGE_GEOM.right) / (hi - lo);
    // Aim for eight intervals, leaving room for the longest ms label; choose a
    // readable 1/2/5 step and build the ruler once for all three SVGs.
    const labelWidth = (`${Number(hi.toPrecision(12))} ms`.length * 7) + 24;
    const minimumStep = Math.max((hi - lo) / 8, Math.max(100, labelWidth) / scale);
    const magnitude = 10 ** Math.floor(Math.log10(minimumStep));
    const step = [1, 2, 5, 10].find((factor) => factor * magnitude >= minimumStep) * magnitude;
    const ticks = Array.from({ length: Math.floor(hi / step) + 1 }, (_, index) => Number((index * step).toPrecision(12)));
    return { lo, hi, scale, ticks, x: (value) => STAGE_GEOM.labelW + (value - lo) * scale };
  }

  function renderStageBandA(root, run, frame) {
    const geom = STAGE_GEOM;
    const height = geom.bandATop + STAGE_STATES.length * (geom.bandARowH + geom.bandAGap) + 30;
    const parts = [`<svg viewBox="0 0 ${geom.width} ${height}" role="img" aria-label="Additive device-time decomposition of end-to-end latency by cache state">`];
    for (const tick of frame.ticks) {
      parts.push(`<line x1="${frame.x(tick).toFixed(2)}" y1="${geom.bandATop - 14}" x2="${frame.x(tick).toFixed(2)}" y2="${height - 30}" class="vgr-grid-line"/>`);
      parts.push(`<text x="${frame.x(tick).toFixed(2)}" y="${geom.bandATop - 20}" text-anchor="end" class="vgr-axis-label">${tick} ms</text>`);
    }
    parts.push(`<line x1="${frame.x(0).toFixed(2)}" y1="${geom.bandATop - 14}" x2="${frame.x(0).toFixed(2)}" y2="${height - 30}" class="vgr-stage-zero"/>`);
    STAGE_STATES.forEach((state, index) => {
      const y = geom.bandATop + index * (geom.bandARowH + geom.bandAGap);
      const e2e = stageMean(run, "diagnostic", STAGE_E2E_KEY[state]);
      parts.push(`<text x="0" y="${y + 18}" class="vgr-stage-strong">${state.toUpperCase()}</text>`);
      parts.push(`<text x="0" y="${y + 36}" class="vgr-axis-label">E2E ${escapeHtml(fmt(e2e, 2))} ms</text>`);
      let cursor = frame.x(0);
      STAGE_SEGMENTS.forEach(([slot, label, base]) => {
        const value = stageMean(run, "diagnostic", `${base}_${state}`) || 0;
        const width = value * frame.scale;
        parts.push(`<g class="vgr-stage-fill-${slot}">`);
        parts.push(`<rect x="${cursor.toFixed(2)}" y="${y}" width="${Math.max(width, 0.6).toFixed(2)}" height="${geom.bandARowH}" rx="2"><title>${escapeHtml(`${label}: ${fmt(value)} ms`)}</title></rect>`);
        if (width >= 46) {
          parts.push(`<text x="${(cursor + width / 2).toFixed(2)}" y="${y + 21}" text-anchor="middle" class="vgr-stage-value">${escapeHtml(fmt(value, 2))}</text>`);
          parts.push(`<text x="${(cursor + width / 2).toFixed(2)}" y="${y + 36}" text-anchor="middle" class="vgr-stage-name">${escapeHtml(label.split(" ")[0])}</text>`);
        }
        parts.push("</g>");
        cursor += width;
      });
    });
    parts.push("</svg>");
    return parts.join("");
  }

  function stageEndpoints(run, state) {
    const value = (base) => stageMean(run, "diagnostic", `${base}_${state}`);
    const dispatch = value("prefill_dispatch_ms");
    const prefill = value("prefill_ms");
    const decode = value("decode_ms");
    const overhead = value("host_overhead_ms");
    const tail = Math.max(overhead - dispatch, 0);
    const prefillGpu = value("prefill_gpu_compute_ms");
    const decodeGpu = value("decode_gpu_compute_ms");
    return {
      ends: {
        dispatch_end: -dispatch,
        lead_start: prefill - value("prefill_cpu_lead_ms"),
        consumed: -dispatch + value("prefill_output_consumed_ms"),
        prefill_gpu_compute: prefillGpu,
        prefill,
        decode_gpu_compute: prefill + decodeGpu,
        decode: prefill + decode,
        overhead_tail: prefill + decode + tail,
      },
      durations: {
        prefill_dispatch_ms: dispatch,
        prefill_cpu_lead_ms: value("prefill_cpu_lead_ms"),
        prefill_output_consumed_ms: value("prefill_output_consumed_ms"),
        host_overhead_head: dispatch,
        host_overhead_tail: tail,
        prefill_gpu_compute_ms: prefillGpu,
        prefill_device_idle_ms: value("prefill_device_idle_ms"),
        decode_gpu_compute_ms: decodeGpu,
        decode_device_idle_ms: value("decode_device_idle_ms"),
      },
    };
  }

  function stageSpan(endpoints, start, end) {
    const resolve = (endpoint) => (typeof endpoint === "string" ? endpoints.ends[endpoint] : endpoint);
    const first = resolve(start);
    const second = resolve(end);
    return second < first ? [second, first] : [first, second];
  }

  // A row that hangs a leader label underneath needs extra room, or the label
  // lands on the next group header. The decision is taken over BOTH states so the
  // miss and hit panels keep identical row heights and stay comparable row by row.
  function stagePanelExtra(endpoints, scale) {
    const extra = {};
    STAGE_LAYER_ROWS.forEach(([, name, specs]) => {
      const thinnest = Math.min(...STAGE_STATES.map((state) => Math.min(...specs.map(([, start, end]) => {
        const [a, b] = stageSpan(endpoints[state], start, end);
        return (b - a) * scale;
      }))));
      extra[name] = thinnest < STAGE_GEOM.inlineMin ? 18 : 0;
    });
    return extra;
  }

  function renderStagePanel(run, state, frame) {
    const geom = STAGE_GEOM;
    const points = {};
    STAGE_STATES.forEach((item) => { points[item] = stageEndpoints(run, item); });
    const endpoints = points[state];
    const extra = stagePanelExtra(points, frame.scale);
    const height = geom.titleH + geom.groupGap + 30
      + STAGE_LAYER_ROWS.reduce((sum, [, name]) => sum + geom.rowH + geom.gap + extra[name], 0);
    const plotW = geom.width - geom.labelW - geom.right;
    const parts = [`<svg viewBox="0 0 ${geom.width} ${height}" role="img" aria-label="${escapeHtml(state)} host and device timeline on one shared axis">`];
    parts.push(`<rect x="${geom.labelW}" y="${geom.titleH - 16}" width="${plotW}" height="${height - geom.titleH - 2}" class="vgr-stage-panel" rx="4"/>`);
    parts.push(`<text x="0" y="${geom.titleH - 20}" class="vgr-stage-strong">${escapeHtml(state.toUpperCase())}</text>`);
    for (const tick of frame.ticks) {
      parts.push(`<line x1="${frame.x(tick).toFixed(2)}" y1="${geom.titleH - 16}" x2="${frame.x(tick).toFixed(2)}" y2="${height - 20}" class="vgr-grid-line"/>`);
      parts.push(`<text x="${frame.x(tick).toFixed(2)}" y="${geom.titleH - 20}" text-anchor="end" class="vgr-axis-label">${tick} ms</text>`);
    }
    parts.push(`<line x1="${frame.x(0).toFixed(2)}" y1="${geom.titleH - 16}" x2="${frame.x(0).toFixed(2)}" y2="${height - 20}" class="vgr-stage-zero"/>`);
    parts.push(`<text x="${frame.x(0).toFixed(2)}" y="${height - 7}" text-anchor="middle" class="vgr-axis-label">PREFILL execute entry (0)</text>`);
    let y = geom.titleH;
    STAGE_LAYER_ROWS.forEach(([lane, name, specs]) => {
      if (name === "dispatch") {
        parts.push(`<text x="0" y="${y - 8}" class="vgr-stage-lane-group">HOST clock · perf_counter_ns</text>`);
      }
      if (lane === "device" && name === "prefill") {
        y += geom.groupGap;
        parts.push(`<text x="0" y="${y - 8}" class="vgr-stage-lane-group">DEVICE clock · single FIFO NPU stream</text>`);
      }
      parts.push(`<text x="0" y="${y + 21}" class="vgr-stage-lane">${escapeHtml(name)}</text>`);
      specs.forEach(([key, start, end, slot, projected]) => {
        const [a, b] = stageSpan(endpoints, start, end);
        const x0 = frame.x(a);
        const width = Math.max((b - a) * frame.scale, 1);
        const label = STAGE_BAR_ALIASES[key] || key;
        const caption = `${fmt(endpoints.durations[key], 2)} ms`;
        parts.push(`<g class="vgr-stage-fill-${slot}">`);
        parts.push(`<rect x="${x0.toFixed(2)}" y="${y}" width="${width.toFixed(2)}" height="${geom.rowH}" rx="2"${projected ? ' class="vgr-stage-projected"' : ""}><title>${escapeHtml(`${label} = ${caption}`)}</title></rect>`);
        if (width >= geom.inlineMin) {
          parts.push(`<text x="${(x0 + width / 2).toFixed(2)}" y="${y + 15}" text-anchor="middle" class="vgr-stage-name">${escapeHtml(label)}</text>`);
          parts.push(`<text x="${(x0 + width / 2).toFixed(2)}" y="${y + 28}" text-anchor="middle" class="vgr-stage-value">${escapeHtml(caption)}</text>`);
        }
        parts.push("</g>");
        if (width < geom.inlineMin) {
          parts.push(`<line x1="${(x0 + width / 2).toFixed(2)}" y1="${y + geom.rowH}" x2="${(x0 + width / 2).toFixed(2)}" y2="${y + geom.rowH + 6}" class="vgr-stage-leader"/>`);
          parts.push(`<text x="${(x0 + width / 2).toFixed(2)}" y="${y + geom.rowH + 16}" text-anchor="middle" class="vgr-stage-leader">${escapeHtml(`${label} ${caption}`)}</text>`);
        }
      });
      y += geom.rowH + geom.gap + extra[name];
    });
    parts.push("</svg>");
    return parts.join("");
  }

  function renderStageAnchorTable(root, run) {
    const rows = STAGE_ANCHOR_ROWS.map(([key, lane]) => {
      const [start, end, engine] = STAGE_ANCHOR_TEXT[key] || ["", "", ""];
      // Three decimals here, two on the bars: the table is the value of record,
      // while the inline label has to stay readable inside a 30 px segment.
      const cell = (state) => escapeHtml(fmt(stageMean(run, "diagnostic", `${key}_${state}`) ?? null, 3));
      return `<tr><th scope="row"><code>${escapeHtml(key)}</code></th><td>${escapeHtml(lane)}</td><td>${cell("miss")}</td><td>${cell("hit")}</td><td><code>${escapeHtml(start)}</code></td><td><code>${escapeHtml(end)}</code></td><td>${escapeHtml(engine)}</td></tr>`;
    }).join("");
    return `
      <h3>Start and end anchors</h3>
      <div class="vgr-breakdown-wrap vgr-stage-anchors">
        <table class="vgr-breakdown-table">
          <thead><tr><th scope="col">Metric</th><th scope="col">Lane</th><th scope="col">Miss mean</th><th scope="col">Hit mean</th><th scope="col">Start anchor</th><th scope="col">End anchor</th><th scope="col">Engine-side semantics</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="vgr-breakdown-note">Means over the diagnostic sample. Each row is additive: prefill NPU compute + prefill device idle + decode NPU compute + decode device idle + host overhead closes to e2e_ms. Device spans are a hardware-unvalidated first draft of torch.npu.Event timing.</p>
    `;
  }

  function renderStageFigure(root, run) {
    if (!root) return;
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const latency = run.diagnostic_distributions;
    if (!latency) {
      root.innerHTML = '<div class="vgr-empty">No diagnostic stage sample was recorded for this run, so the stage decomposition cannot be drawn.</div>';
      return;
    }
    if (!stageComplete(run)) {
      root.innerHTML = '<div class="vgr-empty">This run does not instrument prefill, decode, host overhead, the three host-side spans and the NPU device split together, so the stage figure is not drawn.</div>';
      return;
    }
    const residual = stageResidual(run);
    if (!(residual < STAGE_GEOM.residualLimit)) {
      root.innerHTML = `<div class="vgr-empty">This run fails the additivity gate (residual ${escapeHtml(fmt(residual * 1000, 3))} us): prefill + decode + host overhead no longer closes to E2E, so the stage figure is not drawn.</div>`;
      return;
    }
    const frame = stageFrame(run);
    const legend = STAGE_SEGMENTS.map(([slot, label]) => `<span><i class="vgr-stage-fill-${slot}"></i>${escapeHtml(label)}</span>`).join("");
    root.innerHTML = `
      <div class="vgr-stage">
        <div class="vgr-profile-legend">${legend}</div>
        <div class="vgr-chart">${renderStageBandA(root, run, frame)}</div>
        <p class="vgr-breakdown-note">${escapeHtml(STAGE_NOTES.bandA)}</p>
        <div class="vgr-chart">${renderStagePanel(run, "miss", frame)}</div>
        <div class="vgr-chart">${renderStagePanel(run, "hit", frame)}</div>
        <p class="vgr-breakdown-note">${escapeHtml(STAGE_NOTES.sharedAxis)}</p>
        <p class="vgr-breakdown-note">${escapeHtml(STAGE_NOTES.deviceIdle)}</p>
        <p class="vgr-breakdown-note">${escapeHtml(STAGE_NOTES.projected)}</p>
        ${renderStageAnchorTable(root, run)}
      </div>
    `;
  }

  // ------------------------------------------------------ daily delivery ----
  function renderDailyChange(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const change = run.source_meta?.change_since_previous || {};
    const prs = Array.isArray(change.pull_requests) ? change.pull_requests : [];
    let html;
    if (prs.length) {
      html = prs.map((pr) => `<a class="vgr-pr-chip" href="${escapeHtml(pr.url)}" target="_blank" rel="noopener">PR #${escapeHtml(pr.number)} · ${escapeHtml(pr.title)}</a>`).join("");
    } else if (change.initial_baseline) {
      html = '<span class="vgr-muted">No earlier NPU v2 snapshot with git metadata yet; this run establishes the baseline.</span>';
    } else if (!run.source_meta) {
      html = '<span class="vgr-muted">This run predates git-metadata capture and has no merged-PR information.</span>';
    } else {
      html = '<span class="vgr-muted">No merged PR was detected for this daily snapshot.</span>';
    }
    root.innerHTML = `<div class="vgr-pr-list">${html}</div>`;
  }

  // ------------------------------------------------------ run history ------
  function renderRunHistory(root, runs, selectedId, onSelect) {
    if (!runs.length) {
      root.innerHTML = '<div class="vgr-empty">No runs match the current filters.</div>';
      return;
    }
    root.innerHTML = `<div class="vgr-run-list">${runs.slice().reverse().map((run) => {
      const active = run.runId === selectedId ? " is-active" : "";
      const e2eMiss = cachedDistributions(run, "canonical", "e2e_ms_miss").mean;
      return `<div class="vgr-run-row${active}"><button type="button" class="vgr-run-select" data-run-id="${escapeHtml(run.runId)}"><span class="vgr-run-date">${escapeHtml(run.date)}</span><span class="vgr-run-main"><strong>${escapeHtml(run.scenario.label || run.scenario_key)}</strong><small>${escapeHtml(run.model)} · ${escapeHtml(run.pipeline_version || "unversioned")} · Ascend 910B1 NPU</small></span><span class="vgr-run-result">${fmt(e2eMiss)} ms</span></button></div>`;
    }).join("")}</div>`;
    root.querySelectorAll(".vgr-run-select").forEach((button) => {
      button.addEventListener("click", () => onSelect(button.getAttribute("data-run-id")));
    });
  }

  // ------------------------------------------------------ init -------------
  function initDashboard(data) {
    const root = document.getElementById("npu-v2-dashboard");
    if (!root) return;
    const scenarioSelect = document.getElementById("npu-v2-scenario");
    if (!scenarioSelect) return; // empty state page: no controls, nothing to fill
    const percentileSelect = document.getElementById("npu-v2-percentile");
    const count = document.getElementById("npu-v2-count");
    const latest = document.getElementById("npu-v2-latest");
    const dailyChange = document.getElementById("npu-v2-daily-change");
    const missHitBreakdown = document.getElementById("npu-v2-miss-hit-breakdown");
    const coreTrendGrid = document.getElementById("npu-v2-core-trend-grid");
    const diagnosticTrendGrid = document.getElementById("npu-v2-diagnostic-trend-grid");
    const missHitTitle = document.getElementById("npu-v2-miss-hit-title");
    const coreTrendsTitle = document.getElementById("npu-v2-core-trends-title");
    const diagnosticTrendsTitle = document.getElementById("npu-v2-diagnostic-trends-title");
    const latencyGrid = document.getElementById("npu-v2-latency-grid");
    const beamProfile = document.getElementById("npu-v2-beam-profile");
    const stageFigure = document.getElementById("npu-v2-stage-figure");
    const config = document.getElementById("npu-v2-config");
    const history = document.getElementById("npu-v2-run-history");

    const runs = data.runs || [];
    const scenarioMeta = new Map((data.scenarios || []).map((item) => [item.key, item]));
    const coreMetrics = data.core_metrics || [];
    const diagnosticMetrics = data.diagnostic_metrics || [];
    const percentiles = data.percentiles || ["mean", "p50", "p90", "p95", "p99"];

    // Attach scenario metadata (label, batch/beam/input) to each run so
    // renderers read the same shape they would on the GPU dashboard.
    function decorate(run) {
      const meta = scenarioMeta.get(run.scenario_key) || {};
      run.scenario = {
        key: run.scenario_key,
        label: meta.label || run.scenario_key,
        batch_size: meta.batch_size,
        beam_width: meta.beam_width,
        input_len: meta.input_len,
      };
      run.runId = `${run.scenario_key}/${run.date}`;
      return run;
    }
    runs.forEach(decorate);

    let selectedId = runs.length ? runs[runs.length - 1].runId : null;

    scenarioSelect.innerHTML = ['<option value="all">All scenarios</option>', ...(data.scenarios || []).map((scenario) => `<option value="${escapeHtml(scenario.key)}">${escapeHtml(scenario.label)}</option>`)].join("");
    const scenariosWithRuns = [...new Set(runs.map((run) => run.scenario_key))];
    if (scenariosWithRuns.length) scenarioSelect.value = scenariosWithRuns[scenariosWithRuns.length - 1];
    percentileSelect.innerHTML = percentiles.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item.toUpperCase())}</option>`).join("");
    percentileSelect.value = "mean";

    function filteredRuns() {
      return runs.filter((run) => scenarioSelect.value === "all" || run.scenario_key === scenarioSelect.value);
    }

    function selectedRun(runsForScenario) {
      return runsForScenario.find((run) => run.runId === selectedId)
        || runsForScenario[runsForScenario.length - 1]
        || null;
    }

    function refresh() {
      const scenarioRuns = filteredRuns();
      const selected = selectedRun(scenarioRuns);
      if (selected) selectedId = selected.runId;
      const percentile = percentileSelect.value;
      const statLabel = percentile.toUpperCase();
      count.textContent = `${scenarioRuns.length} run${scenarioRuns.length === 1 ? "" : "s"} shown`;
      missHitTitle.textContent = `Prefill and Decode Miss/Hit breakdown · ${statLabel}`;
      coreTrendsTitle.textContent = `${coreMetrics.length} core metric trends · ${statLabel}`;
      diagnosticTrendsTitle.textContent = `${diagnosticMetrics.length} compute and wait trends · ${statLabel}`;
      renderLatest(latest, selected);
      renderDailyChange(dailyChange, selected);
      renderConfig(config, selected);
      renderMissHitBreakdown(missHitBreakdown, selected, percentile);
      renderTrendGrid(coreTrendGrid, scenarioRuns, percentile, coreMetrics);
      renderTrendGrid(diagnosticTrendGrid, scenarioRuns, percentile, diagnosticMetrics);
      renderLatencyGrid(latencyGrid, selected);
      renderBeamProfile(beamProfile, selected);
      // Deliberately not passed `percentile`: the decomposition is additive for
      // means only.
      renderStageFigure(stageFigure, selected);
      renderRunHistory(history, scenarioRuns, selectedId, (runId) => {
        selectedId = runId;
        refresh();
      });
    }

    [scenarioSelect, percentileSelect].forEach((control) => control.addEventListener("change", refresh));
    refresh();
  }

  // ------------------------------------------------------ boot -------------
  async function boot() {
    const hasRoot = injectDeviceToggle();
    if (!hasRoot) return;
    const root = document.getElementById("npu-v2-dashboard");
    try {
      const url = new URL("../npu-vllm-gr-v2-data.json", window.location.href);
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load npu-vllm-gr-v2-data.json (${response.status})`);
      initDashboard(await response.json());
    } catch (error) {
      root.insertAdjacentHTML("afterbegin", `<div class="vgr-empty is-error">${escapeHtml(error.message)}</div>`);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();