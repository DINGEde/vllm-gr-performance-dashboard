"""Build a pinned source snapshot with three native phase timestamps."""
import argparse
import difflib
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


def instrument(source):
    def replace_once(old, new):
        nonlocal source
        if source.count(old) != 1:
            raise ValueError("Native timing insertion point changed: " + old[:100])
        source = source.replace(old, new, 1)
    replace_once(
        '    """Run beam search in batch mode for custom attention backend."""',
        '    """Run beam search in batch mode for custom attention backend."""\n'
        '    if _native_phase_marks is not None:\n'
        '        _native_phase_marks[0] = time.perf_counter_ns()')
    # Locate only the CUSTOM batch helper signature.
    start = source.index("def _custom_beam_search_batch(")
    end = source.index(") -> None:", start)
    source = source[:end] + "    _native_phase_marks=None,\n" + source[end:]
    replace_once("    for token in token_iter:\n",
                 "    for token in token_iter:\n"
                 "        if token == 1 and _native_phase_marks is not None:\n"
                 "            _native_phase_marks[1] = time.perf_counter_ns()\n")
    replace_once("        # Lazy imports for beam search dependencies",
                 "        native_marks = [0, 0, 0] if getattr(self, '_benchmark_native_timing', False) else None\n"
                 "        self._benchmark_phase_marks = None\n"
                 "        # Lazy imports for beam search dependencies")
    replace_once("                    max_decode_steps=max_tokens - pre_calc,\n",
                 "                    max_decode_steps=max_tokens - pre_calc,\n"
                 "                    _native_phase_marks=native_marks,\n")
    replace_once("        return outputs\n", "        if native_marks is not None:\n"
                 "            native_marks[2] = time.perf_counter_ns()\n"
                 "            self._benchmark_phase_marks = native_marks\n"
                 "        return outputs\n")
    compile(source, "native-gr.py", "exec")
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    archive = subprocess.check_output(["git", "-C", args.repo, "archive", args.sha])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        # The archive is a local pinned commit, not an external uploaded tar.
        tar.extractall(args.output, filter="data")
    target = args.output / "vllm_gr/entrypoints/gr.py"
    original = target.read_text(encoding="utf-8")
    modified = instrument(original)
    target.write_text(modified, encoding="utf-8")
    patch = "".join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True),
                                      fromfile="a/vllm_gr/entrypoints/gr.py", tofile="b/vllm_gr/entrypoints/gr.py"))
    (args.output / "native-timing.patch").write_text(patch, encoding="utf-8")
    manifest = {"version": "vllm-gr-native-phases-v1", "git_sha": args.sha,
                "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
                "source_sha256": hashlib.sha256(modified.encode()).hexdigest()}
    (args.output / "native-timing.json").write_text(json.dumps(manifest), encoding="utf-8")


if __name__ == "__main__":
    main()
