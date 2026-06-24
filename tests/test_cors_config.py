from __future__ import annotations


def test_cors_defaults_include_loopback_frontend_hosts():
    from server.main import CORS_ALLOWED_ORIGINS

    assert "http://localhost:3000" in CORS_ALLOWED_ORIGINS
    assert "http://127.0.0.1:3000" in CORS_ALLOWED_ORIGINS


def test_cors_allows_extra_origins_from_env(monkeypatch):
    from server.main import _cors_allowed_origins

    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "http://100.64.1.2:3000, http://spider-xhs.tailnet-name.ts.net:3000/",
    )

    origins = _cors_allowed_origins()

    assert "http://100.64.1.2:3000" in origins
    assert "http://spider-xhs.tailnet-name.ts.net:3000" in origins
