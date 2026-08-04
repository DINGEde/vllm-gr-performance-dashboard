# AgentCache benchmark artifacts

Compact benchmark artifacts used to build the benchmark dashboard.

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
mkdocs serve          # live preview
mkdocs build          # write static HTML into site/
mkdocs build --strict # fail on warnings
```
