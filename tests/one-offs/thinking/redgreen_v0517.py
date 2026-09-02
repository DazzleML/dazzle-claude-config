"""Red-green audit for v0.5.17 without touching the tree.

Each case neutralizes ONE mechanism in a fresh subprocess (an exec-patched
function or a monkeypatched name), runs the tests that pin it, and expects
RED. Nothing on disk changes; a stash-based revert on a staged index is
exactly what went wrong the first time, so this audit works in memory.

    python tests/one-offs/thinking/redgreen_v0517.py
"""
from __future__ import annotations

import subprocess
import sys

CASES = [
    # (name, python source that neutralizes ONE thing, pytest -k expression, test file)
    ("reopen_is_safe forced False",
     "import dazzle_claude_config.merge as m; m.reopen_is_safe = lambda n, r=None: False",
     "preloads_tool_reopens", "tests/test_inject_flow.py"),
    ("restore removed from _inject_flow",
     "import dazzle_claude_config.merge as m, inspect\n"
     "src = inspect.getsource(m._inject_flow).replace('merged.write_bytes(kept)', 'pass')\n"
     "ns = {}; exec(compile(src, m.__file__, 'exec'), m.__dict__, ns); m._inject_flow = ns['_inject_flow']",
     "saved_over_is_restored", "tests/test_inject_flow.py"),
    ("already-open refusal removed",
     "import dazzle_claude_config.merge as m, inspect\n"
     "src = inspect.getsource(m._inject_flow).replace('if snap.get(\"open\"):', 'if False:')\n"
     "ns = {}; exec(compile(src, m.__file__, 'exec'), m.__dict__, ns); m._inject_flow = ns['_inject_flow']",
     "already_open_session", "tests/test_inject_flow.py"),
    ("declined consent no longer aborts",
     "import dazzle_claude_config.merge as m, inspect\n"
     "src = inspect.getsource(m._inject_flow).replace('if not ask(item, tool):', 'if False:')\n"
     "ns = {}; exec(compile(src, m.__file__, 'exec'), m.__dict__, ns); m._inject_flow = ns['_inject_flow']",
     "declining_consent", "tests/test_inject_flow.py"),
    ("merge_inject=never ignored",
     "import dazzle_claude_config.merge as m, inspect\n"
     "src = inspect.getsource(m._inject_flow).replace('if inject_mode == \"never\":', 'if False:')\n"
     "ns = {}; exec(compile(src, m.__file__, 'exec'), m.__dict__, ns); m._inject_flow = ns['_inject_flow']",
     "never_mode", "tests/test_inject_flow.py"),
    ("driver availability check removed",
     "import dazzle_claude_config.merge as m, inspect\n"
     "src = inspect.getsource(m._inject_flow).replace('if not usable:', 'if False:')\n"
     "ns = {}; exec(compile(src, m.__file__, 'exec'), m.__dict__, ns); m._inject_flow = ns['_inject_flow']",
     "no_driver_is_a_named_refusal", "tests/test_inject_flow.py"),
    ("--discard routed through injection",
     "import dazzle_claude_config.merge as m, inspect\n"
     "src = inspect.getsource(m.run).replace('and not discard and prof is not None', 'and prof is not None')\n"
     "ns = {}; exec(compile(src, m.__file__, 'exec'), m.__dict__, ns); m.run = ns['run']",
     "discard_is_the_old", "tests/test_inject_flow.py"),
    ("exit code not recorded",
     "import dazzle_claude_config.merge as m, inspect\n"
     "src = inspect.getsource(m.run).replace('res.tool_exit[item.label] = rc', 'pass')\n"
     "ns = {}; exec(compile(src, m.__file__, 'exec'), m.__dict__, ns); m.run = ns['run']",
     "exit_code_is_recorded", "tests/test_inject_flow.py"),
    ("os gate on inject profiles removed",
     "import dazzle_claude_config.merge as m; m._profile_applies = lambda p: True",
     "another_platform", "tests/test_inject_flow.py"),
    ("driver: last JSON line parsing broken",
     "import dazzle_claude_config.inject as i; i._last_json_line = lambda text: None",
     "last_json_object_line or refusal_keeps", "tests/test_inject_driver.py"),
    ("driver: profile -> arguments dropped",
     "import dazzle_claude_config.inject as i; i._profile_args = lambda p: []",
     "bc5_profile_maps", "tests/test_inject_driver.py"),
    ("driver: ok assumed when absent",
     "import dazzle_claude_config.inject as i, inspect\n"
     "src = inspect.getsource(i.run_driver).replace('result.setdefault(\"ok\", False)', 'result.setdefault(\"ok\", True)')\n"
     "ns = {}; exec(compile(src, i.__file__, 'exec'), i.__dict__, ns); i.run_driver = ns['run_driver']",
     "ok_is_never_assumed", "tests/test_inject_driver.py"),
    ("user overlay no longer wins",
     "import dazzle_claude_config.merge as m\n"
     "m.effective_registry = lambda user_claude=None: (dict(m.TOOL_REGISTRY), [])",
     "user_entry_wins or add_a_tool", "tests/test_tool_capability.py"),
    ("executable classification removed",
     "import dazzle_claude_config.merge as m; m._exe_key = lambda cmd: None",
     "classified_by_its_executable", "tests/test_tool_capability.py"),
]


def main() -> int:
    red = green = 0
    for name, patch, kexpr, tfile in CASES:
        code = (patch + "\nimport pytest, sys\n"
                f"sys.exit(pytest.main([{tfile!r}, '-q', '-p', 'no:cacheprovider', '--tb=no', '-k', {kexpr!r}]))")
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        last = (p.stdout.strip().splitlines() or [""])[-1]
        is_red = p.returncode != 0 and "failed" in last
        red += is_red
        green += (not is_red)
        print(f"{'RED  ' if is_red else 'GREEN'}  {name:42s} {last}")
    print(f"\n{red} anchors (went red when neutralized), {green} did not")
    return 0 if green == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
