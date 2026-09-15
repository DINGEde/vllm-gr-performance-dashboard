"""Opt-in startup hook for the daily benchmark's lightweight CPU timers.

Python imports ``sitecustomize`` automatically.  Keeping the hook in its own
PYTHONPATH entry lets spawned EngineCore/worker processes inherit the probes
without changing vLLM source files.
"""

from __future__ import annotations

import os


if os.environ.get("VLLM_GR_LIGHTWEIGHT_TIMING") == "1":
    from vllm_gr_lightweight_timing import install

    install()
