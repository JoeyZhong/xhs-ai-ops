"""Subprocess sandbox-lite: timeout + optional Linux rlimit."""
from __future__ import annotations

import os
import platform
import signal
import subprocess
from typing import Callable, Dict, List, Optional

from loguru import logger


class SubprocessTimeoutError(Exception):
    """Command exceeded timeout and was killed."""


def _kill_tree(pid: int) -> None:
    """Kill process and all descendants."""
    if platform.system() == "Windows":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
        )
    else:
        # POSIX: kill process group
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass


def safe_run(
    cmd: List[str],
    *,
    timeout: float = 300,
    mem_mb: int = 1024,
    extra_env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
    line_callback: Optional[Callable[[str], None]] = None,
) -> subprocess.CompletedProcess:
    """Run cmd with timeout. Linux gets rlimit AS; Windows logs warning.

    Args:
        cmd: executable + args list.
        timeout: seconds before kill_tree.
        mem_mb: memory limit (Linux only).
        extra_env: merged into os.environ.
        cwd: working directory.
        line_callback: if given, called for each stdout line (enables streaming).

    Returns:
        CompletedProcess with stdout/stderr merged into stdout.

    Raises:
        SubprocessTimeoutError: on timeout.
    """
    env = {**os.environ, **(extra_env or {})}

    if platform.system() == "Windows" and mem_mb > 0:
        logger.warning("mem limit unavailable (rlimit AS unsupported on Windows)")

    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
        "cwd": cwd,
    }

    if platform.system() != "Windows":
        if mem_mb > 0:
            import resource

            def _setlimit() -> None:
                resource.setrlimit(
                    resource.RLIMIT_AS,
                    (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024),
                )

            kwargs["preexec_fn"] = _setlimit
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    try:
        if line_callback:
            from threading import Thread

            lines: List[str] = []

            def _reader() -> None:
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    lines.append(line)
                    line_callback(line)

            t = Thread(target=_reader, daemon=True)
            t.start()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc.pid)
                t.join(timeout=5)
                raise SubprocessTimeoutError(f"Timed out after {timeout}s: {cmd}")
            t.join()
            stdout = "\n".join(lines)
        else:
            stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise SubprocessTimeoutError(f"Timed out after {timeout}s: {cmd}")

    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, "")
