from __future__ import annotations

import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
SCRIPT = WORKTREE / "scripts" / "build_vllm_gr_overview.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_vllm_gr_overview", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_runs(builder):
    return builder.discover_runs(WORKTREE / "runs")


@pytest.mark.cpu_test
def test_overview_markdown_renders_all_sections() -> None:
    builder = load_builder()
    runs = load_runs(builder)
    assert runs, "expected at least one discoverable vllm-gr summary"

    md = builder.overview_markdown(runs)

    assert md.startswith("## Performance overview")
    # 六个 section：1.1 / 1.2 / 1.3 / 1.4 / 1.5 / Input-length scaling
    for heading in (
        "1.1 Performance overview",
        "1.2 Beam-search pipeline",
        "1.3 E2E latency localization",
        "1.4 CPU / GPU stage breakdown",
        "1.5 CPU pipeline breakdown",
        "Input-length scaling",
    ):
        assert heading in md, f"missing section heading: {heading}"
    # 四张 SVG + 两张表格
    assert md.count("<svg") == 4, "expected exactly four SVG figures"
    assert md.count("<table") == 2, "expected exactly two tables"
    # 根容器 div 开闭配对
    assert md.count('<div class="vgr-dashboard vgr-overview"') == 1
    assert md.count("</div>") == md.count("<div"), "div tags must pair"
    assert md.count("<section") == md.count("</section>"), "section tags must pair"


@pytest.mark.cpu_test
def test_svgs_are_well_formed_xml() -> None:
    builder = load_builder()
    md = builder.overview_markdown(load_runs(builder))
    svgs = re.findall(r"<svg.*?</svg>", md, flags=re.DOTALL)
    assert len(svgs) == 4
    for svg in svgs:
        ET.fromstring(svg)  # raises on malformed XML


@pytest.mark.cpu_test
def test_tables_list_all_beam_and_input_levels() -> None:
    builder = load_builder()
    md = builder.overview_markdown(load_runs(builder))
    # beam sweep 覆盖 64 / 128 / 256；input sweep 覆盖 512 .. 10240
    for token in ("64", "128", "256", "512", "10240"):
        assert token in md, f"expected level {token} to appear in the tables"


@pytest.mark.cpu_test
def test_no_runs_writes_placeholder(tmp_path: Path) -> None:
    builder = load_builder()
    source = tmp_path / "empty"
    source.mkdir()
    out = tmp_path / "docs"
    builder.write_overview(source, out)
    written = (out / "vllm-gr.md").read_text(encoding="utf-8")
    assert written == "# vllm-gr Performance\n\nNo vllm-gr artifacts found.\n"
