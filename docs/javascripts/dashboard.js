(function () {
  const SHAPE_FALLBACK_COLORS = {
    "4/2": "#2563eb",
    "8/4": "#0891b2",
    "16/8": "#15803d",
    "32/16": "#b45309",
    "64/32": "#7c3aed",
  };

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmt(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
    const abs = Math.abs(value);
    if (abs >= 100) return value.toFixed(1);
    if (abs >= 10) return value.toFixed(2);
    return value.toFixed(3);
  }

  function deltaArrow(baseline, router) {
    if (baseline === null || baseline === undefined || router === null || router === undefined || baseline === 0) {
      return "";
    }
    const delta = ((router - baseline) / baseline) * 100;
    if (Math.abs(delta) < 0.05) return { text: "→0.0%", css: "delta-flat" };
    if (delta > 0) return { text: `↑${Math.abs(delta).toFixed(1)}%`, css: "delta-up" };
    return { text: `↓${Math.abs(delta).toFixed(1)}%`, css: "delta-down" };
  }

  function parseDate(value) {
    return new Date(`${value}T00:00:00Z`);
  }

  function isoDate(date) {
    return date.toISOString().slice(0, 10);
  }

  function filterByHardware(days, hardware) {
    if (!hardware) return days.slice();
    return days.filter((day) => day.hardware === hardware);
  }

  function dateBounds(days) {
    if (!days.length) return { min: "", max: "" };
    return days.reduce(
      (bounds, day) => ({
        min: !bounds.min || day.date < bounds.min ? day.date : bounds.min,
        max: !bounds.max || day.date > bounds.max ? day.date : bounds.max,
      }),
      { min: "", max: "" }
    );
  }

  function rangeStartFor(maxDate, rangeDays) {
    const max = parseDate(maxDate);
    const min = new Date(max.getTime());
    min.setUTCDate(min.getUTCDate() - (rangeDays - 1));
    return isoDate(min);
  }

  function filterDays(days, hardware, rangeDays) {
    let filtered = filterByHardware(days, hardware);
    if (!filtered.length || !rangeDays) return filtered;
    const { max } = dateBounds(filtered);
    const minStr = rangeStartFor(max, rangeDays);
    return filtered.filter((day) => day.date >= minStr);
  }

  function filterDaysByInterval(days, hardware, startDate, endDate) {
    let filtered = filterByHardware(days, hardware);
    if (startDate) filtered = filtered.filter((day) => day.date >= startDate);
    if (endDate) filtered = filtered.filter((day) => day.date <= endDate);
    return filtered;
  }

  function sampledIndices(total, plotWidth, minSpacing) {
    if (total <= 2) return [...Array(total).keys()];
    const maxLabels = Math.max(2, Math.floor(plotWidth / minSpacing) + 1);
    if (total <= maxLabels) return [...Array(total).keys()];
    const stride = Math.ceil((total - 1) / (maxLabels - 1));
    const indices = [];
    for (let i = 0; i < total - 1; i += stride) indices.push(i);
    if (indices[indices.length - 1] !== total - 1) indices.push(total - 1);
    return indices;
  }

  function renderTrendSvg(days, metric, shapes, colors) {
    const title = "Hover one series to isolate it and show baseline";
    const width = 1100;
    const height = 440;
    const left = 96;
    const right = 24;
    const top = 48;
    const bottom = 140;
    const labels = days.map((day) => `${day.date} · ${day.host}`);
    const values = [];
    shapes.forEach((shape) => {
      days.forEach((day) => {
        const baseline = day.metrics?.[shape]?.baseline?.[metric];
        const router = day.metrics?.[shape]?.router?.[metric];
        if (baseline !== null && baseline !== undefined) values.push(baseline);
        if (router !== null && router !== undefined) values.push(router);
      });
    });
    if (!values.length) {
      return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#ffffff"/><text x="20" y="30" fill="#5d7082">${escapeHtml(
        title
      )}: no data</text></svg>`;
    }
    let ymin = Math.min(0, ...values);
    let ymax = Math.max(...values);
    if (ymin === ymax) ymax += 1;
    const plotW = width - left - right;
    const plotH = height - top - bottom;
    const xAt = (index) => left + (plotW * index) / Math.max(1, labels.length - 1);
    const yAt = (value) => top + plotH - ((value - ymin) / (ymax - ymin)) * plotH;
    const pointsFor = (getter) =>
      days
        .map((day, index) => {
          const value = getter(day);
          return value === null || value === undefined ? null : `${xAt(index).toFixed(1)},${yAt(value).toFixed(1)}`;
        })
        .filter(Boolean)
        .join(" ");

    const parts = [
      `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" class="metric-trend-chart" role="img">`,
      `<rect width="100%" height="100%" fill="#ffffff"/>`,
      `<style>
.metric-trend-chart .router-layer,
.metric-trend-chart .baseline-layer,
.metric-trend-chart .legend-hit { transition: opacity 0.15s ease; }
.metric-trend-chart .baseline-layer { opacity: 0; pointer-events: none; }
.metric-trend-chart .router-hit { fill: none; stroke: transparent; stroke-width: 16; cursor: pointer; }
.metric-trend-chart .legend-hit { cursor: pointer; }
.metric-trend-chart:has(.series-group:hover) .series-group:not(:hover) .router-layer,
.metric-trend-chart:has(.series-group:hover) .series-group:not(:hover) .legend-hit,
.metric-trend-chart:has(.series-group:focus-within) .series-group:not(:focus-within) .router-layer,
.metric-trend-chart:has(.series-group:focus-within) .series-group:not(:focus-within) .legend-hit { opacity: 0; }
.metric-trend-chart .series-group:hover .baseline-layer,
.metric-trend-chart .series-group:focus-within .baseline-layer { opacity: 1; }
</style>`,
      `<text x="${left}" y="28" font-size="14" fill="#5d7082" font-family="Segoe UI, sans-serif">${escapeHtml(title)}</text>`,
    ];
    [ymin, (ymin + ymax) / 2, ymax].forEach((yv) => {
      const y = yAt(yv);
      parts.push(`<line x1="${left}" y1="${y}" x2="${left + plotW}" y2="${y}" stroke="#e6eef3"/>`);
      parts.push(
        `<text x="${left - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="#6b7f90" font-family="Segoe UI, sans-serif">${fmt(yv)}</text>`
      );
    });
    parts.push(`<line x1="${left}" y1="${top + plotH}" x2="${left + plotW}" y2="${top + plotH}" stroke="#9eb2c2"/>`);
    parts.push(`<line x1="${left}" y1="${top}" x2="${left}" y2="${top + plotH}" stroke="#9eb2c2"/>`);
    const labelY = top + plotH + 28;
    sampledIndices(labels.length, plotW, 120).forEach((index) => {
      const x = xAt(index);
      parts.push(
        `<text x="${x}" y="${labelY}" text-anchor="end" font-size="11" fill="#6b7f90" font-family="Segoe UI, sans-serif" transform="rotate(-35 ${x} ${labelY})">${escapeHtml(
          labels[index]
        )}</text>`
      );
    });

    const legendY = height - 28;
    const legendWidth = plotW / Math.max(1, shapes.length);
    shapes.forEach((shape, shapeIndex) => {
      const color = colors[shape] || SHAPE_FALLBACK_COLORS[shape] || "#334155";
      const shapeId = shape.replace("/", "_");
      const routerPoints = pointsFor((day) => day.metrics?.[shape]?.router?.[metric]);
      const baselinePoints = pointsFor((day) => day.metrics?.[shape]?.baseline?.[metric]);
      parts.push(`<g class="series-group" data-shape="${shapeId}" tabindex="0">`);
      parts.push(`<g class="baseline-layer">`);
      if (baselinePoints) {
        parts.push(
          `<polyline fill="none" stroke="#334155" stroke-width="2.2" stroke-dasharray="6 5" stroke-linecap="round" stroke-linejoin="round" points="${baselinePoints}"/>`
        );
        days.forEach((day, index) => {
          const value = day.metrics?.[shape]?.baseline?.[metric];
          if (value === null || value === undefined) return;
          parts.push(
            `<circle cx="${xAt(index).toFixed(1)}" cy="${yAt(value).toFixed(1)}" r="3.5" fill="#ffffff" stroke="#334155" stroke-width="2"/>`
          );
        });
      }
      parts.push(`</g><g class="router-layer">`);
      if (routerPoints) {
        parts.push(`<polyline class="router-hit" points="${routerPoints}"/>`);
        parts.push(
          `<polyline fill="none" stroke="${color}" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" points="${routerPoints}"/>`
        );
        days.forEach((day, index) => {
          const value = day.metrics?.[shape]?.router?.[metric];
          if (value === null || value === undefined) return;
          parts.push(
            `<circle cx="${xAt(index).toFixed(1)}" cy="${yAt(value).toFixed(1)}" r="4" fill="#ecfeff" stroke="${color}" stroke-width="2"/>`
          );
        });
      }
      parts.push(`</g>`);
      const legendX = left + shapeIndex * legendWidth;
      parts.push(
        `<g class="legend-hit"><line x1="${legendX.toFixed(1)}" y1="${legendY}" x2="${(legendX + 18).toFixed(
          1
        )}" y2="${legendY}" stroke="${color}" stroke-width="3"/><text x="${(legendX + 24).toFixed(
          1
        )}" y="${legendY + 4}" font-size="12" fill="${color}" font-family="Segoe UI, sans-serif">${escapeHtml(
          shape
        )} router</text></g>`
      );
      parts.push(`</g>`);
    });
    parts.push(
      `<text x="${left}" y="${height - 8}" font-size="11" fill="#6b7f90" font-family="Segoe UI, sans-serif">Hover a router line to hide other shapes and show that shape's dashed baseline.</text>`
    );
    parts.push(`</svg>`);
    return parts.join("");
  }

  function renderMetrics(root, days, shapes, coreMetrics, activeShapeId) {
    if (!days.length) {
      root.innerHTML = `<p class="section-note">No rows for the selected hardware and date range.</p>`;
      return;
    }
    const preferred =
      activeShapeId && shapes.some((shape) => shape.replace("/", "_") === activeShapeId)
        ? activeShapeId
        : shapes[0]?.replace("/", "_");
    const buttons = shapes
      .map((shape) => {
        const shapeId = shape.replace("/", "_");
        const active = shapeId === preferred ? " is-active" : "";
        const selected = shapeId === preferred ? "true" : "false";
        return `<button type="button" class="shape-btn${active}" data-shape="${shapeId}" role="tab" aria-selected="${selected}">${escapeHtml(
          shape
        )}</button>`;
      })
      .join("");

    const panels = shapes
      .map((shape) => {
        const shapeId = shape.replace("/", "_");
        const hidden = shapeId === preferred ? "" : " hidden";
        const headers = ["day", "host"]
          .concat(coreMetrics.flatMap((metric) => [metric.label, `${metric.label} baseline`]))
          .map((header) => `<th>${escapeHtml(header)}</th>`)
          .join("");
        const rows = days
          .map((day) => {
            const cells = [`<td>${escapeHtml(day.date)}</td>`, `<td>${escapeHtml(day.host)}</td>`];
            coreMetrics.forEach((metric) => {
              const baseline = day.metrics?.[shape]?.baseline?.[metric.key];
              const router = day.metrics?.[shape]?.router?.[metric.key];
              const delta = deltaArrow(baseline, router);
              const routerHtml = delta
                ? `${escapeHtml(fmt(router))} <span class="${delta.css}">${escapeHtml(delta.text)}</span>`
                : escapeHtml(fmt(router));
              cells.push(`<td class="metric-router">${routerHtml}</td>`);
              cells.push(`<td class="metric-baseline">${escapeHtml(fmt(baseline))}</td>`);
            });
            return `<tr>${cells.join("")}</tr>`;
          })
          .join("");
        return `<div class="shape-panel"${hidden} data-shape="${shapeId}" role="tabpanel"><p class="shape-panel-label">Shape <strong>${escapeHtml(
          shape
        )}</strong> · router values show change vs baseline</p><div class="shape-table-scroll"><table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table></div></div>`;
      })
      .join("");

    root.innerHTML = `<div class="shape-switch" role="tablist" aria-label="Concurrency shape">${buttons}</div>${panels}`;
    const buttonNodes = Array.from(root.querySelectorAll(".shape-btn"));
    const panelNodes = Array.from(root.querySelectorAll(".shape-panel"));
    buttonNodes.forEach((button) => {
      button.addEventListener("click", () => {
        const shape = button.getAttribute("data-shape");
        buttonNodes.forEach((item) => {
          const active = item === button;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-selected", active ? "true" : "false");
        });
        panelNodes.forEach((panel) => {
          panel.hidden = panel.getAttribute("data-shape") !== shape;
        });
      });
    });
  }

  function initDashboard(data) {
    const root = document.getElementById("dashboard-root");
    if (!root) return;
    const hardwareSelect = document.getElementById("hardware-filter");
    const counts = document.getElementById("dashboard-counts");
    const metricsRoot = document.getElementById("shape-metrics-root");
    const detailsRangeSwitch = document.getElementById("details-range-switch");
    const detailsFrom = document.getElementById("details-date-from");
    const detailsTo = document.getElementById("details-date-to");
    const cards = Array.from(root.querySelectorAll(".trend-card"));
    const chartRanges = new Map(cards.map((card) => [card, 7]));
    let detailsRange = "7";
    let syncingDetailsDates = false;

    function selectedHardware() {
      if (hardwareSelect && hardwareSelect.value) return hardwareSelect.value;
      return (data.hardware_options || [])[0] || "Unknown";
    }

    function setDetailsRangeActive(range) {
      detailsRange = range;
      if (!detailsRangeSwitch) return;
      detailsRangeSwitch.querySelectorAll(".range-btn").forEach((button) => {
        button.classList.toggle("is-active", button.getAttribute("data-range") === range);
      });
    }

    function applyDetailsPreset(range, hardwareDays) {
      const bounds = dateBounds(hardwareDays);
      if (!detailsFrom || !detailsTo || !bounds.max) {
        setDetailsRangeActive(range);
        return;
      }
      let start = bounds.min;
      let end = bounds.max;
      if (range === "7") start = rangeStartFor(bounds.max, 7);
      if (range === "30") start = rangeStartFor(bounds.max, 30);
      if (start < bounds.min) start = bounds.min;
      syncingDetailsDates = true;
      detailsFrom.min = bounds.min;
      detailsFrom.max = bounds.max;
      detailsTo.min = bounds.min;
      detailsTo.max = bounds.max;
      detailsFrom.value = start;
      detailsTo.value = end;
      syncingDetailsDates = false;
      setDetailsRangeActive(range);
    }

    function syncDetailsInputs(hardwareDays) {
      const bounds = dateBounds(hardwareDays);
      if (!detailsFrom || !detailsTo || !bounds.max) return;
      detailsFrom.min = bounds.min;
      detailsFrom.max = bounds.max;
      detailsTo.min = bounds.min;
      detailsTo.max = bounds.max;
      if (!detailsFrom.value || !detailsTo.value || detailsRange !== "custom") {
        applyDetailsPreset(detailsRange === "custom" ? "7" : detailsRange, hardwareDays);
      } else {
        if (detailsFrom.value < bounds.min) detailsFrom.value = bounds.min;
        if (detailsFrom.value > bounds.max) detailsFrom.value = bounds.max;
        if (detailsTo.value < bounds.min) detailsTo.value = bounds.min;
        if (detailsTo.value > bounds.max) detailsTo.value = bounds.max;
      }
    }

    function refresh() {
      const hardware = selectedHardware();
      const hardwareDays = filterByHardware(data.days || [], hardware);
      syncDetailsInputs(hardwareDays);
      const startDate = detailsFrom?.value || "";
      const endDate = detailsTo?.value || "";
      const tableDays = filterDaysByInterval(data.days || [], hardware, startDate, endDate);
      if (counts) {
        counts.textContent = `${tableDays.length} RUN GROUPS`;
      }
      cards.forEach((card) => {
        const metric = card.getAttribute("data-metric");
        const range = chartRanges.get(card) || 7;
        const mount = card.querySelector(".trend-chart-mount");
        const days = filterDays(data.days || [], hardware, range);
        if (mount) {
          mount.innerHTML = renderTrendSvg(days, metric, data.shapes || [], data.shape_colors || {});
        }
      });
      if (metricsRoot) {
        const activeShapeId = metricsRoot.querySelector(".shape-btn.is-active")?.getAttribute("data-shape");
        renderMetrics(metricsRoot, tableDays, data.shapes || [], data.core_metrics || [], activeShapeId);
      }
    }

    if (hardwareSelect) {
      if ((data.hardware_options || []).length) {
        hardwareSelect.value = data.hardware_options[0];
      }
      hardwareSelect.addEventListener("change", () => {
        detailsRange = detailsRange === "custom" ? "7" : detailsRange;
        refresh();
      });
    }

    if (detailsRangeSwitch) {
      detailsRangeSwitch.querySelectorAll(".range-btn").forEach((button) => {
        button.addEventListener("click", () => {
          const hardwareDays = filterByHardware(data.days || [], selectedHardware());
          applyDetailsPreset(button.getAttribute("data-range") || "7", hardwareDays);
          refresh();
        });
      });
    }

    function onDetailsDateChange() {
      if (syncingDetailsDates || !detailsFrom || !detailsTo) return;
      if (detailsFrom.value && detailsTo.value && detailsFrom.value > detailsTo.value) {
        detailsTo.value = detailsFrom.value;
      }
      setDetailsRangeActive("custom");
      detailsRangeSwitch?.querySelectorAll(".range-btn").forEach((button) => button.classList.remove("is-active"));
      refresh();
    }

    detailsFrom?.addEventListener("change", onDetailsDateChange);
    detailsTo?.addEventListener("change", onDetailsDateChange);

    cards.forEach((card) => {
      card.querySelectorAll(".range-btn").forEach((button) => {
        button.addEventListener("click", () => {
          card.querySelectorAll(".range-btn").forEach((item) => item.classList.remove("is-active"));
          button.classList.add("is-active");
          chartRanges.set(card, Number(button.getAttribute("data-range") || 7));
          refresh();
        });
      });
    });

    refresh();
  }

  async function boot() {
    try {
      const response = await fetch("dashboard-data.json", { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load dashboard-data.json (${response.status})`);
      const data = await response.json();
      initDashboard(data);
    } catch (error) {
      const root = document.getElementById("dashboard-root");
      if (root) {
        root.insertAdjacentHTML(
          "afterbegin",
          `<p class="section-note">Failed to load interactive dashboard data: ${escapeHtml(error.message)}</p>`
        );
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
