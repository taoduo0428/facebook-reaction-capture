"""Bridge to the existing, battle-tested implementation.

Everything in this module is about *not changing behaviour*: the same security
module, the same static-asset resolution, the same error classes and the same
``JobManager``.  If the legacy module ever needs to be replaced, this is the
only file the HTTP layer should have to care about.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from facebook_backend.domain.exceptions import (
    ApiError,
    BackendError,
    NotFoundError,
    SchedulerStartError,
    ServiceUnavailableError,
)

_LEGACY: Any | None = None


def legacy_module() -> Any:
    """Import ``facebook_capture_api`` lazily and cache the module object."""
    global _LEGACY
    if _LEGACY is None:
        import facebook_capture_api as module

        _LEGACY = module
    return _LEGACY


def set_legacy_module(module: Any | None) -> None:
    """Test hook: inject a stub legacy module (``None`` restores the real one)."""
    global _LEGACY
    _LEGACY = module


def is_legacy_api_error(exc: BaseException) -> bool:
    """Type identity is shared, so this no longer has to import the old module."""
    return isinstance(exc, ApiError)


def is_scheduler_start_error(exc: BaseException) -> bool:
    return isinstance(exc, SchedulerStartError)


@contextmanager
def map_legacy_errors(
    *,
    not_found: str | None = None,
    api_status: int | None = None,
    api_message: str | None = None,
    scheduler: str | None = None,
    os_error: str | None = None,
    timeout: str | None = None,
    decode_status: int | None = None,
) -> Iterator[None]:
    """Translate legacy exceptions into :class:`BackendError` subclasses.

    Only the explicitly mapped cases are translated; anything else is re-raised
    so an unexpected failure can never be silently reported as success.
    """
    try:
        yield
    except KeyError as exc:
        if not_found is None:
            raise
        raise NotFoundError(not_found) from exc
    except TimeoutError as exc:
        if timeout is None:
            raise
        raise BackendError(timeout, status_code=408) from exc
    except OSError as exc:
        if os_error is None:
            raise
        raise ServiceUnavailableError(os_error) from exc
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        if decode_status is None:
            raise
        raise BackendError(str(exc), status_code=decode_status) from exc
    except Exception as exc:  # noqa: BLE001 - re-raised when unmapped
        if is_scheduler_start_error(exc):
            if scheduler is None:
                raise
            raise ServiceUnavailableError(scheduler) from exc
        if is_legacy_api_error(exc):
            if api_status is None:
                raise
            raise BackendError(api_message or str(exc), status_code=api_status) from exc
        raise


def apply_local_environment(base_dir: Path) -> bool:
    """Mirror ``facebook_capture_api.main()``'s optional local configuration."""
    if not (base_dir / "facebook.local.json").is_file():
        return False
    from facebook_local import configuration, environment

    environment(configuration(base_dir), base_dir)
    return True


def build_manager(
    *,
    serial: str,
    base_dir: Path,
    load_local_config: bool = True,
    max_runtime_seconds: int | None = None,
    store: Any | None = None,
) -> Any:
    """Create the single process-wide ``JobManager`` (blocking; call in a thread)."""
    if load_local_config:
        apply_local_environment(base_dir)
    api = legacy_module()
    kwargs: dict[str, Any] = {"worker_config_path": base_dir / "facebook_workers.json"}
    if max_runtime_seconds is not None:
        kwargs["max_runtime_seconds"] = max_runtime_seconds
    if store is not None:
        kwargs["store"] = store
    return api.JobManager(serial, **kwargs)


def resolve_static_asset(request_path: str) -> Path | None:
    return legacy_module().resolve_static_asset(request_path)


def resolve_avatar_url(user_id: str) -> str:
    return legacy_module().resolve_avatar_url(user_id)


def doctor() -> dict[str, Any]:
    from facebook_local import doctor as local_doctor

    return local_doctor()


def static_assets() -> dict[str, str]:
    return dict(legacy_module().STATIC_ASSETS)


def web_dir() -> Path:
    return Path(legacy_module().WEB_DIR)


def inline_artifact_suffixes() -> frozenset[str]:
    return frozenset(legacy_module().INLINE_ARTIFACT_SUFFIXES)


def capabilities() -> dict[str, Any]:
    import sys

    return {
        "platform": sys.platform,
        "capture_pcap_supported": sys.platform in {"win32", "darwin"},
    }


__all__ = [
    "apply_local_environment",
    "build_manager",
    "capabilities",
    "doctor",
    "inline_artifact_suffixes",
    "is_legacy_api_error",
    "is_scheduler_start_error",
    "legacy_module",
    "map_legacy_errors",
    "resolve_avatar_url",
    "resolve_static_asset",
    "set_legacy_module",
    "static_assets",
    "web_dir",
]
