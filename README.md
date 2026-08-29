# AgentCache and vllm-gr benchmark artifacts

Compact benchmark artifacts used to build the AgentCache and vllm-gr dashboards.

Full benchmark result directories and raw service logs should stay on the runner hosts.

## View the dashboard locally

The generated Markdown dashboard lives under `docs/` and is meant to be previewed with MkDocs:

```bash
pip install mkdocs
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
