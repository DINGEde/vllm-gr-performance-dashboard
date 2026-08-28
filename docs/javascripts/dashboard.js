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

  function dayAxisLabel(day) {
    return String(day.date || "").slice(5);
  }

  function dayFullLabel(day) {
    const host = day.host ? ` · ${day.host}` : "";
    return `${day.date}${host}`;
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

  function yDomain(values) {
    let ymin = Math.min(...values);
    let ymax = Math.max(...values);
    if (ymin === ymax) {
      const pad = Math.abs(ymin) * 0.1 || 1;
      return { ymin: ymin - pad, ymax: ymax + pad };
    }
    const pad = (ymax - ymin) * 0.08;
    ymin -= pad;
    ymax += pad;
    if (ymin > 0 && ymin / (ymax - ymin) < 0.15) ymin = 0;
    if (ymax < 0 && -ymax / (ymax - ymin) < 0.15) ymax = 0;
    return { ymin, ymax };
  }

  function chartLayout() {
    const width = 1100;
    const height = 400;
    const left = 112;
    const right = 20;
    const top = 24;
    const bottom = 76;
    return {
      width,
      height,
      left,
      right,
      top,
      bottom,
      plotW: width - left - right,
      plotH: height - top - bottom,
    };
  }

  function updateSharedLegendFocus(focusShape) {
    const focusId = focusShape ? focusShape.replace("/", "_") : null;
    document.querySelectorAll("#trend-shared-legend .legend-item").forEach((item) => {
      const shapeId = item.getAttribute("data-shape");
      if (item.classList.contains("legend-baseline")) {
        item.classList.toggle("is-focus", Boolean(focusId));
        item.classList.toggle("is-dimmed", !focusId);
        return;
      }
      item.classList.toggle("is-focus", Boolean(focusId) && shapeId === focusId);
      item.classList.toggle("is-dimmed", Boolean(focusId) && shapeId !== focusId);
    });
  }

  function renderSharedLegend(root, shapes, colors) {
    const section = root.querySelector(".trend-section");
    if (!section) return;
    let legend = section.querySelector("#trend-shared-legend");
    if (!legend) {
      const head = section.querySelector(".section-head");
      legend = document.createElement("div");
      legend.id = "trend-shared-legend";
      legend.className = "trend-shared-legend";
      legend.setAttribute("role", "list");
      legend.setAttribute("aria-label", "Concurrency shape legend");
      head?.insertAdjacentElement("afterend", legend);
      if (!section.querySelector(".trend-shared-hint")) {
        const hint = document.createElement("p");
        hint.className = "trend-shared-hint section-note";
        hint.textContent =
          "Hover a router line to isolate that shape, show its dashed baseline, and rescale the Y axis. Data points show exact values.";
        legend.insertAdjacentElement("afterend", hint);
      }
    }
    const items = shapes
      .map((shape) => {
        const shapeId = shape.replace("/", "_");
        const color = colors[shape] || SHAPE_FALLBACK_COLORS[shape] || "#334155";
        return `<span class="legend-item" data-shape="${shapeId}" role="listitem"><span class="legend-swatch" style="background:${color}"></span>${escapeHtml(
          shape
        )} router</span>`;
      })
      .join("");
    legend.innerHTML = `${items}<span class="legend-item legend-baseline" role="listitem"><span class="legend-swatch legend-swatch-baseline"></span>baseline (dashed)</span>`;
  }

  function collectScaleValues(days, metric, shapes, focusShape) {
    const values = [];
    const selected = focusShape ? [focusShape] : shapes;
    selected.forEach((shape) => {
      days.forEach((day) => {
        const router = day.metrics?.[shape]?.router?.[metric];
        if (router !== null && router !== undefined) values.push(router);
        if (focusShape) {
          const baseline = day.metrics?.[shape]?.baseline?.[metric];
          if (baseline !== null && baseline !== undefined) values.push(baseline);
        }
      });
    });
    return values;
  }

  function pointsString(days, layout, ymin, ymax, getter) {
    const { left, top, plotW, plotH } = layout;
    const xAt = (index) => left + (plotW * index) / Math.max(1, days.length - 1);
    const yAt = (value) => top + plotH - ((value - ymin) / (ymax - ymin)) * plotH;
    return days
      .map((day, index) => {
        const value = getter(day);
        return value === null || value === undefined ? null : `${xAt(index).toFixed(1)},${yAt(value).toFixed(1)}`;
      })
      .filter(Boolean)
      .join(" ");
  }

  function pointerInPlot(svg, event) {
    const layout = svg._trendState?.layout;
    if (!layout) return false;
    const ctm = svg.getScreenCTM();
    if (!ctm) return false;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const local = point.matrixTransform(ctm.inverse());
    return (
      local.x >= layout.left &&
      local.x <= layout.left + layout.plotW &&
      local.y >= layout.top &&
      local.y <= layout.top + layout.plotH
    );
  }

  function setTrendFocus(svg, focusShape) {
    const state = svg._trendState;
    if (!state) return;
    if (state.focusShape === focusShape) return;
    state.focusShape = focusShape;
    svg.classList.toggle("has-focus", Boolean(focusShape));
    const focusId = focusShape ? focusShape.replace("/", "_") : null;
    svg.querySelectorAll(".series-group").forEach((group) => {
      group.classList.toggle("is-focus", Boolean(focusId) && group.getAttribute("data-shape") === focusId);
    });
    updateSharedLegendFocus(focusShape);

    const { days, metric, shapes, layout } = state;
    const values = collectScaleValues(days, metric, shapes, focusShape);
    if (!values.length) return;
    const { ymin, ymax } = yDomain(values);
    const { left, top, plotW, plotH } = layout;
    const xAt = (index) => left + (plotW * index) / Math.max(1, days.length - 1);
    const yAt = (value) => top + plotH - ((value - ymin) / (ymax - ymin)) * plotH;
    const ticks = [ymin, (ymin + ymax) / 2, ymax];
    svg.querySelectorAll(".y-grid").forEach((line, index) => {
      const y = yAt(ticks[index]).toFixed(1);
      line.setAttribute("y1", y);
      line.setAttribute("y2", y);
    });
    svg.querySelectorAll(".y-label").forEach((label, index) => {
      label.setAttribute("y", (yAt(ticks[index]) + 4).toFixed(1));
      label.textContent = fmt(ticks[index]);
    });
    shapes.forEach((shape) => {
      const shapeId = shape.replace("/", "_");
      const group = svg.querySelector(`.series-group[data-shape="${shapeId}"]`);
      if (!group) return;
      ["baseline", "router"].forEach((side) => {
        const getter = (day) => day.metrics?.[shape]?.[side]?.[metric];
        const points = pointsString(days, layout, ymin, ymax, getter);
        group.querySelectorAll(`polyline[data-side="${side}"]`).forEach((node) => {
          // Keep hit-target geometry on the default scale to avoid hover thrashing.
          if (node.classList.contains("router-hit")) return;
          node.setAttribute("points", points);
        });
        group.querySelectorAll(`circle.data-point[data-side="${side}"]`).forEach((node) => {
          const index = Number(node.getAttribute("data-index"));
          const value = getter(days[index]);
          if (value === null || value === undefined) return;
          node.setAttribute("cx", xAt(index).toFixed(1));
          node.setAttribute("cy", yAt(value).toFixed(1));
        });
      });
    });
  }

  function bindTrendTooltips(svg, mount, metricLabel) {
    let tooltip = mount.querySelector(".trend-point-tooltip");
    if (!tooltip) {
      tooltip = document.createElement("div");
      tooltip.className = "trend-point-tooltip";
      tooltip.hidden = true;
      mount.appendChild(tooltip);
    }
    const hide = () => {
      tooltip.hidden = true;
    };
    const show = (event) => {
      const state = svg._trendState;
      const node = event.currentTarget;
      if (!state || !node) return;
      const shapeId = node.getAttribute("data-shape");
      const shape = state.shapes.find((item) => item.replace("/", "_") === shapeId) || shapeId.replace("_", "/");
      const side = node.getAttribute("data-side") || "router";
      const index = Number(node.getAttribute("data-index"));
      const day = state.days[index];
      if (!day) return;
      const value = day.metrics?.[shape]?.[side]?.[state.metric];
      const sideLabel = side === "baseline" ? "baseline" : "router";
      const hostLabel = day.host ? ` · ${day.host}` : "";
      tooltip.innerHTML = [
        `<strong>${escapeHtml(day.date)}${escapeHtml(hostLabel)}</strong>`,
        `<span>${escapeHtml(shape)} · ${escapeHtml(sideLabel)}</span>`,
        `<span>${escapeHtml(metricLabel)}: ${escapeHtml(fmt(value))}</span>`,
      ].join("");
      tooltip.hidden = false;
      const mountRect = mount.getBoundingClientRect();
      const tooltipRect = tooltip.getBoundingClientRect();
      let left = event.clientX - mountRect.left + 12;
      let top = event.clientY - mountRect.top - tooltipRect.height - 12;
      if (left + tooltipRect.width > mountRect.width - 8) {
        left = event.clientX - mountRect.left - tooltipRect.width - 12;
      }
      if (top < 8) top = event.clientY - mountRect.top + 12;
      tooltip.style.left = `${Math.max(8, left)}px`;
      tooltip.style.top = `${Math.max(8, top)}px`;
    };
    svg.querySelectorAll(".data-point").forEach((node) => {
      node.addEventListener("mouseenter", show);
      node.addEventListener("mousemove", show);
      node.addEventListener("mouseleave", hide);
    });
    mount.addEventListener("mouseleave", hide);
  }

  function bindTrendScale(svg) {
    svg._trendState.focusShape = null;
    svg.querySelectorAll(".series-group").forEach((group) => {
      const focus = (event) => {
        if (!pointerInPlot(svg, event)) {
          setTrendFocus(svg, null);
          return;
        }
        const shapeId = group.getAttribute("data-shape");
        const shape = (svg._trendState?.shapes || []).find((item) => item.replace("/", "_") === shapeId);
        setTrendFocus(svg, shape || null);
      };
      group.addEventListener("pointerenter", focus);
      group.addEventListener("pointermove", focus);
    });
    svg.addEventListener("pointermove", (event) => {
      if (!pointerInPlot(svg, event)) setTrendFocus(svg, null);
    });
    svg.addEventListener("pointerleave", () => setTrendFocus(svg, null));
  }

  function mountTrendChart(mount, days, metric, shapes, colors, metricLabel) {
    mount.innerHTML = renderTrendSvg(days, metric, shapes, colors);
    const svg = mount.querySelector("svg.metric-trend-chart");
    if (!svg) return;
    svg._trendState = {
      days,
      metric,
      shapes,
      layout: chartLayout(),
    };
    bindTrendScale(svg);
    bindTrendTooltips(svg, mount, metricLabel || metric);
  }

  function renderTrendSvg(days, metric, shapes, colors) {
    const layout = chartLayout();
    const { width, height, left, top, plotW, plotH } = layout;
    const axisLabels = days.map(dayAxisLabel);
    const fullLabels = days.map(dayFullLabel);
    const values = collectScaleValues(days, metric, shapes, null);
    if (!values.length) {
      return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#ffffff"/><text x="20" y="30" fill="#5d7082">No data</text></svg>`;
    }
    const { ymin, ymax } = yDomain(values);
    const xAt = (index) => left + (plotW * index) / Math.max(1, axisLabels.length - 1);
    const yAt = (value) => top + plotH - ((value - ymin) / (ymax - ymin)) * plotH;
    const pointsFor = (getter) => pointsString(days, layout, ymin, ymax, getter);

    const clipId = `plot-clip-${metric.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
    const parts = [
      `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" class="metric-trend-chart" role="img">`,
      `<defs><clipPath id="${clipId}"><rect x="${left}" y="${top}" width="${plotW}" height="${plotH}"/></clipPath></defs>`,
      `<rect width="100%" height="100%" fill="#ffffff"/>`,
      `<style>
.metric-trend-chart .router-layer,
.metric-trend-chart .baseline-layer { transition: opacity 0.12s ease; }
.metric-trend-chart .baseline-layer { opacity: 0; pointer-events: none; }
.metric-trend-chart .router-hit { fill: none; stroke: transparent; stroke-width: 18; cursor: pointer; pointer-events: stroke; }
.metric-trend-chart .data-point { pointer-events: all; cursor: crosshair; }
.metric-trend-chart .baseline-layer .data-point { pointer-events: none; }
.metric-trend-chart.has-focus .series-group.is-focus .baseline-layer .data-point { pointer-events: all; }
.metric-trend-chart.has-focus .series-group:not(.is-focus) .router-layer,
.metric-trend-chart.has-focus .series-group:not(.is-focus) .data-point { opacity: 0; pointer-events: none; }
.metric-trend-chart .series-group.is-focus .baseline-layer { opacity: 1; }
</style>`,
    ];
    [ymin, (ymin + ymax) / 2, ymax].forEach((yv) => {
      const y = yAt(yv);
      parts.push(
        `<line class="y-grid" x1="${left}" y1="${y}" x2="${left + plotW}" y2="${y}" stroke="#e6eef3"/>`
      );
      parts.push(
        `<text class="y-label" x="${left - 8}" y="${y + 4}" text-anchor="end" font-size="14" fill="#6b7f90" font-family="Segoe UI, sans-serif">${fmt(
          yv
        )}</text>`
      );
    });
    parts.push(`<line x1="${left}" y1="${top + plotH}" x2="${left + plotW}" y2="${top + plotH}" stroke="#9eb2c2"/>`);
    parts.push(`<line x1="${left}" y1="${top}" x2="${left}" y2="${top + plotH}" stroke="#9eb2c2"/>`);
    const labelY = top + plotH + 28;
    sampledIndices(axisLabels.length, plotW, 72).forEach((index) => {
      const x = xAt(index);
      parts.push(
        `<text class="x-label" x="${x}" y="${labelY}" text-anchor="end" font-size="14" fill="#6b7f90" font-family="Segoe UI, sans-serif" transform="rotate(-35 ${x} ${labelY})">${escapeHtml(
          axisLabels[index]
        )}<title>${escapeHtml(fullLabels[index])}</title></text>`
      );
    });

    parts.push(`<g class="plot-layer" clip-path="url(#${clipId})">`);
    shapes.forEach((shape) => {
      const color = colors[shape] || SHAPE_FALLBACK_COLORS[shape] || "#334155";
      const shapeId = shape.replace("/", "_");
      const routerPoints = pointsFor((day) => day.metrics?.[shape]?.router?.[metric]);
      const baselinePoints = pointsFor((day) => day.metrics?.[shape]?.baseline?.[metric]);
      parts.push(`<g class="series-group" data-shape="${shapeId}">`);
      parts.push(`<g class="baseline-layer">`);
      if (baselinePoints) {
        parts.push(
          `<polyline data-side="baseline" fill="none" stroke="#334155" stroke-width="2.2" stroke-dasharray="6 5" stroke-linecap="round" stroke-linejoin="round" points="${baselinePoints}"/>`
        );
        days.forEach((day, index) => {
          const value = day.metrics?.[shape]?.baseline?.[metric];
          if (value === null || value === undefined) return;
          parts.push(
            `<circle class="data-point" data-shape="${shapeId}" data-side="baseline" data-index="${index}" cx="${xAt(index).toFixed(1)}" cy="${yAt(value).toFixed(
              1
            )}" r="5.5" fill="#ffffff" stroke="#334155" stroke-width="2"/>`
          );
        });
      }
      parts.push(`</g><g class="router-layer">`);
      if (routerPoints) {
        parts.push(`<polyline class="router-hit" data-side="router" points="${routerPoints}"/>`);
        parts.push(
          `<polyline data-side="router" fill="none" stroke="${color}" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" points="${routerPoints}"/>`
        );
        days.forEach((day, index) => {
          const value = day.metrics?.[shape]?.router?.[metric];
          if (value === null || value === undefined) return;
          parts.push(
            `<circle class="data-point" data-shape="${shapeId}" data-side="router" data-index="${index}" cx="${xAt(index).toFixed(1)}" cy="${yAt(value).toFixed(
              1
            )}" r="6" fill="#ecfeff" stroke="${color}" stroke-width="2"/>`
          );
        });
      }
      parts.push(`</g></g>`);
    });
    parts.push(`</g>`);
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
    renderSharedLegend(root, data.shapes || [], data.shape_colors || {});
    const chartRanges = new Map(cards.map((card) => [card, 7]));
    let detailsRange = "7";
    let syncingDetailsDates = false;

    function selectedHardware() {
      const options = data.hardware_options || [];
      const fallback = options[0] || "Unknown";
      if (!hardwareSelect) return fallback;
      const current = hardwareSelect.value;
      if (current && filterByHardware(data.days || [], current).length) return current;
      if (options.length && hardwareSelect.value !== fallback) {
        hardwareSelect.value = fallback;
      }
      return fallback;
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
          const metricLabel = card.querySelector(".trend-card-head h3")?.textContent?.trim() || metric;
          mountTrendChart(mount, days, metric, data.shapes || [], data.shape_colors || {}, metricLabel);
        }
      });
      if (metricsRoot) {
        const activeShapeId = metricsRoot.querySelector(".shape-btn.is-active")?.getAttribute("data-shape");
        renderMetrics(metricsRoot, tableDays, data.shapes || [], data.core_metrics || [], activeShapeId);
      }
    }

    if (hardwareSelect) {
      const options = data.hardware_options || [];
      if (options.length) {
        hardwareSelect.innerHTML = options
          .map(
            (hardware, index) =>
              `<option value="${escapeHtml(hardware)}"${index === 0 ? " selected" : ""}>${escapeHtml(hardware)}</option>`
          )
          .join("");
        hardwareSelect.value = options[0];
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
