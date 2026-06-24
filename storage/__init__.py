"""Storage layer compatibility exports.

`storage.factory.get_backend()` is the canonical cutover path and follows the
`STORAGE_BACKEND` environment variable. `storage.get_backend(settings)` remains
for older callers that still pass an explicit `storage_backend` setting.
"""

from storage.base import StorageBackend, TenantContextRequired
from storage.local_json import LocalJsonBackend


def get_backend(settings: dict | None = None) -> StorageBackend:
    """Return the configured storage backend.

    If `settings["storage_backend"]` or `settings["local_storage_root"]` is
    provided, preserve the legacy settings-based routing. Otherwise follow
    `STORAGE_BACKEND` through `storage.factory`, which is the post-cutover
    source of truth.
    """
    settings = settings or {}
    if "storage_backend" not in settings and "local_storage_root" not in settings:
        from storage.factory import get_backend as _factory_get
        return _factory_get()

    kind = settings.get("storage_backend", "local").lower()

    if kind == "local":
        return LocalJsonBackend(
            base_dir=settings.get("local_storage_root", None),
        )
    if kind in ("postgres", "supabase"):
        from storage.factory import get_backend as _factory_get
        return _factory_get()
    raise ValueError(f"unknown storage_backend: {kind!r}")


__all__ = ["StorageBackend", "TenantContextRequired", "get_backend", "LocalJsonBackend"]
