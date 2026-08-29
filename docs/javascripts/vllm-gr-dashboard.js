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
    if (metric === "request_throughput") return number(run.results?.throughput?.requests_per_second);
    if (metric === "output_throughput") return number(run.results?.throughput?.output_tokens_per_second);
    if (metric === "cache_hit") return number(run.results?.cache?.prefix?.hit_rate_percent);
    return number(run.results?.latency_ms?.[metric]?.[percentile]);
  }

  function scenarioKey(run) {
    return run.scenario?.key || `beam${run.scenario?.n ?? "unknown"}-legacy`;
  }

  function metricMeta(data, key) {
    return data.metrics.find((item) => item.key === key) || { key, label: key, unit: "" };
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
    const ttft = run.results.latency_ms.ttft;
    const e2el = run.results.latency_ms.e2el;
    const requests = run.results.requests;
    const reasons = run.run.qualification_reasons || [];
    root.innerHTML = `
      <div class="vgr-hero-copy">
        <div class="vgr-hero-label">${statusBadge(run)}<span>${escapeHtml(run.run.date)} · ${escapeHtml(run.environment.host)}</span></div>
        <h2>${escapeHtml(run.scenario.name)}</h2>
        <p>${escapeHtml(run.model.id)} · ${escapeHtml(run.dataset.name)}</p>
        <div class="vgr-tags">
          <span>${escapeHtml(run.dataset.kind)}</span>
          <span>concurrency ${escapeHtml(run.scenario.max_concurrency)}</span>
          <span>beam n=${escapeHtml(run.scenario.n)}</span>
          <span>input ${escapeHtml(run.scenario.input_tokens_target ?? "dataset")} tokens</span>
          <span>${escapeHtml(requests.completed)} passed / ${escapeHtml(requests.failed)} failed</span>
        </div>
      </div>
      <div class="vgr-kpi-grid">
        ${kpi("TTFT P50", fmt(ttft.p50), "ms", `P90 ${fmt(ttft.p90)} ms`)}
        ${kpi("E2EL P50", fmt(e2el.p50), "ms", `P90 ${fmt(e2el.p90)} ms`)}
        ${kpi("Request throughput", fmt(run.results.throughput.requests_per_second), "req/s", `${fmt(run.results.duration_seconds)} s measured`)}
        ${kpi("Output throughput", fmt(run.results.throughput.output_tokens_per_second), "tok/s", run.results.tokens.output_semantics)}
        ${kpi("Prefix cache hit", fmt(run.results.cache?.prefix?.hit_rate_percent), "%", "hit tokens / queried tokens")}
      </div>
      ${reasons.length ? `<div class="vgr-qualification"><strong>Excluded from baseline</strong><ul>${reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul></div>` : ""}
    `;
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

  function barChart(values) {
    if (!values.length) return '<div class="vgr-empty">This run does not include per-request TTFT samples.</div>';
    const width = 760;
    const height = 320;
    const left = 66;
    const right = 20;
    const top = 24;
    const bottom = 54;
    const plotW = width - left - right;
    const plotH = height - top - bottom;
    const max = Math.max(...values) * 1.1 || 1;
    const slot = plotW / values.length;
    const barW = Math.max(8, slot * 0.62);
    const tickCount = Math.min(6, values.length);
    const tickIndices = new Set(values.length === 1
      ? [0]
      : Array.from({ length: tickCount }, (_, index) => Math.round((index * (values.length - 1)) / (tickCount - 1))));
    const grid = [];
    for (let tick = 0; tick <= 4; tick += 1) {
      const value = max - (max * tick) / 4;
      const yy = top + (plotH * tick) / 4;
      grid.push(`<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" class="vgr-grid-line"/><text x="${left - 10}" y="${yy + 4}" text-anchor="end" class="vgr-axis-label">${escapeHtml(fmt(value, 0))}</text>`);
    }
    const bars = values.map((value, index) => {
      const barH = (value / max) * plotH;
      const xx = left + index * slot + (slot - barW) / 2;
      const yy = top + plotH - barH;
      const wave = index < 4 ? "vgr-bar is-jit" : "vgr-bar";
      const tickLabel = tickIndices.has(index)
        ? `<text x="${xx + barW / 2}" y="${height - 24}" text-anchor="middle" class="vgr-axis-label">R${index + 1}</text>`
        : "";
      return `<g><rect x="${xx}" y="${yy}" width="${barW}" height="${barH}" rx="4" class="${wave}"><title>Request ${index + 1}: ${escapeHtml(fmt(value))} ms</title></rect>${tickLabel}</g>`;
    });
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Per-request TTFT in milliseconds"><text x="18" y="${top + plotH / 2}" transform="rotate(-90 18 ${top + plotH / 2})" text-anchor="middle" class="vgr-axis-title">TTFT (ms)</text>${grid.join("")}${bars.join("")}<text x="${left + plotW / 2}" y="${height - 2}" text-anchor="middle" class="vgr-axis-title">Request sequence</text></svg><div class="vgr-chart-legend"><span><i class="is-jit"></i>First concurrency wave / JIT affected</span><span><i></i>Following requests</span></div>`;
  }

  function renderLatencyGrid(root, run) {
    if (!run) {
      root.innerHTML = '<div class="vgr-empty">No run selected.</div>';
      return;
    }
    const labels = { ttft: "TTFT", e2el: "E2EL", tpot: "TPOT", itl: "ITL" };
    root.innerHTML = `<div class="vgr-latency-cards">${["ttft", "e2el", "tpot", "itl"].map((key) => {
      const value = run.results.latency_ms[key];
      const caution = ["tpot", "itl"].includes(key) && run.results.tokens.output_semantics === "beam-aggregate";
      return `<article class="vgr-latency-card${caution ? " is-caution" : ""}"><div><strong>${labels[key]}</strong>${caution ? "<span>beam aggregate</span>" : ""}</div><dl><dt>P50</dt><dd>${fmt(value.p50)} ms</dd><dt>P90</dt><dd>${fmt(value.p90)} ms</dd><dt>P95</dt><dd>${fmt(value.p95)} ms</dd><dt>P99</dt><dd>${fmt(value.p99)} ms</dd></dl></article>`;
    }).join("")}</div>`;
  }

  function renderRunHistory(root, runs, selectedId, onSelect) {
    if (!runs.length) {
      root.innerHTML = '<div class="vgr-empty">No runs match the current filters.</div>';
      return;
    }
    root.innerHTML = `<div class="vgr-run-list">${runs.slice().reverse().map((run) => {
      const active = run.run.id === selectedId ? " is-active" : "";
      const reasons = run.run.qualification_reasons || [];
      return `<button type="button" class="vgr-run-row${active}" data-run-id="${escapeHtml(run.run.id)}"><span class="vgr-run-date">${escapeHtml(run.run.date)}</span><span class="vgr-run-main"><strong>${escapeHtml(run.scenario.name)}</strong><small>${escapeHtml(run.source.git_sha.slice(0, 8))} · ${escapeHtml(run.dataset.kind)} · ${escapeHtml(run.environment.host)}</small></span><span class="vgr-run-result">${escapeHtml(run.results.requests.completed)}/${escapeHtml(run.scenario.num_prompts)}<small>${reasons.length ? `${reasons.length} qualification flags` : "qualified"}</small></span>${statusBadge(run)}</button>`;
    }).join("")}</div>`;
    root.querySelectorAll(".vgr-run-row").forEach((button) => {
      button.addEventListener("click", () => onSelect(button.getAttribute("data-run-id")));
    });
  }

  function initDashboard(data) {
    const root = document.getElementById("vgr-dashboard");
    if (!root) return;
    const hostSelect = document.getElementById("vgr-host");
    const scenarioSelect = document.getElementById("vgr-scenario");
    const metricSelect = document.getElementById("vgr-metric");
    const percentileSelect = document.getElementById("vgr-percentile");
    const qualifiedOnly = document.getElementById("vgr-qualified-only");
    const count = document.getElementById("vgr-count");
    const latest = document.getElementById("vgr-latest");
    const trendChart = document.getElementById("vgr-trend-chart");
    const trendTitle = document.getElementById("vgr-trend-title");
    const trendCaption = document.getElementById("vgr-trend-caption");
    const ttftChart = document.getElementById("vgr-ttft-chart");
    const latencyGrid = document.getElementById("vgr-latency-grid");
    const history = document.getElementById("vgr-run-history");
    let selectedId = data.runs.length ? data.runs[data.runs.length - 1].run.id : null;

    hostSelect.innerHTML = ['<option value="all">All hosts</option>', ...(data.hosts || []).map((host) => `<option value="${escapeHtml(host)}">${escapeHtml(host)}</option>`)].join("");
    scenarioSelect.innerHTML = ['<option value="all">All scenarios</option>', ...(data.scenarios || []).map((scenario) => `<option value="${escapeHtml(scenario.key)}">${escapeHtml(scenario.label)}</option>`)].join("");
    if ((data.scenarios || []).length) scenarioSelect.value = data.scenarios[data.scenarios.length - 1].key;
    metricSelect.innerHTML = data.metrics.map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`).join("");
    metricSelect.value = "e2el";
    percentileSelect.innerHTML = data.percentiles.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item.toUpperCase())}</option>`).join("");
    percentileSelect.value = "p50";

    function filteredRuns() {
      return data.runs.filter((run) => {
        if (hostSelect.value !== "all" && run.environment.host !== hostSelect.value) return false;
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
      const metric = metricSelect.value;
      const percentile = percentileSelect.value;
      const meta = metricMeta(data, metric);
      const percentileApplies = !["request_throughput", "output_throughput", "cache_hit"].includes(metric);
      percentileSelect.disabled = !percentileApplies;
      const statLabel = percentileApplies ? percentile.toUpperCase() : "measured";
      count.textContent = `${runs.length} run${runs.length === 1 ? "" : "s"} shown · ${data.trend_runs.length} trend qualified`;
      trendTitle.textContent = `${meta.label} · ${statLabel}`;
      trendCaption.textContent = qualifiedOnly.checked ? "Qualified daily runs only" : "All runs; hollow qualification is preserved in details";
      trendChart.innerHTML = lineChart(runs, metric, percentile, meta);
      renderLatest(latest, selected);
      ttftChart.innerHTML = selected ? barChart(selected.results.samples.ttft_ms || []) : '<div class="vgr-empty">No run selected.</div>';
      renderLatencyGrid(latencyGrid, selected);
      renderRunHistory(history, runs, selectedId, (runId) => {
        selectedId = runId;
        refresh();
      });
    }

    [hostSelect, scenarioSelect, metricSelect, percentileSelect, qualifiedOnly].forEach((control) => control.addEventListener("change", refresh));
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
