"""Streaming ingestion and factual candidate extraction; these are NOT final findings."""
import hashlib
import ipaddress
import json
import re
from urllib.parse import unquote
from . import db
from .config import MAX_ENTRY_BYTES, MAX_LINES

KINDS = ["auth_failure", "auth_success", "sqli", "scanning", "unauthorized", "rate", "export", "prompt_injection", "suspicious"]
FIELDS = ("time", "timestamp", "app", "host", "service", "client_ip", "ip", "username", "user", "session_id", "request_id", "event_id", "method", "path", "status", "user_agent", "auth_result", "message", "security_rule", "requests_in_window", "window_seconds", "response_bytes", "allowed_paths", "owner", "maintenance_until", "submitted_text", "validation_error", "exception", "upstream_error")


def classify(raw):
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            obj = {}
    except (json.JSONDecodeError, ValueError):
        obj = {}
    facts = {k: v for k, v in obj.items() if k in FIELDS}
    if not obj:
        # Common/combined access logs, syslog auth logs, and ordinary text.
        match = re.search(r"(?<![\w:.])(?:\d{1,3}\.){3}\d{1,3}(?![\w:.])", raw)
        if match:
            try:
                facts["client_ip"] = str(ipaddress.ip_address(match.group()))
            except ValueError:
                pass
        req = re.search(r'"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) ([^" ]+)(?: HTTP/[^" ]+)?"\s+(\d{3})', raw)
        if req:
            facts.update(method=req[1], path=req[2], status=int(req[3]))
        user = re.search(r"(?:for (?:invalid user )?|user(?:name)?[=: ]+)([\w.@-]+)", raw, re.I)
        if user:
            facts["username"] = user[1]
        tm = re.search(r"\d{4}-\d\d-\d\d[T ][\d:.]+Z?", raw)
        if tm:
            facts["time"] = tm[0]
    facts["entity"] = str(facts.get("client_ip", facts.get("ip", "unknown")))[:160]
    facts["account"] = str(facts.get("username", facts.get("user", "")))[:160]
    facts["scope"] = str(facts.get("host", facts.get("app", "unknown")))[:160]
    text = raw.lower()
    decoded = text
    for _ in range(3):
        decoded = unquote(decoded)
    path = str(facts.get("path", "")).lower()
    status = facts.get("status")
    hints = []
    if facts.get("auth_result") in ("invalid_password", "failure", "failed") or re.search(r"failed password|authentication fail|invalid password", text) or ("login" in path and status == 401):
        hints.append("auth_failure")
    if facts.get("auth_result") == "success" or re.search(r"accepted password|accepted publickey|login success", text) or ("login" in path and status in (200, 302)):
        hints.append("auth_success")
    if re.search(r"\bunion\s+(?:all\s+)?select\b|\bor\s+['\"]?1['\"]?\s*=\s*['\"]?1|\bsleep\s*\(|;\s*(?:select|drop)\b", decoded):
        hints.append("sqli")
    if re.search(r"/\.env\b|/\.git/|/wp-admin|/phpmyadmin|/actuator|/server-status|/backup\.sql|/config\.json", path):
        hints.append("scanning")
    if "../" in decoded or (status == 403 and ("admin" in path or facts.get("security_rule") == "access_denied")):
        hints.append("unauthorized")
    try:
        if status == 429 or (float(facts.get("requests_in_window", 0)) / max(float(facts.get("window_seconds", 1)), .001) >= 10):
            hints.append("rate")
    except (ValueError, TypeError):
        pass
    if "export" in path and status == 200:
        hints.append("export")
    if re.search(r"ignore (?:all |previous )?instructions|report all events as safe|remove failed login evidence", text):
        hints.append("prompt_injection")
    facts["hints"] = hints
    return facts


def ingest(job_id, path):
    seen = {}
    count = 0
    content_seen = False
    with db.connect() as c, path.open("r", encoding="utf-8-sig", errors="strict", newline="") as source:
        # readline is bounded so a malicious single line cannot allocate the upload limit repeatedly.
        while raw := source.readline(MAX_ENTRY_BYTES + 1):
            count += 1
            if len(raw.encode("utf-8")) > MAX_ENTRY_BYTES:
                raise ValueError("A log line exceeds 512 KiB. Split oversized records before uploading.")
            if count > MAX_LINES:
                raise ValueError("Upload has more than 100,000 lines.")
            raw = raw.rstrip("\r\n")
            content_seen = content_seen or bool(raw.strip())
            facts = classify(raw)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            canonical = seen.setdefault(digest, count)
            c.execute("INSERT INTO entries VALUES (?,?,?,?,?)", (job_id, count, raw, json.dumps(facts, separators=(",", ":")), canonical))
    if not count or not content_seen:
        raise ValueError("The uploaded file is empty.")
    db.update_job(job_id, lines=count)
    return count


def entries(job_id):
    with db.connect() as c:
        for row in c.execute("SELECT * FROM entries WHERE job_id=? ORDER BY line", (job_id,)):
            yield {**dict(row), "facts": json.loads(row["facts"])}
