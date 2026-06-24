from __future__ import annotations


def test_storage_get_backend_defaults_to_factory(monkeypatch):
    import storage
    import storage.factory

    sentinel = object()
    monkeypatch.setattr(storage.factory, "get_backend", lambda: sentinel)

    assert storage.get_backend() is sentinel
    assert storage.get_backend({}) is sentinel


def test_storage_get_backend_preserves_explicit_local_setting(tmp_path):
    import storage
    from storage.local_json import LocalJsonBackend

    backend = storage.get_backend({
        "storage_backend": "local",
        "local_storage_root": str(tmp_path),
    })

    assert isinstance(backend, LocalJsonBackend)
