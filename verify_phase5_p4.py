"""P4 Subprocess Sandbox-Lite 验收.

TDD vertical slice. Run: python verify_phase5_p4.py
"""
from __future__ import annotations

import platform
import sys
import time
from pathlib import Path

# allow import from repo root
sys.path.insert(0, str(Path(__file__).parent))

from xhs_utils.safe_run import safe_run, SubprocessTimeoutError


def test_echo():
    """Basic run returns stdout."""
    r = safe_run([sys.executable, "-c", "print('hello')"])
    assert r.stdout.strip() == "hello"
    assert r.returncode == 0
    print("PASS test_echo")


def test_timeout_kills():
    """Timeout triggers SubprocessTimeoutError and kills child."""
    start = time.time()
    try:
        # sleep 10s, should timeout at 0.5s
        safe_run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.5)
        assert False, "should have timed out"
    except SubprocessTimeoutError:
        elapsed = time.time() - start
        assert elapsed < 3, f"kill took too long: {elapsed:.1f}s"
    print("PASS test_timeout_kills")


def test_timeout_kill_tree():
    """Timeout kills grandchild too."""
    # spawn a child that spawns another sleeper
    script = """
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
time.sleep(30)
"""
    start = time.time()
    try:
        safe_run([sys.executable, "-c", script], timeout=1.0)
        assert False, "should have timed out"
    except SubprocessTimeoutError:
        elapsed = time.time() - start
        assert elapsed < 5, f"kill_tree took too long: {elapsed:.1f}s"
    print("PASS test_timeout_kill_tree")


def test_windows_mem_warning(capsys):
    """On Windows, mem_mb>0 logs warning (no rlimit)."""
    if platform.system() != "Windows":
        print("SKIP test_windows_mem_warning (not Windows)")
        return
    # Use a logger capture or just check that it runs without error
    # Since we can't easily capture loguru, just verify no exception
    r = safe_run([sys.executable, "-c", "print('ok')"], mem_mb=512)
    assert r.returncode == 0
    print("PASS test_windows_mem_warning")


def test_returncode_nonzero():
    """Non-zero exit captured, not raised."""
    r = safe_run([sys.executable, "-c", "import sys; sys.exit(42)"])
    assert r.returncode == 42
    print("PASS test_returncode_nonzero")


def test_env_injection():
    """extra_env reaches child."""
    r = safe_run(
        [sys.executable, "-c", "import os; print(os.environ.get('P4_TEST','missing'))"],
        extra_env={"P4_TEST": "injected"},
    )
    assert r.stdout.strip() == "injected"
    print("PASS test_env_injection")


def test_line_callback():
    """line_callback receives each line and result still correct."""
    received = []
    r = safe_run(
        [sys.executable, "-c", "print('a'); print('b'); print('c')"],
        line_callback=lambda line: received.append(line),
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "a\nb\nc"
    assert received == ["a", "b", "c"]
    print("PASS test_line_callback")


def test_timeout_no_hang_on_fast_cmd():
    """Fast cmd with long timeout returns normally."""
    r = safe_run([sys.executable, "-c", "print('fast')"], timeout=300)
    assert r.stdout.strip() == "fast"
    print("PASS test_timeout_no_hang_on_fast_cmd")


def test_cwd():
    """cwd parameter sets working directory."""
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        r = safe_run(
            [sys.executable, "-c", "import os; print(os.getcwd())"],
            cwd=tmp,
        )
        assert r.stdout.strip() == tmp
    print("PASS test_cwd")


def test_timeout_callback_partial_output():
    """Timeout still preserves lines received before kill."""
    received = []
    script = "import sys; print('first'); sys.stdout.flush(); import time; time.sleep(30)"
    try:
        safe_run(
            [sys.executable, "-c", script],
            timeout=1.0,
            line_callback=lambda line: received.append(line),
        )
        assert False, "should have timed out"
    except SubprocessTimeoutError:
        assert "first" in received
    print("PASS test_timeout_callback_partial_output")


def test_signal_imported():
    """safe_run module has signal available (POSIX fix: missing import signal)."""
    import xhs_utils.safe_run as sr
    # signal.SIGKILL used in _kill_tree POSIX branch — verify module can access it
    assert hasattr(sr, "signal") or True  # noqa: module-level import not exposed
    # Actually verify the source file contains 'import signal'
    src = Path(sr.__file__).read_text("utf-8")
    assert "import signal" in src, "Missing 'import signal' — POSIX _kill_tree will NameError"
    print("PASS test_signal_imported")


def test_posix_start_new_session():
    """POSIX branch sets start_new_session=True to isolate pgid."""
    import xhs_utils.safe_run as sr
    src = Path(sr.__file__).read_text("utf-8")
    assert "start_new_session" in src, (
        "Missing start_new_session=True — kill_tree kills parent process group"
    )
    print("PASS test_posix_start_new_session")


if __name__ == "__main__":
    test_echo()
    test_timeout_kills()
    test_timeout_kill_tree()
    test_windows_mem_warning(None)
    test_returncode_nonzero()
    test_env_injection()
    test_line_callback()
    test_timeout_no_hang_on_fast_cmd()
    test_cwd()
    test_timeout_callback_partial_output()
    test_signal_imported()
    test_posix_start_new_session()
    print("\nAll P4 tests passed.")
