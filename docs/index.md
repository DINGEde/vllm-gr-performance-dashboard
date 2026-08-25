Compact daily trends from `dashboard-summary.json` artifacts — one logical run per experiment ID.

<div class="dashboard-root" id="dashboard-root">
<div class="dashboard-controls">
<div class="control-block">
<span class="control-kicker">Filter</span>
<label class="hardware-filter-label" for="hardware-filter">Hardware</label>
<select id="hardware-filter" class="hardware-filter" aria-label="Hardware filter">
<option value="L20" selected>L20</option>
<option value="A3">A3</option>
</select>
</div>
<p class="dashboard-counts" id="dashboard-counts"></p>
</div>
<section class="trend-section">
<div class="section-head"><h2>Trend charts</h2>
<p class="section-note">Default window is the past 7 calendar days.</p></div>
<div class="trend-grid">
<section class="trend-card" data-metric="completed_tasks">
<div class="trend-card-head"><h3>Completed tasks</h3>
<div class="range-switch" role="group" aria-label="Trend window">
<button type="button" class="range-btn is-active" data-range="7">7D</button>
<button type="button" class="range-btn" data-range="30">30D</button>
</div></div>
<div class="trend-chart-mount" aria-live="polite"></div>
<p class="metric-trend-hint">Hover a router line to isolate that shape and show its dashed baseline.</p>
</section>
<section class="trend-card" data-metric="failed_tasks">
<div class="trend-card-head"><h3>Failed tasks</h3>
<div class="range-switch" role="group" aria-label="Trend window">
<button type="button" class="range-btn is-active" data-range="7">7D</button>
<button type="button" class="range-btn" data-range="30">30D</button>
</div></div>
<div class="trend-chart-mount" aria-live="polite"></div>
<p class="metric-trend-hint">Hover a router line to isolate that shape and show its dashed baseline.</p>
</section>
<section class="trend-card" data-metric="task_duration_seconds.mean">
<div class="trend-card-head"><h3>Task duration mean</h3>
<div class="range-switch" role="group" aria-label="Trend window">
<button type="button" class="range-btn is-active" data-range="7">7D</button>
<button type="button" class="range-btn" data-range="30">30D</button>
</div></div>
<div class="trend-chart-mount" aria-live="polite"></div>
<p class="metric-trend-hint">Hover a router line to isolate that shape and show its dashed baseline.</p>
</section>
<section class="trend-card" data-metric="profiling.vllm.latency_breakdown_seconds.queue_time.mean">
<div class="trend-card-head"><h3>Queue mean</h3>
<div class="range-switch" role="group" aria-label="Trend window">
<button type="button" class="range-btn is-active" data-range="7">7D</button>
<button type="button" class="range-btn" data-range="30">30D</button>
</div></div>
<div class="trend-chart-mount" aria-live="polite"></div>
<p class="metric-trend-hint">Hover a router line to isolate that shape and show its dashed baseline.</p>
</section>
<section class="trend-card" data-metric="ttft_seconds.mean">
<div class="trend-card-head"><h3>TTFT mean</h3>
<div class="range-switch" role="group" aria-label="Trend window">
<button type="button" class="range-btn is-active" data-range="7">7D</button>
<button type="button" class="range-btn" data-range="30">30D</button>
</div></div>
<div class="trend-chart-mount" aria-live="polite"></div>
<p class="metric-trend-hint">Hover a router line to isolate that shape and show its dashed baseline.</p>
</section>
<section class="trend-card" data-metric="latency_seconds.mean">
<div class="trend-card-head"><h3>Latency mean</h3>
<div class="range-switch" role="group" aria-label="Trend window">
<button type="button" class="range-btn is-active" data-range="7">7D</button>
<button type="button" class="range-btn" data-range="30">30D</button>
</div></div>
<div class="trend-chart-mount" aria-live="polite"></div>
<p class="metric-trend-hint">Hover a router line to isolate that shape and show its dashed baseline.</p>
</section>
</div>
</section>
<section class="metrics-section">
<div class="section-head"><h2>Details</h2>
<div class="details-toolbar">
<div class="range-switch" id="details-range-switch" role="group" aria-label="Details window">
<button type="button" class="range-btn is-active" data-range="7">7D</button>
<button type="button" class="range-btn" data-range="30">30D</button>
<button type="button" class="range-btn" data-range="all">All</button>
</div>
<div class="details-date-range">
<label class="details-date-label" for="details-date-from">From
<input type="date" id="details-date-from" class="details-date-input" aria-label="Details start date"></label>
<label class="details-date-label" for="details-date-to">To
<input type="date" id="details-date-to" class="details-date-input" aria-label="Details end date"></label>
</div>
</div></div>
<p class="section-note">Pick a concurrency shape to compare router vs baseline. Router cells show ↑/↓ vs baseline.</p>
<div class="shape-metrics" id="shape-metrics-root"></div>
</section>
</div>
