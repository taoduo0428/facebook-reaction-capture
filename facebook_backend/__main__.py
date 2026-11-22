"""``python -m facebook_backend`` — the FastAPI entrypoint.

Single-process by design.  ``JobManager`` keeps the queue, the scheduler thread,
the capture lease and the per-job process table in memory; two Uvicorn workers
would own two independent schedulers over the same manifests and could start
two capture processes for one job.
"""
from __future__ import annotations

import argparse
import os
import sys

from facebook_backend.config import LOOPBACK_HOSTS, Settings
from facebook_backend.main import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Facebook capture automation API (FastAPI)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--log-level", default="info")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {}
    if args.host is not None:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = args.port
    if args.serial is not None:
        overrides["serial"] = args.serial
    settings = Settings.from_env(**overrides)

    if settings.host not in LOOPBACK_HOSTS:
        raise SystemExit(
            "refusing non-loopback bind; expose this service only through an authenticated private tunnel"
        )
    requested_workers = settings.uvicorn_workers
    if requested_workers != 1:
        raise SystemExit(
            "the in-process scheduler requires exactly one uvicorn worker "
            f"(FACEBOOK_API_WORKERS={requested_workers}); cross-process scheduling is not implemented"
        )
    if os.environ.get("WEB_CONCURRENCY") not in (None, "", "1"):
        raise SystemExit(
            "WEB_CONCURRENCY must be 1: the in-process scheduler cannot be shared between processes"
        )

    import uvicorn

    app = create_app(settings=settings)
    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_level=args.log_level,
                            access_log=False, workers=1)
    server = uvicorn.Server(config)
    # Reserve the listener before lifespan can recover a job or start a worker.
    # An occupied port must leave persisted jobs untouched.
    with config.bind_socket() as listener:
        server.run(sockets=[listener])
    return 0 if server.started else 1


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    sys.exit(main())
