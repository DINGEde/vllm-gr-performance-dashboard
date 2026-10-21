(function () {
  // ------------------------------------------------------ device toggle ----
  // Pure text GPU/NPU switch. Injected at runtime on every dashboard page so
  // the generated GPU page (vllm-gr.md) never needs to be modified: this file
  // simply adds the toggle on top of whatever dashboard is present.
  const DEVICE_TOGGLE_PAGES = { gpu: "vgr-dashboard", npu: "npu-dashboard" };

  function siblingPageUrl(name) {
    const path = window.location.pathname;
    if (/\/[^/]*\.html$/i.test(path)) return name + ".html";
    return "../" + name + "/";
  }

  function deviceToggle(active) {
    const buttons = Object.keys(DEVICE_TOGGLE_PAGES).map((device) =>
      `<button type="button" class="npu-device-btn${device === active ? " is-active" : ""}" data-device="${device}">${device.toUpperCase()}</button>`
    ).join("");
    return `<div class="npu-device-toggle" role="group" aria-label="Device">${buttons}</div>`;
  }

  function injectDeviceToggle() {
    for (const [device, rootId] of Object.entries(DEVICE_TOGGLE_PAGES)) {
      const root = document.getElementById(rootId);
      if (!root) continue;
      root.insertAdjacentHTML("afterbegin", deviceToggle(device));
      root.querySelectorAll(".npu-device-btn").forEach((button) => {
        button.addEventListener("click", () => {
          const target = button.getAttribute("data-device");
          if (target === device) return;
          window.location.href = siblingPageUrl(target === "gpu" ? "vllm-gr" : "npu-vllm-gr");
        });
      });
      return device;
    }
    return null;
  }

  // ------------------------------------------------------ NPU dashboard ----
  const LATENCY_STATS = ["mean", "p50", "p95", "min", "max"];
  const TREND_METRICS = [
    { key: "latency", label: "Batch latency", unit: "ms", latency: true },
    { key: "returned_beams_per_second", label: "Returned beams / s", unit: "beams/s" },
    { key: "requests_per_second", label: "Requests / s", unit: "req/s" },
  ];

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

  function metricValue(run, metric, stat) {
    if (metric.latency) return number(run.latency_ms?.[stat]);
    return number(run[metric.key]);
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

  // One stable color per scenario, in first-seen order. The trend charts, the
  // legend and the run history all share this mapping so colors always match.
  const SCENARIO_PALETTE = ["#0f8f86", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666", "#1b9e77", "#d95f7f", "#4477aa"];

  function scenarioGroups(points) {
    const groups = [];
    const byKey = new Map();
    for (const point of points) {
      const key = point.scenario_key || "scenario";
      if (!byKey.has(key)) {
        const group = { key, label: point.scenario_label || key, color: SCENARIO_PALETTE[groups.length % SCENARIO_PALETTE.length], points: [] };
        byKey.set(key, group);
        groups.push(group);
      }
      byKey.get(key).points.push(point);
    }
    groups.forEach((group) => group.points.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0)));
    return groups;
  }

  function lineChart(points, dates, metric, stat, unit, groups) {
    const multi = groups.length > 1;
    const drawGroups = groups
      .map((group) => ({ ...group, points: group.points.filter((point) => metricValue(point, metric, stat) !== null) }))
      .filter((group) => group.points.length);
    const values = drawGroups.flatMap((group) => group.points.map((point) => metricValue(point, metric, stat)));
    if (!values.length) return '<div class="vgr-empty">No values are available for this selection.</div>';

    const width = 1040;
    const height = 300;
    const left = 72;
    const right = 28;
    const top = 28;
    const bottom = 64;
    const plotW = width - left - right;
    const plotH = height - top - bottom;
    const domain = yDomain(values);
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
    // Segments connect only consecutive dates *within* one scenario, so
    // unrelated scenarios sharing a run date never zig-zag together.
    const segments = drawGroups.map((group) => group.points.slice(1).map((point, index) => {
      const previous = group.points[index];
      const stroke = multi ? ` stroke="${group.color}"` : "";
      return `<line x1="${x(previous.date)}" y1="${y(metricValue(previous, metric, stat))}" x2="${x(point.date)}" y2="${y(metricValue(point, metric, stat))}" class="vgr-trend-line"${stroke}><title>${escapeHtml(group.label)} · ${escapeHtml(metric.label)}</title></line>`;
    }).join("")).join("");
    const marks = drawGroups.map((group) => group.points.map((point) => {
      const xx = x(point.date);
      const yy = y(metricValue(point, metric, stat));
      const title = `${point.date} · ${group.label} · ${metric.label} ${fmt(metricValue(point, metric, stat))} ${unit} · ${point.source || ""}`;
      const fill = multi ? ` fill="${group.color}"` : "";
      // In multi-scenario mode the numeric labels would overlap into noise,
      // so they are only drawn for single-scenario lines.
      const valueLabel = !multi && labeledDates.has(point.date)
        ? `<text x="${xx}" y="${yy - 13}" text-anchor="middle" class="vgr-value-label">${escapeHtml(fmt(metricValue(point, metric, stat)))}</text>`
        : "";
      return `<g class="vgr-point"><circle cx="${xx}" cy="${yy}" r="6"${fill}><title>${escapeHtml(title)}</title></circle>${valueLabel}</g>`;
    }).join("")).join("");
    const xLabels = labelIndexes.map((index) => `<text x="${x(dates[index])}" y="${height - 34}" text-anchor="middle" class="vgr-axis-label">${escapeHtml(dates[index].slice(5))}</text>`).join("");
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(metric.label)} daily trend"><text x="18" y="${top + plotH / 2}" transform="rotate(-90 18 ${top + plotH / 2})" text-anchor="middle" class="vgr-axis-title">${escapeHtml(metric.label)} (${escapeHtml(unit)})</text>${grid.join("")}${segments}${marks}${xLabels}<text x="${left + plotW / 2}" y="${height - 4}" text-anchor="middle" class="vgr-axis-title">Run date</text></svg>`;
  }

  function renderLatest(root, run, stat) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No runs match the current filters.</div>';
      return;
    }
    const graph = run.graph || {};
    const config = run.config || {};
    const modelShort = run.model || "unknown";
    const kpis = [
      [`Batch latency ${stat.toUpperCase()}`, fmt(run.latency_ms?.[stat]), "ms", LATENCY_STATS.filter((key) => key !== stat).map((key) => `${key.toUpperCase()} ${fmt(run.latency_ms?.[key])} ms`).join(" · ")],
      ["Returned beams / s", fmt(run.returned_beams_per_second), "beams/s", `requests/s ${fmt(run.requests_per_second)}`],
      ["Graph fallbacks", fmt(graph.fallbacks ?? 0), "", `captured shapes ${(graph.captured_shapes || []).map((shape) => `${shape[0]}x${shape[1]}`).join(", ") || "N/A"}`],
      ["Timed repeats", fmt(run.repeats?.length ?? run.repeat), "", `warmup runs ${fmt(run.warmup_runs)}`],
    ].map(([label, value, suffix, hint]) => kpi(label, value, suffix, hint)).join("");
    root.innerHTML = `
      <div class="vgr-hero-copy">
        <div class="vgr-hero-label"><span>${escapeHtml(run.date)} · Ascend 910B1 NPU</span></div>
        <h2>${escapeHtml(run.scenario_label || run.scenario_key)}</h2>
        <p>${escapeHtml(modelShort)} · constraint backend ${escapeHtml(config.constraint_backend || "npu")} · ${escapeHtml(config.execution || "graph")} execution</p>
        <div class="vgr-tags">
          <span>batch ${escapeHtml(run.scenario_batch)}</span>
          <span>beam ${escapeHtml(run.scenario_beam)}</span>
          <span>input ${escapeHtml(run.scenario_input)} tokens</span>
          <span>runner ${escapeHtml(graph.runner || "NPUModelRunner")}</span>
          <span>beam graph ${graph.beam_graph_enabled ? "enabled" : "disabled"}</span>
          <span>${escapeHtml(run.source || "")}</span>
        </div>
      </div>
      <div class="vgr-kpi-grid">${kpis}</div>
    `;
  }

  function renderTrendGrid(root, runs, dates, stat, groups) {
    root.innerHTML = TREND_METRICS.map((metric) => `
      <article class="vgr-trend-card">
        <div class="vgr-trend-card-head"><strong>${escapeHtml(metric.label)}${metric.latency ? ` · ${escapeHtml(stat.toUpperCase())}` : ""}</strong><span>daily · ${escapeHtml(metric.unit)}</span></div>
        <div class="vgr-chart">${lineChart(runs, dates, metric, stat, metric.unit, groups)}</div>
      </article>
    `).join("");
  }

  function renderLatencyGrid(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const labels = { mean: "Mean", p50: "P50", p95: "P95", min: "Min", max: "Max" };
    root.innerHTML = `<div class="vgr-latency-cards">${LATENCY_STATS.map((stat) => `
      <article class="vgr-latency-card"><div><strong>${escapeHtml(labels[stat])}</strong><small>batch latency</small></div><dl><dt>Value</dt><dd>${fmt(run.latency_ms?.[stat])} ms</dd></dl></article>
    `).join("")}</div>`;
  }

  function renderRepeatFigure(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const repeats = (run.repeats || []).filter((item) => number(item.batch_latency_ms) !== null);
    if (!repeats.length) {
      root.innerHTML = '<div class="vgr-empty">No per-repeat samples were recorded for this run.</div>';
      return;
    }
    const values = repeats.map((item) => item.batch_latency_ms);
    const max = Math.max(...values);
    const bars = repeats.map((item, index) => {
      const width = max ? (item.batch_latency_ms / max) * 100 : 0;
      return `<div class="npu-repeat-row"><span class="npu-repeat-label">run ${escapeHtml(item.run ?? index + 1)}</span><span class="npu-repeat-track"><span class="npu-repeat-bar" style="width:${width.toFixed(2)}%"></span></span><strong>${fmt(item.batch_latency_ms)} ms</strong></div>`;
    }).join("");
    root.innerHTML = `<div class="npu-repeat-figure">${bars}</div>`;
  }

  function renderConfig(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const config = run.config || {};
    const graph = run.graph || {};
    const rows = [
      ["Device", "Ascend 910B1 NPU"],
      ["Model", config.model],
      ["Catalog", config.catalog],
      ["Prompt", config.prompt],
      ["Constraint backend", config.constraint_backend],
      ["Execution", config.execution],
      ["Batch size", run.scenario_batch],
      ["Beam width", run.scenario_beam],
      ["Input length", `${run.scenario_input} tokens`],
      ["Generated steps", config.generated_steps],
      ["Length penalty", config.length_penalty],
      ["Max model len", config.max_model_len],
      ["Memory utilization", config.gpu_memory_utilization],
      ["Measured / warmup", `${run.repeats?.length ?? run.repeat} / ${run.warmup_runs}`],
      ["Prefix caching", config.prefix_caching],
      ["Beam graph", graph.beam_graph_enabled ? `enabled · max width ${graph.beam_max_width}` : "disabled / eager"],
      ["Captured shapes", (graph.captured_shapes || []).map((shape) => `${shape[0]}x${shape[1]}`).join(", ") || "N/A"],
      ["Full dispatches", graph.full_dispatches],
      ["Result file", run.source],
    ].filter(([, value]) => value !== undefined && value !== null && value !== "");
    root.innerHTML = `<dl class="vgr-config-grid">${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
  }

  function renderRunHistory(root, runs, selectedKey, onSelect, groups) {
    if (!runs.length) {
      root.innerHTML = '<div class="vgr-empty">No runs match the current filters.</div>';
      return;
    }
    const multi = groups.length > 1;
    const colorByKey = new Map(groups.map((group) => [group.key, group.color]));
    root.innerHTML = `<div class="vgr-run-list">${runs.slice().reverse().map((run) => {
      const active = `${run.scenario_key}/${run.date}` === selectedKey ? " is-active" : "";
      const color = multi && colorByKey.has(run.scenario_key) ? colorByKey.get(run.scenario_key) : null;
      const rowStyle = color ? ` style="border-left:3px solid ${color}"` : "";
      return `<div class="vgr-run-row${active}"${rowStyle}><button type="button" class="vgr-run-select" data-run-key="${escapeHtml(`${run.scenario_key}/${run.date}`)}"><span class="vgr-run-date">${escapeHtml(run.date)}</span><span class="vgr-run-main"><strong>${escapeHtml(run.scenario_label || run.scenario_key)}</strong><small>${escapeHtml(run.model || "")} · ${escapeHtml(run.source || "")}</small></span><span class="vgr-run-result">${fmt(run.latency_ms?.mean)} ms</span></button></div>`;
    }).join("")}</div>`;
    root.querySelectorAll(".vgr-run-select").forEach((button) => {
      button.addEventListener("click", () => onSelect(button.getAttribute("data-run-key")));
    });
  }

  function initDashboard(data) {
    const root = document.getElementById("npu-dashboard");
    if (!root) return;
    const scenarioSelect = document.getElementById("npu-scenario");
    const statSelect = document.getElementById("npu-percentile");
    const count = document.getElementById("npu-count");
    const latest = document.getElementById("npu-latest");
    const trendGrid = document.getElementById("npu-trend-grid");
    const trendsTitle = document.getElementById("npu-trends-title");
    const latencyGrid = document.getElementById("npu-latency-grid");
    const repeatFigure = document.getElementById("npu-repeat-figure");
    const config = document.getElementById("npu-config");
    const history = document.getElementById("npu-run-history");

    const runs = data.runs || [];
    const dates = [...new Set(runs.map((run) => run.date))].sort();
    const scenarioMeta = new Map((data.scenarios || []).map((item) => [item.key, item]));
    let selectedKey = null;
    if (runs.length) {
      const last = runs[runs.length - 1];
      selectedKey = `${last.scenario_key}/${last.date}`;
    }

    scenarioSelect.innerHTML = ['<option value="all">All scenarios</option>', ...(data.scenarios || []).map((scenario) => `<option value="${escapeHtml(scenario.key)}">${escapeHtml(scenario.label)}</option>`)].join("");
    const scenariosWithRuns = [...new Set(runs.map((run) => run.scenario_key))];
    if (scenariosWithRuns.length) scenarioSelect.value = scenariosWithRuns[scenariosWithRuns.length - 1];
    statSelect.innerHTML = LATENCY_STATS.map((stat) => `<option value="${escapeHtml(stat)}">${escapeHtml(stat.toUpperCase())}</option>`).join("");
    statSelect.value = "mean";

    function decorate(run) {
      const meta = scenarioMeta.get(run.scenario_key) || {};
      return {
        ...run,
        scenario_label: meta.label || run.scenario_key,
        scenario_batch: meta.batch_size ?? "?",
        scenario_beam: meta.beam_width ?? "?",
        scenario_input: meta.input_len ?? "?",
      };
    }

    function filteredRuns() {
      return runs.filter((run) => scenarioSelect.value === "all" || run.scenario_key === scenarioSelect.value).map(decorate);
    }

    function selectedRun(runsForScenario) {
      return runsForScenario.find((run) => `${run.scenario_key}/${run.date}` === selectedKey)
        || runsForScenario[runsForScenario.length - 1]
        || null;
    }

    function refresh() {
      const scenarioRuns = filteredRuns();
      const groups = scenarioGroups(scenarioRuns);
      const selected = selectedRun(scenarioRuns);
      if (selected) selectedKey = `${selected.scenario_key}/${selected.date}`;
      const stat = statSelect.value;
      count.textContent = `${scenarioRuns.length} run${scenarioRuns.length === 1 ? "" : "s"} shown`;
      trendsTitle.textContent = `Daily latency history · ${stat.toUpperCase()}`;
      renderLatest(latest, selected, stat);
      renderTrendGrid(trendGrid, scenarioRuns, dates, stat, groups);
      renderLatencyGrid(latencyGrid, selected);
      renderRepeatFigure(repeatFigure, selected);
      renderConfig(config, selected);
      renderRunHistory(history, scenarioRuns, selectedKey, (key) => {
        selectedKey = key;
        refresh();
      }, groups);
    }

    [scenarioSelect, statSelect].forEach((control) => control.addEventListener("change", refresh));
    refresh();
  }

  async function boot() {
    const device = injectDeviceToggle();
    if (device !== "npu") return;
    const root = document.getElementById("npu-dashboard");
    try {
      const url = new URL("../npu-vllm-gr-data.json", window.location.href);
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load npu-vllm-gr-data.json (${response.status})`);
      initDashboard(await response.json());
    } catch (error) {
      root.insertAdjacentHTML("afterbegin", `<div class="vgr-empty is-error">${escapeHtml(error.message)}</div>`);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
