"""safe_run.py tests — subprocess sandbox.

NOTE: conftest.py mocks xhs_utils to prevent execjs import side effects.
We restore the real module for safe_run (which has no execjs dependency).
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Restore real xhs_utils package (conftest shadows it)
_xhs_utils_mock = sys.modules.pop("xhs_utils", None)
try:
    from xhs_utils.safe_run import safe_run
finally:
    if _xhs_utils_mock is not None:
        sys.modules["xhs_utils"] = _xhs_utils_mock


class TestPosixStartNewSession:
    """start_new_session must be set on POSIX regardless of mem_mb."""

    def _mock_proc(self):
        """Helper: return Popen mock configured for non-streaming path."""
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = []
        proc.communicate.return_value = ("", "")
        proc.wait.return_value = 0
        return proc

    def test_posix_no_mem_limit_has_start_new_session(self):
        """start_new_session=True even when mem_mb=0."""
        with patch("platform.system", return_value="Linux"), \
             patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = self._mock_proc()

            safe_run(["echo", "hi"], mem_mb=0, timeout=5)

            _, kwargs = mock_popen.call_args
            assert kwargs.get("start_new_session") is True, (
                f"Missing start_new_session: {kwargs}"
            )

    def test_posix_with_mem_limit_has_start_new_session(self):
        """start_new_session=True when mem_mb>0 (regression)."""
        resource_mock = MagicMock()
        with patch("platform.system", return_value="Linux"), \
             patch("subprocess.Popen") as mock_popen, \
             patch.dict("sys.modules", {"resource": resource_mock}):
            mock_popen.return_value = self._mock_proc()

            safe_run(["echo", "hi"], mem_mb=1024, timeout=5)

            _, kwargs = mock_popen.call_args
            assert kwargs.get("start_new_session") is True
            assert "preexec_fn" in kwargs  # rlimit setter
