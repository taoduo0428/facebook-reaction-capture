"""Performance harness (not part of the runtime package).

* ``fake_server`` — FastAPI app with an in-memory manager (no device) for HTTP load.
* ``k6_api.js`` — k6 profile (reads + submissions).
* ``run_api_load.py`` — starts the server, runs k6, samples CPU/RSS, writes a report.
* ``scheduler_bench.py`` — scheduler throughput with the mock executor.
* ``sampler.py`` — dependency-free CPU/RSS reader.
"""
from __future__ import annotations

__all__ = ["fake_server", "run_api_load", "sampler", "scheduler_bench"]
