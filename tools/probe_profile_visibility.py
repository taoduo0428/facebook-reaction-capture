from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
ADB = BASE / "platform-tools" / "adb.exe"
SERIAL = "emulator-5554"
EXCLUDE = {
    "100023402828449", "1770921688", "100000832151739",
    "100002479694500", "100002704356402", "100001389831058",
}
MAX_PER_FAMILY = 12
MIN_PER_FAMILY = 8
MAX_TOTAL = 24
REMOTE_XML = "/sdcard/profile-visibility-probe.xml"

ADD_FRIEND_RE = re.compile(r"(?i)(add\s+friend|添加好友|加好友|新增好友|新增朋友|加為好友|加为好友|成為朋友|成为朋友)")
MESSAGE_RE = re.compile(r"(?i)(message|发消息|發消息|傳送訊息|发送消息|傳訊息)")
EXPLICIT_RESTRICTED_RE = re.compile(
    r"(?i)(friends\s+only|only\s+friends|only.*friends.*see|add.*friend.*see|"
    r"仅好友|僅好友|只有好友|只有朋友|只限好友|只能.*好友|添加好友.*查看|加好友.*查看|"
    r"no\s+posts\s+(?:to\s+show|available)|nothing\s+to\s+show|"
    r"没有可显示的帖子|沒有可顯示的貼文|没有帖子可显示|沒有貼文可顯示)"
)
POST_COUNT_RE = re.compile(r"(?i)([0-9][0-9,.\s]*|[0-9.]+\s*[KkMm]|[0-9.]+\s*[万萬])\s*(?:posts?|帖子|貼文)")
FRIEND_COUNT_RE = re.compile(r"(?i)([0-9][0-9,.\s]*|[0-9.]+\s*[KkMm]|[0-9.]+\s*[万萬])\s*(?:friends?|好友|朋友)")
ACTION_RE = re.compile(r"(?i)^(like|comment|comments|share|赞|讚|评论|評論|分享|回复|回覆)$")
PROFILE_TAB_RE = re.compile(r"(?i)^(posts?|帖子|貼文|全部|all|photos?|照片|reels?)$")


def adb(*args: str, timeout: int = 20, check: bool = True, binary: bool = False):
    cmd = [str(ADB), "-s", SERIAL, *args]
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if check and result.returncode != 0:
        err = result.stderr.decode("utf-8", "ignore") if isinstance(result.stderr, bytes) else str(result.stderr)
        raise RuntimeError(f"adb failed: {' '.join(args)} :: {err.strip()}")
    if binary:
        return result.stdout
    return result.stdout.decode("utf-8", "ignore").strip()


def parse_number(raw: str | None):
    if not raw:
        return None
    s = raw.replace(",", "").replace(" ", "")
    mult = 1
    if s[-1:] in ("K", "k"):
        mult, s = 1000, s[:-1]
    elif s[-1:] in ("M", "m"):
        mult, s = 1_000_000, s[:-1]
    elif s[-1:] in ("万", "萬"):
        mult, s = 10_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def node_labels(path: Path):
    raw = path.read_text(encoding="utf-8", errors="ignore")
    labels = []
    buttons = []
    for tag in re.findall(r"<node\b[^>]*>", raw):
        attrs = dict(re.findall(r'([A-Za-z][A-Za-z0-9_-]*)="([^"]*)"', tag))
        text = (attrs.get("text") or "").strip()
        desc = (attrs.get("content-desc") or "").strip()
        label = text or desc
        if label:
            labels.append(label)
        if attrs.get("class") == "android.widget.Button" and label:
            buttons.append(label)
    return labels, buttons


def dump_ui(path: Path):
    adb("shell", "uiautomator", "dump", REMOTE_XML, timeout=20)
    adb("pull", REMOTE_XML, str(path), timeout=20)
    return node_labels(path)


def screenshot(path: Path):
    data = adb("exec-out", "screencap", "-p", timeout=20, binary=True)
    path.write_bytes(data)


def collect_candidates():
    rows = []
    seen = set()
    run_dirs = sorted((BASE / "runs").glob("20*"), key=lambda p: p.name, reverse=True)
    for family, filename, id_field, name_field in (
        ("reaction", "facebook-reactor-users.csv", "user_id", "user_name"),
        ("share", "facebook-share-users.csv", "actor_id", "actor_name"),
    ):
        count = 0
        for run in run_dirs:
            path = run / "analysis" / filename
            if not path.exists():
                continue
            try:
                with path.open(encoding="utf-8-sig", newline="") as fh:
                    for row in csv.DictReader(fh):
                        uid = str(row.get(id_field) or "").strip()
                        if not uid or uid in EXCLUDE or not uid.isdigit():
                            continue
                        key = (family, uid)
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append({
                            "family": family,
                            "user_id": uid,
                            "user_name": str(row.get(name_field) or "").strip(),
                            "source_post_id": str(row.get("source_post_id") or "").strip(),
                            "source_run": run.name,
                        })
                        count += 1
                        if count >= MAX_PER_FAMILY:
                            break
            except Exception:
                continue
            if count >= MAX_PER_FAMILY:
                break
    # interleave families so both are represented early
    by_family = defaultdict(list)
    for row in rows:
        by_family[row["family"]].append(row)
    interleaved = []
    for i in range(max(len(by_family["reaction"]), len(by_family["share"]))):
        for family in ("reaction", "share"):
            if i < len(by_family[family]):
                interleaved.append(by_family[family][i])
    return interleaved[:MAX_TOTAL]


