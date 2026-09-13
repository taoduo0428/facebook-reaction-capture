"""Independent macOS Android Reaction capture (no bootstrap or account changes).

Requires an already booted, logged-in device, mitmproxy's installed system CA,
and, when requested, an already running compatible Frida server. Uses bounded
uiautomator/ADB gestures; native-port is reserved for worker isolation, not a
native scroll server. Exit 0 means verified terminal enumeration, 2 means
partial/failure, and 130/143 means cancellation. SIGKILL cannot be recovered.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from analyze_facebook_capture import (
    decode_stream, extract_posts, extract_reactors, iter_records,
    is_reaction_query, record_source_post_id, verify_target_capture,
)
from facebook_capture_progress import summarize_reaction_records

BASE = Path(__file__).resolve().parent
PACKAGE = "com.facebook.katana"
FACEBOOK_URL_COMPONENT = f"{PACKAGE}/.IntentUriHandler"
FACEBOOK_URL_INTENT_FLAGS = "0x34000000"  # NEW_TASK | CLEAR_TOP | SINGLE_TOP
GROUPS = {"mitm", "safe_capture", "progress", "pcap"}


class CaptureError(RuntimeError):
    pass


class Cancelled(CaptureError):
    def __init__(self, signum):
        self.signum = signum
        super().__init__(f"cancelled by signal {signum}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--post-url", required=True)
    parser.add_argument("--post-id")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--capture-port", type=int, default=18081)
    parser.add_argument("--native-port", type=int, default=18766,
                        help="Reserved worker port; this runner uses guarded ADB scrolling")
    parser.add_argument("--frida-ssl-unpin", action="store_true")
    parser.add_argument("--save-artifacts", default="mitm,safe_capture,progress",
                        help="Comma list: mitm,safe_capture,progress; pcap is unsupported")
    # Zero preserves the unlimited terminal-pagination contract. A positive
    # value is an explicit bounded sample and is reported as an absolute cap.
    parser.add_argument("--max-scrolls", type=int, default=0)
    parser.add_argument("--max-seconds", type=int, default=900)
    return parser.parse_args(argv)


def validate_options(args, env):
    selected = {s.strip() for s in args.save_artifacts.split(",") if s.strip()}
    if selected - GROUPS:
        raise CaptureError("Unknown --save-artifacts group")
    if "pcap" in selected:
        raise CaptureError("PCAP unsupported: omit pcap from --save-artifacts; arrange a separate "
                           "authorized macOS tcpdump capture on the intended interface before running.")
    u = urlsplit(args.post_url)
    if (u.scheme != "https" or not u.hostname or
            not (u.hostname == "facebook.com" or u.hostname.endswith(".facebook.com")) or
            u.username or u.password or u.port not in (None, 443) or
            not (re.search(r"/(?:posts|permalink|reel|share|videos)/|/(?:story|permalink)\.php$", u.path)
                 or (u.path.rstrip("/") == "/watch" and re.fullmatch(r"\d{1,32}", (parse_qs(u.query).get("v") or [""])[0])))):
        raise CaptureError("--post-url must be a post-specific HTTPS facebook.com URL")
    if args.post_id and not re.fullmatch(r"[0-9]{1,32}", args.post_id):
        raise CaptureError("--post-id must be a numeric Facebook post ID")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", args.serial):
        raise CaptureError("Invalid ADB serial")
    if any(not 1024 <= p <= 65535 for p in (args.capture_port, args.native_port)) or args.capture_port == args.native_port:
        raise CaptureError("Capture/native ports must be distinct and between 1024 and 65535")
    if not 0 <= args.max_scrolls <= 10000 or not 1 <= args.max_seconds <= 7200:
        raise CaptureError("Invalid capture budget")
    mode = env.get("FACEBOOK_NETWORK_MODE", "direct").strip().lower()
    if mode == "direct":
        return selected, "regular"
    if mode != "upstream":
        raise CaptureError("FACEBOOK_NETWORK_MODE must be direct or upstream")
    upstream = env.get("FACEBOOK_UPSTREAM_PROXY", "")
    p = urlsplit(upstream)
    if (p.scheme not in {"http", "https"} or not p.hostname or not p.port or
            p.username or p.password or p.path not in ("", "/") or p.query or p.fragment):
        raise CaptureError("Set FACEBOOK_UPSTREAM_PROXY to http[s]://host:port (no embedded credentials)")
    return selected, "upstream:" + upstream.rstrip("/")


def inferred_post_id(url):
    u = urlsplit(url)
    match = re.search(r"/(?:posts|permalink)/(\d{1,32})(?:/|$)", u.path)
    if match:
        return match.group(1)
    return (parse_qs(u.query).get("story_fbid") or [None])[0]


def nodes_from_xml(xml):
    start = xml.find("<?xml")
    if start < 0:
        start = xml.find("<hierarchy")
    if start < 0:
        raise CaptureError("ADB UI dump contains no XML")
    end = xml.find("</hierarchy>", start)
    root = ET.fromstring(xml[start:end+len("</hierarchy>")] if end >= 0 else xml[start:].strip())
    nodes = []
    for node in root.iter("node"):
        a = dict(node.attrib)
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
        if not match or a.get("package") != PACKAGE or a.get("enabled") == "false":
            continue
        a["rect"] = tuple(map(int, match.groups()))
        x1, y1, x2, y2 = a["rect"]
        if x2 <= x1 or y2 <= y1:
            continue
        a["label"] = (a.get("content-desc") or a.get("text") or "").strip()
        nodes.append(a)
    if not nodes:
        raise CaptureError("Foreground is not a readable Facebook surface; unlock/login manually")
    return nodes


def reaction_entry(nodes):
    candidates = []
    for node in nodes:
        label, rid = node["label"], node.get("resource-id", "")
        if (node.get("clickable") != "true" or "EditText" in node.get("class", "") or
                node.get("long-clickable") == "true"):
            continue  # Reel like buttons are deliberately not tapped.
        if re.search(r"like button|赞按钮|讚按鈕|^(?:like|liked|赞|讚)$", label, re.I):
            continue
        count = r"[0-9][0-9,.]*\s*[kKmMbB万萬千亿億]?"
        if (rid.endswith("/feed_like_count") and re.fullmatch(count, label)) or re.fullmatch(
                count + r"\s*(?:reactions?|likes|个心情|個心情|个反应|個反應)", label, re.I):
            candidates.append(node)
    if len(candidates) != 1:
        raise CaptureError("No unique safe reaction-count entry; unsupported/ambiguous UI (no tap made)")
    return candidates[0]


def reaction_scroll_rect(nodes):
    if not any(re.fullmatch(r"Reactions(?: tab bar\.)?|心情|回应|回應|反应|反應", n["label"], re.I) for n in nodes):
        raise CaptureError("Reaction list title not confirmed; refusing to scroll")
    containers = [n for n in nodes if n.get("scrollable") == "true"]
    if not containers:
        raise CaptureError("No scrollable Reaction list (UI end does not prove completion)")
    return max(containers, key=lambda n: (n["rect"][2]-n["rect"][0]) * (n["rect"][3]-n["rect"][1]))["rect"]


def target_identity(records, post_id, url):
    # Stronger than substring evidence in the shared verifier: require an exact
    # Story identity in the *matched navigation response*, or its resolver proof.
    navigation = [(line, r) for line, r in records if not is_reaction_query(r.get("friendly_name"))]
    parsed = urlsplit(url)
    if parsed.path.rstrip("/") == "/watch":
        # Shared analyzer understands /videos/id, but not watch?v=id. This is
        # verification-only normalization; Android opens the original URL.
        video = (parse_qs(parsed.query).get("v") or [""])[0]
        url = "https://www.facebook.com/videos/" + video + "/"
    proof = verify_target_capture([], navigation, post_id, url)
    if proof.get("status") != "matched":
        return None
    matched = str(proof.get("matched_post_id") or "")
    record = next((r for line, r in navigation if line == proof.get("matched_line")), {})
    rows = extract_posts(decode_stream(record.get("response_body") or ""), False)
    ids = {str(r["post_id"]) for r in rows if r.get("post_id")}
    if not matched and len(ids) == 1:
        matched = next(iter(ids))
    resolver = proof.get("matched_by") in {"share-url-resolver-story", "share-navigation-graphql", "share-reel-seed-story"}
    if not matched or (not resolver and matched not in ids) or (post_id and matched != post_id):
        return None
    return matched


def visibility_limited(nodes):
    return any(re.search(r"only.*friends.*(?:names|react)|(?:names|react).*only.*friends|"
                         r"只(?:能|会|會)?.*(?:好友|朋友).*(?:姓名|名字)|仅.*好友|僅.*好友", n["label"], re.I)
               for n in nodes)


def progress_state(records, source):
    summary = summarize_reaction_records(records, source)
    if summary.get("observed_source_post_ids") not in ([], [source]):
        return summary, "wrong-source"
    if summary.get("rate_limit_count") or summary.get("session_restricted"):
        return summary, "throttled"
    for _, r in records:
        if is_reaction_query(r.get("friendly_name")):
            if (r.get("status_code") not in (None, 200, "200") or
                    r.get("response_graphql_error_code") or r.get("response_api_error_code")):
                return summary, "response-error"
            if not record_source_post_id(r):
                return summary, "unidentified-source"
    _, pages = extract_reactors(records, False)
    all_pages = [p for p in pages if str(p.get("source_post_id") or "") == source and p.get("reaction_id") in (None, "")]
    if all_pages and max(all_pages, key=lambda p: p["line"]).get("has_next_page") is False:
        return summary, "terminal"
    return summary, "pending"


class Runtime:
    """Injectable process/clock boundary; every child command has a deadline."""
    def run(self, argv, timeout=15, **kwargs):
        try:
            return subprocess.run(argv, timeout=timeout, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise CaptureError("Bounded command timed out: " + Path(argv[0]).name) from exc

    def spawn(self, argv, log, env):
        return subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, start_new_session=True)

    def reserve(self, port):
        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
            return sock
        except BaseException:
            sock.close()
            raise

    def listening(self, port):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.3):
                return True
        except OSError:
            return False

    sleep = staticmethod(time.sleep)
    now = staticmethod(time.monotonic)
    which = staticmethod(shutil.which)


class CaptureRunner:
    def __init__(self, args, runtime=None, env=None):
        self.args = args
        self.rt = runtime or Runtime()
        self.env = dict(os.environ if env is None else env)
        self.children = []
        self.logs = []
        self.leases = []
        self.cleanup_errors = []
        self.proxy_dirty = self.reverse_owned = False
        self.old_proxy = None
        self.run_id = uuid.uuid4().hex
        self.source = None
        self.boundary = 1
        self.attempts = 0
        self.frida_session = self.frida_script = None
        self.lock = None
        self.selected = set()
        self.entry_label = None
        self.visibility_hint = None
        self.pagination_started_at = None
        self.pagination_stop_reason = None
        self.pagination_summary = {}
        self.pagination_state = "pending"
        self.pagination_errors = 0
        self.growth_events = 0
        self.out = args.output_dir.resolve()
        self.capture = self.out / "safe-facebook-http-capture.jsonl"

    @staticmethod
    def pagination_outcome(stop_reason):
        """Map runner wording to the API's persisted pagination contract."""
        if str(stop_reason or "").strip().lower() == "scroll-budget":
            return "absolute-cap"
        return stop_reason

    @staticmethod
    def _utc_now():
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _pagination_record(self, summary=None, state=None, errors=0, records=None, stop_reason=None):
        summary = summary or {}
        records = records or []
        if self.pagination_started_at is None:
            self.pagination_started_at = self.rt.now()
        outcome = self.pagination_outcome(stop_reason or self.pagination_stop_reason)
        return {
            "reaction_key": "__ALL__",
            "attempts": int(self.attempts),
            "growth_events": int(self.growth_events),
            "absolute_cap": int(self.args.max_scrolls) if self.args.max_scrolls > 0 else 0,
            "final_budget": int(self.args.max_scrolls) if self.args.max_scrolls > 0 else 0,
            "unlimited": self.args.max_scrolls == 0,
            "stop_reason": outcome or None,
            "terminal_seen": bool(state == "terminal" or summary.get("terminal_page_seen")),
            "open_seen": bool(summary.get("open_page_seen")),
            "latest_rate_limited": bool(summary.get("latest_rate_limited") or summary.get("rate_limit_count")),
            "latest_graphql_error_code": summary.get("latest_graphql_error_code"),
            "latest_api_error_code": summary.get("latest_api_error_code"),
            "session_restricted": bool(summary.get("session_restricted")),
            "parse_errors": int(errors or 0),
            "record_line_count": max((int(line) for line, _ in records), default=0),
            "page_count": int(summary.get("page_count") or 0),
            "user_count": int(summary.get("user_count") or 0),
            "elapsed_ms": max(0, int((self.rt.now() - self.pagination_started_at) * 1000)),
        }

    def _write_live_progress(self, summary=None, state=None, errors=0, records=None, stop_reason=None):
        if not self.out.exists():
            return
        summary = summary or {}
        records = records or []
        pagination = self._pagination_record(summary, state, errors, records, stop_reason)
        source_key = str(self.source or "__ALL__")
        timestamp = self._utc_now()
        source = {
            "query_records": int(summary.get("query_records") or 0),
            "user_count": int(summary.get("user_count") or 0),
            "page_count": int(summary.get("page_count") or 0),
            "terminal_page_seen": bool(pagination["terminal_seen"]),
            "rate_limit_count": int(summary.get("rate_limit_count") or 0),
            "session_restricted": bool(summary.get("session_restricted")),
            "latest_graphql_error_code": summary.get("latest_graphql_error_code"),
            "latest_api_error_code": summary.get("latest_api_error_code"),
        }
        payload = {
            "version": 1,
            "updated_at": timestamp,
            "record_line_count": pagination["record_line_count"],
            "session": {
                "temporary_feature_block_count": 1 if summary.get("session_restricted") else 0,
                "latest_temporary_feature_block_graphql_code": summary.get("session_restriction_graphql_code"),
                "latest_temporary_feature_block_api_code": summary.get("session_restriction_api_error_code"),
            },
            "families": {"reaction": {"sources": {source_key: source}}},
            "pagination": pagination,
        }
        (self.out / "facebook-live-progress.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _write_diagnostics(self):
        if not self.out.exists():
            return
        records = []
        try:
            records, errors = self.records()
        except (CaptureError, OSError):
            errors = self.pagination_errors
        summary = self.pagination_summary
        state = self.pagination_state
        if not summary:
            summary, state = progress_state(records, self.source)
        if not self.pagination_stop_reason:
            self.pagination_stop_reason = "error"
        self._write_live_progress(summary, state, errors, records, self.pagination_stop_reason)
        ui_dir = self.out / "readonly-ui"
        ui_dir.mkdir(exist_ok=True)
        pagination = self._pagination_record(
            summary, state, errors, records, self.pagination_stop_reason
        )
        payload = {
            "version": 1,
            "actions": [{
                "action": "reaction",
                "candidate": {"label": self.entry_label},
                "visibility_hint": self.visibility_hint,
            }],
            "pagination_runs": [pagination],
            "reaction_screening": {
                "latest_graphql_error_code": pagination.get("latest_graphql_error_code"),
                "latest_api_error_code": pagination.get("latest_api_error_code"),
                "latest_rate_limited": pagination.get("latest_rate_limited"),
                "session_restricted": pagination.get("session_restricted"),
                "parse_errors": pagination.get("parse_errors", 0),
            },
        }
        (ui_dir / "exploration-diagnostics.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _finish_pagination(self, reason, summary, state, errors, records):
        self.pagination_stop_reason = reason
        self.pagination_summary = summary or {}
        self.pagination_state = state or "pending"
        self.pagination_errors = int(errors or 0)
        self._write_live_progress(summary, state, errors, records, reason)
        return reason

    def adb(self, *argv, check=True):
        # adb shell rejoins arguments on Android; quote for the remote shell too.
        command = [self.adb_path, "-s", self.args.serial]
        command += ["shell", shlex.join(argv[1:])] if argv and argv[0] == "shell" else list(argv)
        result = self.rt.run(command, timeout=20)
        if check and result.returncode:
            raise CaptureError("ADB command failed: " + " ".join(argv[:3]))
        return result.stdout.strip()

    def preflight(self):
        self.selected, self.mode = validate_options(self.args, self.env)
        if platform.system() != "Darwin":
            raise CaptureError("This runner requires macOS (Darwin)")
        self.adb_path = self.rt.which("adb")
        self.mitm = self.rt.which("mitmdump")
        if not self.adb_path or not self.mitm or any(Path(p).suffix.lower() == ".exe" for p in (self.adb_path, self.mitm)):
            raise CaptureError("Install native macOS adb and mitmdump on PATH (ARM or Intel)")
        for file in ("analyze_facebook_capture.py", "facebook_capture_addon_safe.py", "facebook_raw_flow_writer.py"):
            if not (BASE / file).is_file():
                raise CaptureError("Missing capture dependency: " + file)
        if self.adb("get-state") != "device" or self.adb("shell", "getprop", "sys.boot_completed") != "1":
            raise CaptureError("Selected device must already be online and booted")
        if "package:" not in self.adb("shell", "pm", "path", PACKAGE):
            raise CaptureError("Facebook is not installed; setup must provision it first")
        ca = Path(self.env.get("FACEBOOK_MITMPROXY_CA", str(Path.home()/".mitmproxy/mitmproxy-ca-cert.pem"))).expanduser()
        if not ca.is_file():
            raise CaptureError("Provision mitmproxy CA first, or set FACEBOOK_MITMPROXY_CA")
        self.ca_dir = ca.parent
        pem = ca.read_text(encoding="ascii")
        self.ca_der = ssl.PEM_cert_to_DER_cert(pem)
        openssl = self.rt.which("openssl")
        if not openssl:
            raise CaptureError("openssl is required to verify the installed Android system CA")
        result = self.rt.run([openssl, "x509", "-in", str(ca), "-subject_hash_old", "-noout"], timeout=10)
        ca_hash = result.stdout.strip()
        if result.returncode or not re.fullmatch(r"[a-fA-F0-9]{8}", ca_hash):
            raise CaptureError("Unable to identify the mitmproxy CA")
        trusted = False
        for directory in ("/apex/com.android.conscrypt/cacerts", "/system/etc/security/cacerts"):
            installed = self.adb("shell", "cat", f"{directory}/{ca_hash}.0", check=False)
            try:
                trusted |= ssl.PEM_cert_to_DER_cert(installed) == self.ca_der
            except (ValueError, ssl.SSLError):
                pass
        if not trusted:
            raise CaptureError("Matching mitmproxy CA not readable in Android system trust; provision it in setup and restart Facebook")
        self.old_proxy = self.adb("shell", "settings", "get", "global", "http_proxy")
        if not re.fullmatch(r"null|:0|[^\s'\";]+:\d+", self.old_proxy):
            raise CaptureError("Unsupported existing Android proxy setting; refusing to replace it")
        existing = self.adb("reverse", "--list")
        if any(f"tcp:{self.args.capture_port}" in line.split()[1:2] for line in existing.splitlines()):
            raise CaptureError("Selected Android reverse port already belongs to another service")
        if self.args.frida_ssl_unpin:
            self.prepare_frida()

    def prepare_frida(self):
        # Probe out of process so unresponsive Frida RPC is bounded. Do not start
        # a server, spawn/kill Facebook, or eternalize hooks in the user's app.
        code = ("import frida,sys; d=frida.get_device(sys.argv[1],timeout=5); "
                "p=[p.pid for p in d.enumerate_processes() if p.name=='com.facebook.katana']; "
                "assert len(p)==1, 'Start logged-in Facebook manually first'; print(p[0])")
        result = self.rt.run([sys.executable, "-c", code, self.args.serial], timeout=12)
        if result.returncode or not result.stdout.strip().isdigit():
            raise CaptureError("Frida not ready: install matching Python/server versions and start logged-in Facebook manually")
        self.frida_pid = result.stdout.strip()

    def start_child(self, argv, name, env=None):
        log = (self.out / name).open("w", encoding="utf-8")
        self.logs.append(log)
        child = self.rt.spawn(argv, log, env or self.env)
        self.children.append(child)
        return child

    def check_children(self):
        if any(child.poll() is not None for child in self.children):
            raise CaptureError("Owned capture/Frida process exited unexpectedly; inspect run logs")

    def wait_for(self, predicate, seconds):
        deadline = self.rt.now() + seconds
        while self.rt.now() < deadline:
            self.check_children()
            if predicate():
                return
            self.rt.sleep(.25)
        raise CaptureError("Capture readiness timed out")

    def start_transport(self):
        self.leases.append(self.rt.reserve(self.args.native_port))
        reservation = self.rt.reserve(self.args.capture_port)
        reservation.close()  # mitmdump must bind this port itself; check its liveness.
        env = dict(self.env, FACEBOOK_CAPTURE_DIR=str(self.out), FACEBOOK_CAPTURE_MAX_FLOWS="0")
        command = [self.mitm, "--mode", self.mode, "--listen-host", "127.0.0.1",
                   "--listen-port", str(self.args.capture_port), "--set", "confdir="+str(self.ca_dir),
                   "--set", "connection_strategy=lazy", "--set", "block_global=false",
                   "-s", str(BASE/"facebook_capture_addon_safe.py")]
        if "mitm" in self.selected:
            command += ["-s", str(BASE/"facebook_raw_flow_writer.py")]
        self.start_child(command, "mitmdump.log", env)
        self.wait_for(lambda: self.rt.listening(self.args.capture_port), 15)
        self.rt.sleep(.3)
        self.check_children()
        if self.adb("shell", "settings", "get", "global", "http_proxy") != self.old_proxy:
            raise CaptureError("Android proxy changed since preflight; refusing to overwrite concurrent work")
        self.adb("reverse", "--no-rebind", f"tcp:{self.args.capture_port}", f"tcp:{self.args.capture_port}")
        self.reverse_owned = True
        self.proxy_dirty = True  # set before mutation: timeouts can occur after it applied.
        self.adb("shell", "settings", "put", "global", "http_proxy", f"127.0.0.1:{self.args.capture_port}")
        if self.adb("shell", "settings", "get", "global", "http_proxy") != f"127.0.0.1:{self.args.capture_port}":
            raise CaptureError("Android capture proxy did not persist")
        if self.args.frida_ssl_unpin:
            # Dedicated child owns the attachment. Normal termination detaches
            # it and unloads hooks; no --eternalize and no remote process kill.
            code = ("import frida,sys,time; d=frida.get_device(sys.argv[1],timeout=5); "
                    "s=d.attach(int(sys.argv[2])); "
                    "j=s.create_script(open(sys.argv[3]).read()); "
                    "j.on('message',lambda m,data: print(m,flush=True)); j.load(); "
                    "print('ATTACHED',flush=True); time.sleep(7200)")
            self.start_child([sys.executable, "-u", "-c", code, self.args.serial,
                              self.frida_pid, str(BASE/"facebook_ssl_unpin.js")], "frida.log")
            self.wait_for(lambda: "hook installed" in (self.out/"frida.log").read_text(encoding="utf-8"), 15)

    def records(self):
        if not self.capture.exists():
            return [], 0
        if self.capture.stat().st_size > 256 * 1024 * 1024:
            raise CaptureError("Capture exceeded 256 MiB safety budget; retaining partial data")
        return iter_records(self.capture, 0, min_line=self.boundary)

    def dump(self):
        # /dev/tty avoids a shared on-device XML filename and stale snapshots.
        return nodes_from_xml(self.adb("shell", "uiautomator", "dump", "/dev/tty"))

    def navigate(self):
        if self.capture.exists():
            with self.capture.open("rb") as handle:
                self.boundary = sum(1 for _ in handle) + 1
        url = self.args.post_url
        result = self.adb("shell", "am", "start", "-W", "-a", "android.intent.action.VIEW",
                          "-d", url, "-n", FACEBOOK_URL_COMPONENT, "-f", FACEBOOK_URL_INTENT_FLAGS)
        if re.search(r"Error:|Exception|unable to resolve", result, re.I):
            raise CaptureError("Facebook could not open the target URL")
        expected = self.args.post_id or inferred_post_id(url)
        def confirmed():
            records, _ = self.records()
            self.source = target_identity(records, expected, url)
            return bool(self.source)
        self.wait_for(confirmed, 30)
        entry = reaction_entry(self.dump())
        self.entry_label = entry["label"]
        x1, y1, x2, y2 = entry["rect"]
        self.adb("shell", "input", "tap", str((x1+x2)//2), str((y1+y2)//2))
        self.rt.sleep(2)

    def paginate(self):
        self.pagination_started_at = self.rt.now()
        deadline = self.rt.now() + self.args.max_seconds
        unchanged = 0
        last = None
        last_summary = {}
        last_state = "pending"
        last_errors = 0
        last_records = []
        while self.rt.now() < deadline:
            self.check_children()
            records, errors = self.records()
            summary, state = progress_state(records, self.source)
            last_summary, last_state = summary, state
            last_errors, last_records = errors, records
            self._write_live_progress(summary, state, errors, records)
            if errors:
                self._finish_pagination("parse-error", summary, state, errors, records)
                raise CaptureError("Capture JSONL parse errors; retaining evidence")
            nodes = self.dump()
            if visibility_limited(nodes):
                self.visibility_hint = "friends-only-names"
                return self._finish_pagination(
                    "visibility-limited" if summary.get("user_count") else "visibility-limited-no-rows",
                    summary, state, errors, records,
                )
            if state != "pending":
                return self._finish_pagination(state, summary, state, errors, records)
            if self.args.max_scrolls and self.attempts >= self.args.max_scrolls:
                return self._finish_pagination("scroll-budget", summary, state, errors, records)
            marker = (summary.get("page_count"), summary.get("user_count"))
            if marker != last:
                self.growth_events += 1
            unchanged = unchanged + 1 if marker == last else 0
            last = marker
            if unchanged >= 6:
                return self._finish_pagination("no-growth", summary, state, errors, records)
            x1, y1, x2, y2 = reaction_scroll_rect(nodes)
            self.adb("shell", "input", "swipe", str((x1+x2)//2), str(y2-(y2-y1)//5),
                     str((x1+x2)//2), str(y1+(y2-y1)//5), "350")
            self.attempts += 1
            self.rt.sleep(2)
        return self._finish_pagination(
            "time-budget", last_summary, last_state, last_errors, last_records
        )

    def cleanup(self):
        def attempt(label, callback):
            try:
                callback()
            except Exception:
                self.cleanup_errors.append(label)
        if self.proxy_dirty:
            def restore():
                current = self.adb("shell", "settings", "get", "global", "http_proxy")
                if current not in (self.old_proxy, f"127.0.0.1:{self.args.capture_port}"):
                    raise CaptureError("Android proxy changed externally; leave current setting untouched")
                if self.old_proxy == "null":
                    self.adb("shell", "settings", "delete", "global", "http_proxy")
                else:
                    self.adb("shell", "settings", "put", "global", "http_proxy", self.old_proxy)
                if self.adb("shell", "settings", "get", "global", "http_proxy") != self.old_proxy:
                    raise CaptureError("Proxy restore readback failed")
            attempt("restore Android http_proxy", restore)
        if self.reverse_owned:
            attempt("remove owned adb reverse", lambda: self.adb("reverse", "--remove", f"tcp:{self.args.capture_port}"))
        for child in reversed(self.children):
            def stop(child=child):
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
            attempt("stop owned child", stop)
        for handle in self.logs + self.leases:
            attempt("close owned handle", handle.close)

    def analyze(self):
        self.capture.touch(exist_ok=True)
        self._write_diagnostics()
        command = [sys.executable, str(BASE/"analyze_facebook_capture.py"), "--input", str(self.capture),
                   "--out-dir", str(self.out/"analysis"), "--expected-url", self.args.post_url,
                   "--require-target-match", "--min-line", str(self.boundary), "--max-records", "0"]
        expected = self.args.post_id or self.source or inferred_post_id(self.args.post_url)
        if expected:
            command += ["--expected-post-id", expected]
        # Do not focus-filter: the report must retain wrong-source contamination.
        result = self.rt.run(command, timeout=120)
        (self.out/"analyzer.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        return result.returncode

    def validate_result(self):
        report = json.loads((self.out/"analysis/facebook-sample-patterns.json").read_text(encoding="utf-8"))
        target = report.get("target_verification") or {}
        integrity = report.get("reaction_integrity") or {}
        sources = (report.get("source_post_ids_by_family") or {}).get("reactions")
        outcome = self.pagination_outcome(self.pagination_stop_reason)
        if (not self.source or target.get("status") != "matched" or
                str(target.get("matched_post_id") or "") != self.source or sources != [self.source] or
                report.get("line_parse_errors")):
            raise CaptureError("Strict analysis target/source validation failed")
        with (self.out/"analysis/facebook-reactor-users.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if any(r.get("source_post_id") != self.source or not r.get("user_id") for r in rows):
            raise CaptureError("Reaction CSV contains invalid identities or wrong sources")
        if int(integrity.get("reported_total_count") or 0) > 0 and not rows:
            raise CaptureError("Reported reactions exist but no user identities were captured")
        records, errors = self.records()
        state = progress_state(records, self.source)[1]
        if errors:
            raise CaptureError("Final capture contains parse errors")
        if outcome == "terminal":
            if (not integrity.get("completion_claim_allowed") or
                    integrity.get("last_successful_page_has_next") is not False or
                    state != "terminal"):
                raise CaptureError("Final capture is not an unthrottled ALL terminal traversal")
            return
        if outcome == "absolute-cap":
            if self.args.max_scrolls <= 0 or state == "terminal":
                raise CaptureError("Bounded capture has inconsistent pagination state")
            if (integrity.get("completion_claim_allowed") or
                    int(integrity.get("rate_limited_records") or 0) or
                    int(integrity.get("temporary_feature_block_records") or 0)):
                raise CaptureError("Bounded capture contains terminal or restricted evidence")
            return
        raise CaptureError("Final capture has no accepted pagination outcome")

    def execute(self):
        self.preflight()  # Read-only; PCAP/host validation occurs before ADB calls.
        # Cross-process per-device flock protects independent runners. API/setup
        # must coordinate its own jobs; never steal a stale lock or unknown service.
        import fcntl
        key = hashlib.sha256(self.args.serial.encode()).hexdigest()[:24]
        self.lock = open(Path(tempfile.gettempdir())/f"facebook-android-{key}.lock", "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise CaptureError("Another Android capture runner owns this serial")
        old_handlers = {}
        code, reason = 2, "not-started"
        created = False
        def cancel(signum, frame):
            raise Cancelled(signum)
        try:
            if self.out.exists() and any(self.out.iterdir()):
                raise CaptureError("--output-dir must be new or empty; existing runs are never overwritten")
            self.out.mkdir(parents=True, exist_ok=True)
            with (self.out/".android-capture-owner").open("x", encoding="ascii") as marker:
                marker.write(self.run_id)
            created = True
            for sig in (signal.SIGINT, signal.SIGTERM):
                old_handlers[sig] = signal.signal(sig, cancel)
            try:
                self.start_transport()
                self.navigate()
                reason = self.paginate()
                code = 0 if self.pagination_outcome(reason) in {"terminal", "absolute-cap"} else 2
            except Cancelled as exc:
                code, reason = 128 + exc.signum, str(exc)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, CaptureError) else type(exc).__name__
                code = 2
            finally:
                # A repeated SIGTERM must not interrupt proxy restoration.
                for sig in old_handlers:
                    signal.signal(sig, signal.SIG_IGN)
                self.cleanup()
            if not self.pagination_stop_reason:
                self.pagination_stop_reason = reason
            try:
                analyzer_code = self.analyze()
                if code == 0:
                    if analyzer_code:
                        raise CaptureError("Analyzer rejected target")
                    self.validate_result()
            except Exception as exc:
                if code == 0:
                    code = 2
                reason += "; analysis: " + (str(exc) if isinstance(exc, CaptureError) else type(exc).__name__)
            if self.cleanup_errors:
                code = code if code in (130, 143) else 2
                reason += "; cleanup incomplete: " + ", ".join(self.cleanup_errors)
            # Failed/cancelled captures retain safe evidence even when not requested.
            if code == 0:
                for group, name in (("safe_capture", "safe-facebook-http-capture.jsonl"), ("progress", "facebook-live-progress.json")):
                    if group not in self.selected:
                        (self.out/name).unlink(missing_ok=True)
            outcome = self.pagination_outcome(self.pagination_stop_reason or reason)
            completion_scope = (
                "terminal" if outcome == "terminal" else
                "bounded-sample" if outcome == "absolute-cap" else None
            )
            status = {"run_id": self.run_id, "exit_code": code, "stop_reason": reason,
                      "pagination_outcome": outcome, "completion_scope": completion_scope,
                      "source_post_id": self.source, "attempts": self.attempts,
                      "completion_claim_allowed": code == 0 and outcome == "terminal", "cleanup_errors": self.cleanup_errors,
                      "native_scroll": "disabled: guarded ADB XML gestures; native-port reserved",
                      "save_artifacts": sorted(self.selected), "partial_evidence_retained": code != 0}
            (self.out/"android-capture-status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
            return code
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
            self.lock.close()
            if created:
                print(f"Capture finished: {self.out}")


def main(argv=None):
    try:
        return CaptureRunner(parse_args(argv)).execute()
    except (CaptureError, ValueError, OSError) as exc:
        print(str(exc) if isinstance(exc, CaptureError) else type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
