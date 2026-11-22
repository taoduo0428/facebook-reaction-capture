"""Artifact catalogue: logical names, relative paths and required/optional sets.

These values used to live at the top of ``facebook_capture_api.py``.  They are
pure data (no I/O, no imports from the service), so both the legacy module and
the new artifact manager can share one definition.  Logical names are part of
the HTTP contract (``/v1/jobs/{job_id}/artifacts/{name}``) and must never be
renamed casually.
"""
from __future__ import annotations

from pathlib import Path

#: ``(logical name, Chinese label, layer, path relative to the run directory)``
ARTIFACT_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("raw-network.pcap", "原始网络 PCAP", "TLS 解密前", "raw-network.pcap"),
    ("raw-network-capture.json", "PCAP 校验信息", "完整性", "raw-network-capture.json"),
    ("raw-facebook-http.mitm", "完整 MITM flow", "实时解密", "raw-facebook-http.mitm"),
    ("raw-facebook-http-index.jsonl", "安全 flow 索引", "实时解密", "raw-facebook-http-index.jsonl"),
    ("safe-facebook-http-capture.jsonl", "脱敏 GraphQL 记录", "安全分析", "safe-facebook-http-capture.jsonl"),
    ("facebook-live-progress.json", "分页实时状态", "抓取进度", "facebook-live-progress.json"),
    ("raw-facebook-http-summary.json", "MITM 校验摘要", "完整性", "raw-facebook-http-summary.json"),
    ("facebook-sample-patterns.json", "解析与完整性报告", "解析结果", "analysis/facebook-sample-patterns.json"),
    ("facebook-reactor-users.csv", "Reaction 用户结果", "解析结果", "analysis/facebook-reactor-users.csv"),
)

ARTIFACT_PATHS: dict[str, Path] = {name: Path(relative) for name, _label, _layer, relative in ARTIFACT_SPECS}

INLINE_ARTIFACT_SUFFIXES: frozenset[str] = frozenset({".csv", ".json", ".jsonl", ".log", ".txt"})

OPTIONAL_ARTIFACT_GROUPS: dict[str, set[str]] = {
    "pcap": {"raw-network.pcap", "raw-network-capture.json"},
    "mitm": {"raw-facebook-http.mitm", "raw-facebook-http-index.jsonl", "raw-facebook-http-summary.json"},
    "safe_capture": {"safe-facebook-http-capture.jsonl"},
    "progress": {"facebook-live-progress.json"},
}

# Default to the lightweight evidence needed for live parsing and progress.
# PCAP and full MITM archives remain opt-in diagnostics because they add heavy
# disk I/O without being required for the final Reaction-user result.
DEFAULT_SAVE_ARTIFACTS: tuple[str, ...] = ("safe_capture", "progress")

REQUIRED_ARTIFACTS: frozenset[str] = frozenset(
    {"facebook-sample-patterns.json", "facebook-reactor-users.csv"}
)

ARTIFACT_GROUP_BY_NAME: dict[str, str] = {
    name: group for group, names in OPTIONAL_ARTIFACT_GROUPS.items() for name in names
}

BATCH_ARTIFACT_SPECS: tuple[tuple[str, str, str], ...] = (
    ("batch-summary.json", "批次汇总", "批次结果"),
    ("batch-posts.csv", "帖子任务明细", "批次结果"),
    ("batch-reactor-users.csv", "帖子-用户记录", "批次结果"),
    ("batch-user-post-relations.csv", "用户与帖子关系", "批次结果"),
)

__all__ = [
    "ARTIFACT_GROUP_BY_NAME",
    "ARTIFACT_PATHS",
    "ARTIFACT_SPECS",
    "BATCH_ARTIFACT_SPECS",
    "DEFAULT_SAVE_ARTIFACTS",
    "INLINE_ARTIFACT_SUFFIXES",
    "OPTIONAL_ARTIFACT_GROUPS",
    "REQUIRED_ARTIFACTS",
]