def inspect_profile(row: dict, out: Path):
    uid = row["user_id"]
    user_dir = out / "profiles" / uid
    user_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.facebook.com/{uid}"
    adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url, "-p", "com.facebook.katana", timeout=20)
    time.sleep(5)

    initial_xml = user_dir / "initial.xml"
    labels0, buttons0 = dump_ui(initial_xml)

    # two read-only scrolls to expose the post area without tapping anything
    adb("shell", "input", "swipe", "540", "1600", "540", "700", "500")
    time.sleep(2)
    scroll1_xml = user_dir / "scroll-1.xml"
    labels1, buttons1 = dump_ui(scroll1_xml)

    adb("shell", "input", "swipe", "540", "1600", "540", "700", "500")
    time.sleep(2)
    scroll2_xml = user_dir / "scroll-2.xml"
    labels2, buttons2 = dump_ui(scroll2_xml)

    labels = labels0 + labels1 + labels2
    buttons = buttons0 + buttons1 + buttons2
    joined = "\n".join(labels)
    joined_buttons = "\n".join(buttons)
    add_friend = bool(ADD_FRIEND_RE.search(joined_buttons) or ADD_FRIEND_RE.search(joined))
    message = bool(MESSAGE_RE.search(joined_buttons) or MESSAGE_RE.search(joined))
    explicit_restricted = bool(EXPLICIT_RESTRICTED_RE.search(joined))

    post_count = None
    friend_count = None
    for label in labels:
        if post_count is None:
            m = POST_COUNT_RE.search(label)
            if m:
                post_count = parse_number(m.group(1))
        if friend_count is None:
            m = FRIEND_COUNT_RE.search(label)
            if m:
                friend_count = parse_number(m.group(1))

    action_markers = [x for x in labels1 + labels2 if ACTION_RE.match(x.strip())]
    tabs = [x for x in labels0 if PROFILE_TAB_RE.match(x.strip())]
    visible_post_actions = len(action_markers)
    name_seen = bool(row["user_name"] and any(row["user_name"].lower() in x.lower() for x in labels0))

    # Conservative classifications: strong requires either an explicit restriction
    # or a positive post count with no visible post action row after two scrolls.
    if add_friend and explicit_restricted:
        classification = "strong-nonfriend-restricted"
    elif add_friend and post_count and post_count > 0 and visible_post_actions == 0:
        classification = "strong-nonfriend-posts-hidden"
    elif add_friend and visible_post_actions == 0:
        classification = "nonfriend-limited-candidate"
    elif visible_post_actions > 0:
        classification = "public-posts-visible"
    else:
        classification = "uncertain"

    result = {
        **row,
        "profile_url": url,
        "name_seen": name_seen,
        "add_friend_visible": add_friend,
        "message_visible": message,
        "explicit_restriction_text": explicit_restricted,
        "reported_post_count": post_count,
        "reported_friend_count": friend_count,
        "visible_post_action_markers": visible_post_actions,
        "profile_tabs": tabs[:10],
        "classification": classification,
        "initial_buttons": buttons0[:20],
        "scroll_buttons": (buttons1 + buttons2)[:30],
        "xml_dir": str(user_dir.relative_to(out)).replace("\\", "/"),
    }
    if classification.startswith("strong-") or classification == "nonfriend-limited-candidate":
        shot = user_dir / "candidate.png"
        screenshot(shot)
        result["candidate_screenshot"] = str(shot.relative_to(out)).replace("\\", "/")
    else:
        result["candidate_screenshot"] = ""
    return result


def main():
    if not ADB.exists():
        raise SystemExit(f"adb not found: {ADB}")
    state = adb("get-state")
    if state.strip() != "device":
        raise SystemExit(f"ADB not ready: {state}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = BASE / "runs" / f"profile-visibility-audit-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    candidates = collect_candidates()
    results = []
    family_tested = defaultdict(int)
    strong = 0

    for row in candidates:
        if len(results) >= MAX_TOTAL:
            break
        try:
            result = inspect_profile(row, out)
        except Exception as exc:
            result = {**row, "classification": "probe-error", "error": str(exc)}
        results.append(result)
        family_tested[row["family"]] += 1
        if str(result.get("classification", "")).startswith("strong-"):
            strong += 1
        # Stop early only after both families have meaningful coverage.
        if strong >= 3 and all(family_tested[f] >= MIN_PER_FAMILY for f in ("reaction", "share")):
            break

    (out / "audit-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = [
        "family", "source_run", "source_post_id", "user_id", "user_name", "classification",
        "name_seen", "add_friend_visible", "message_visible", "explicit_restriction_text",
        "reported_post_count", "reported_friend_count", "visible_post_action_markers",
        "candidate_screenshot", "profile_url", "xml_dir",
    ]
    with (out / "audit-results.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    proxy = adb("shell", "settings", "get", "global", "http_proxy", check=False)
    reverse = adb("reverse", "--list", check=False)
    marker = (BASE / ".facebook-capture-active.json").exists()
    summary = {
        "output_dir": str(out),
        "tested": len(results),
        "family_tested": dict(family_tested),
        "classifications": dict((k, sum(1 for r in results if r.get("classification") == k)) for k in sorted({r.get("classification") for r in results})),
        "strong_candidates": [
            {k: r.get(k) for k in ("family", "source_post_id", "user_id", "user_name", "classification", "reported_post_count", "visible_post_action_markers", "candidate_screenshot")}
            for r in results if str(r.get("classification", "")).startswith("strong-")
        ],
        "transport": {
            "adb_state": adb("get-state", check=False),
            "proxy": proxy,
            "reverse_7897": "tcp:7897 tcp:7897" in reverse,
            "reverse_8081": "tcp:8081 tcp:8081" in reverse,
            "capture_marker": marker,
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
