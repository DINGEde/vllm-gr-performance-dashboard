# AgentCache and vllm-gr benchmark artifacts

Compact benchmark artifacts used to build the AgentCache and vllm-gr dashboards.

Full benchmark result directories and raw service logs should stay on the runner hosts.

## View the dashboard locally

The generated Markdown dashboard lives under `docs/` and is meant to be previewed with MkDocs:

```bash
pip install mkdocs==1.6.1
mkdocs serve
```

Then open the URL printed by MkDocs (usually `http://127.0.0.1:8000`).

Useful commands:

```bash
python scripts/build_benchmark_dashboard.py --source runs --output docs
python scripts/build_vllm_gr_dashboard.py --source runs --output docs
mkdocs serve          # live preview
mkdocs build          # write static HTML into site/
mkdocs build --strict # fail on warnings
```

vllm-gr daily summaries are named `vllm-gr-summary.json` and follow
`schemas/vllm-gr-daily-summary.schema.json`. Synthetic and unqualified runs remain visible in the
vllm-gr page and can be excluded with the qualified-only production-trend filter.

## Deploy your own copy

This repository is two halves, and they are deployed separately:

| Half | What it is | Where it is documented |
|---|---|---|
| **Rendering** | The MkDocs site, the builders, and the GitHub Actions job that publishes it | this file |
| **Data production** | The offline benchmark that manufactures the summaries this site renders | [`tools/daily_benchmark/README.md`](tools/daily_benchmark/README.md) |

You can deploy the site against the existing published data first, and add your own producer later.

### 1. Fork or clone

Fork the repository (or clone it and push it to a repository you control). You need push access to
the repository, because the site is published from it.

### 2. Set the site identity

Edit `mkdocs.yml`:

```yaml
site_name: <your dashboard name>
site_description: <one line about what it shows>
```

### 3. Turn on GitHub Pages

In the repository: **Settings → Pages → Build and deployment → Source → GitHub Actions**.

This is not optional and is easy to miss. The workflow publishes with `actions/deploy-pages`, which
fails unless Pages is set to be deployed by Actions rather than from a branch.

### 4. Push to `main`

The workflow runs on pushes to `main` that touch `runs/**`, `schemas/**`, `scripts/**`, `docs/**`,
`mkdocs.yml`, or the workflow itself. Editing only the root `README.md` or `tools/**` does not
trigger a deploy.

It then rebuilds the dashboard data from `runs/`, runs `mkdocs build --strict`, and deploys. The
committed `docs/vllm-gr.md` and `docs/vllm-gr-dashboard-data.json` are snapshots; **the live site
uses the copies CI regenerates from `runs/`.**

### Pin the same tool versions as CI

The workflow pins Python 3.11 and `mkdocs==1.6.1`. Match them locally if you want a local
`mkdocs build --strict` to mean the same thing as a green CI run — a newer MkDocs can turn a new
warning into a failure, and `--strict` promotes warnings to errors.

### Do not rename the pages

`docs/javascripts/vllm-gr-dashboard.js` resolves its data with
`new URL("../vllm-gr-dashboard-data.json", window.location.href)` — a **relative** path that assumes
the page is exactly one level deep. `docs/javascripts/dashboard.js` fetches `dashboard-data.json`
from the same directory. Renaming or moving pages in `mkdocs.yml`'s `nav`, or nesting one deeper,
breaks the fetch with a 404 that shows up only at runtime, not at build time.

### Running on different hardware or a different timeline

`scripts/build_vllm_gr_dashboard.py` and `scripts/benchmark_dashboard_schema.py` contain literals
that describe the machine this dashboard was built for. Change them if yours differs:

| Location | Value | Effect if left alone |
|---|---|---|
| `scripts/build_vllm_gr_dashboard.py:13` | `DISPLAY_START_DATE = "2026-09-01"` | Runs before that date are silently dropped from the site |
| `scripts/build_vllm_gr_dashboard.py:368` | `"gpu": "L20"` | The page labels your hardware L20 |
| `scripts/build_vllm_gr_dashboard.py:380` | The intro sentence naming the GPU | Same, in prose |
| `scripts/benchmark_dashboard_schema.py` | `CANONICAL_L20_HARDWARE`, `NPU_MACHINE_TO_HARDWARE` | The hardware filter buckets runs under the wrong label |
