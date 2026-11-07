import hashlib
import copy
import json
import queue
import threading
import time
from datetime import datetime
from collections import Counter, defaultdict
from . import db
from .config import CONTEXT, GUARD, MAP_OUTPUT, REDUCE_OUTPUT, MODEL, MEMORY_GUARD
from .ingest import entries, KINDS
from .llm import ModelError
from .prompts import MAP_SYSTEM, MAP_SCHEMA, REDUCE_SYSTEM, REVIEW_SCHEMA, VERSION

TITLES = {"authentication": "Repeated login attempts", "sqli": "SQL injection attempts", "scanning": "Endpoint reconnaissance", "unauthorized": "Unauthorized access attempts", "rate": "Unusual request rate", "prompt_injection": "Instruction injection in log content", "suspicious": "Suspicious activity requiring review", "export": "Unusual data export"}
SEVERITY = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}


def safe_data(text):
    return text.replace("<|", "\\u003c|").replace("<think>", "\\u003cthink>").replace("</think>", "\\u003c/think>")


def map_user(content, memory="None"):
    return "SAVED FACTS (untrusted, for correlation only):\n" + safe_data(memory) + "\nLOG DATA; L<number> is the original physical line:\n" + safe_data(content) + "\nEND LOG DATA. Emit signals for the current log lines only."


def validate_map(obj, allowed):
    if set(obj) != {"signals"} or not isinstance(obj["signals"], list) or len(obj["signals"]) > 12:
        raise ModelError("Invalid signals schema")
    for signal in obj["signals"]:
        if set(signal) != {"kind", "lines"} or signal["kind"] not in KINDS:
            raise ModelError("Invalid signal kind")
        if not isinstance(signal["lines"], list) or not signal["lines"] or len(signal["lines"]) > 20:
            raise ModelError("Invalid signal evidence")
        if any(type(n) is not int or n not in allowed for n in signal["lines"]):
            raise ModelError("Model cited a line outside the current chunk")


def validate_review(obj, allowed):
    if set(obj) != {"report", "severity", "reason", "action", "lines"}:
        raise ModelError("Invalid review fields")
    if type(obj["report"]) is not bool or obj["severity"] not in SEVERITY:
        raise ModelError("Invalid review verdict or severity")
    if not isinstance(obj["reason"], str) or not isinstance(obj["action"], str) or len(obj["reason"]) > 600 or len(obj["action"]) > 400:
        raise ModelError("Invalid review explanation")
    if not isinstance(obj["lines"], list) or len(obj["lines"]) > 16 or any(type(n) is not int or n not in allowed for n in obj["lines"]):
        raise ModelError("Review evidence is not in the supplied dossier")
    if obj["report"] and (not obj["lines"] or not obj["reason"].strip() or not obj["action"].strip()):
        raise ModelError("Reported finding has no evidence, reason, or action")


def grounded_schema(schema, allowed, map_stage=False):
    schema = copy.deepcopy(schema)
    target = schema["properties"]["signals"]["items"]["properties"]["lines"] if map_stage else schema["properties"]["lines"]
    target["items"] = {"type": "integer", "enum": sorted(allowed)}
    return schema


def plan_chunks(job_id, model):
    with db.connect() as c:
        job = c.execute("SELECT total FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job[0] and c.execute("SELECT COUNT(*) FROM chunks WHERE job_id=?", (job_id,)).fetchone()[0] == job[0]:
            return
        c.execute("DELETE FROM chunks WHERE job_id=?", (job_id,))
    pending = []
    idx = 0
    split = False

    def fits(content):
        # A reserved 160 actual tokenizer tokens will be available for carry-forward facts.
        return model.count(model.template(MAP_SYSTEM, map_user(content))) + MAP_OUTPUT + GUARD + 160 <= CONTEXT

    def save():
        nonlocal idx, pending
        if not pending:
            return
        with db.connect() as c:
            c.execute("INSERT INTO chunks(job_id,idx,content,lines) VALUES(?,?,?,?)", (job_id, idx, "\n".join(x[1] for x in pending), json.dumps(sorted(set(x[0] for x in pending)))))
        idx += 1
        pending = []

    # Keep physical line references even for a stack trace continuation or oversized entry.
    for entry in entries(job_id):
        n, raw = entry["line"], entry["raw"]
        rendered = f"L{n}: {raw}"
        trial = "\n".join([x[1] for x in pending] + [rendered])
        if fits(trial):
            pending.append((n, rendered))
            continue
        save()
        if fits(rendered):
            pending.append((n, rendered))
            continue
        split = True
        offset = 0
        while offset < len(raw):
            low, high = 1, min(len(raw) - offset, 8192)
            best = 0
            while low <= high:
                middle = (low + high) // 2
                text = f"L{n} [fragment chars {offset}:{offset+middle}]: {raw[offset:offset+middle]}"
                if fits(text):
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            if not best:
                raise ModelError("Prompt overhead leaves no room for log content")
            pending = [(n, f"L{n} [fragment chars {offset}:{offset+best}]: {raw[offset:offset+best]}")]
            save()
            offset += best
    save()
    if split:
        db.warn(job_id, "An oversized entry was split into character-labelled fragments; original line numbers are preserved.")
    db.update_job(job_id, total=idx)


def memory_for(job_id, before, model):
    facts = defaultdict(lambda: {"fail": 0, "ok": 0, "first": None, "last": None})
    for entry in entries(job_id):
        if entry["line"] >= before:
            break
        f = entry["facts"]
        if "auth_failure" in f["hints"] or "auth_success" in f["hints"]:
            key = f["entity"] + "/" + f["account"]
            info = facts[key]
            info["fail"] += int("auth_failure" in f["hints"] and entry["canonical"] == entry["line"])
            info["ok"] += int("auth_success" in f["hints"] and entry["canonical"] == entry["line"])
            info["first"] = info["first"] or entry["line"]
            info["last"] = entry["line"]
    ranked = sorted(facts.items(), key=lambda x: x[1]["fail"], reverse=True)
    memory = []
    for key, value in ranked:
        item = f"{key}: failures={value['fail']},successes={value['ok']},lines={value['first']}-{value['last']}"
        trial = "; ".join(memory + [item])
        if model.count(trial) > 145:
            break
        memory.append(item)
    return "; ".join(memory) or "None"


def build_cases(job_id):
    signals = defaultdict(set)
    with db.connect() as c:
        for row in c.execute("SELECT result FROM chunks WHERE job_id=? AND status='done' ORDER BY idx", (job_id,)):
            for signal in json.loads(row[0])["signals"]:
                for n in signal["lines"]:
                    signals[n].add(signal["kind"])
    groups = {}
    for entry in entries(job_id):
        f = entry["facts"]
        kinds = set(f["hints"]) | signals[entry["line"]]
        # Success/export facts are carried into authentication cases, never treated
        # as proof of an attack on their own. Candidate extraction is a recall aid.
        for kind in kinds:
            family = "authentication" if kind in ("auth_failure", "auth_success", "export") else kind
            scope = "*" if family in ("scanning", "unauthorized") else f["scope"]
            account = f["account"] if family == "authentication" else ""
            key = json.dumps([family, f["entity"], scope, account], separators=(",", ":"))
            case = groups.setdefault(key, {"key": key, "family": family, "entity": f["entity"], "scope": scope, "account": account, "entries": {}, "kinds": defaultdict(set), "model_lines": set()})
            case["entries"][entry["line"]] = entry
            case["kinds"][kind].add(entry["line"])
            if kind in signals[entry["line"]]:
                case["model_lines"].add(entry["line"])
    # Join related authenticated activity through a demonstrated session, while
    # retaining IP and application scope so session IDs cannot bridge tenants.
    sessions = {}
    for case in groups.values():
        if case["family"] == "authentication":
            for entry in case["entries"].values():
                f = entry["facts"]
                if "auth_success" in f["hints"] and f.get("session_id"):
                    sessions[(f["entity"], f["scope"], str(f["session_id"]))] = case
    for entry in entries(job_id):
        f = entry["facts"]
        if not f.get("session_id"):
            continue
        case = sessions.get((f["entity"], f["scope"], str(f["session_id"])))
        if case and f.get("account", "") in ("", case["account"]):
            case["entries"][entry["line"]] = entry
            if "auth_success" not in f["hints"]:
                case["kinds"]["session_activity"].add(entry["line"])
    return sorted(groups.values(), key=lambda x: x["key"])


def timestamp(entry):
    f = entry["facts"]
    value = f.get("time", f.get("timestamp"))
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (ValueError, OverflowError):
            pass
    return None


def earlier(a, b):
    ta, tb = timestamp(a), timestamp(b)
    return ta < tb if ta is not None and tb is not None else a["line"] < b["line"]


def supported(case):
    """Conservative evidence gate independent of model confidence."""
    unique = {e["canonical"] for e in case["entries"].values()}
    family = case["family"]
    if family == "authentication":
        # A model can mistake "supplier request failed" for a login failure.
        # The brute-force threshold must count source-corroborated auth events.
        failures = {e["canonical"] for e in case["entries"].values() if "auth_failure" in e["facts"]["hints"]}
        return len(failures) >= 3
    if family == "scanning":
        paths = {str(e["facts"].get("path", "")).split("?", 1)[0] for e in case["entries"].values()} - {""}
        return len(unique) >= 3 and len(paths) >= 3
    if family == "rate":
        for e in case["entries"].values():
            f = e["facts"]
            try:
                if float(f.get("requests_in_window", 0)) / max(float(f.get("window_seconds", 1)), .001) >= 10:
                    return True
            except (ValueError, TypeError):
                pass
        denied = {e["canonical"] for e in case["entries"].values() if e["facts"].get("status") == 429}
        if len(denied) >= 3:
            return True
        times = sorted(t for e in case["entries"].values() if (t := timestamp(e)) is not None)
        # An explicit timestamped burst is needed; five events over hours are not a rate anomaly.
        return any(times[i+9] - times[i] <= 1 for i in range(max(0, len(times)-9)))
    if family in ("sqli", "unauthorized", "prompt_injection"):
        return any(family in e["facts"]["hints"] for e in case["entries"].values())
    return family == "suspicious" and len(unique) >= 2 and case["entity"] != "unknown"


def dossier(case, model):
    all_rows = list(case["entries"].values())
    chosen = set()
    for kind, lines in case["kinds"].items():
        canonical = sorted({case["entries"][n]["canonical"] for n in lines})
        chosen.update(canonical[:2] + canonical[-2:])
    selected = [e for e in all_rows if e["line"] in chosen]
    selected.sort(key=lambda e: e["line"])
    counts = {kind: len({case["entries"][n]["canonical"] for n in nums}) for kind, nums in case["kinds"].items()}
    summary = {"candidate": TITLES.get(case["family"], case["family"]), "entity": case["entity"], "scope": case["scope"], "account": case["account"], "unique_counts": counts,
               "total_unique_events": len({e["canonical"] for e in all_rows}), "duplicate_lines": [e["line"] for e in all_rows if e["line"] != e["canonical"]][:20], "first_line": min(case["entries"]), "last_line": max(case["entries"])}
    if case["family"] == "rate":
        counters = [(e["facts"].get("requests_in_window"), e["facts"].get("window_seconds")) for e in all_rows]
        counters = [(n, w) for n, w in counters if isinstance(n, (int, float)) and isinstance(w, (int, float)) and w > 0]
        if counters:
            n, w = max(counters, key=lambda x: x[0]/x[1])
            summary["peak_observed_counter"] = {"requests": n, "window_seconds": w, "note": "Overlapping rolling counters; do not sum them."}
    # Present compact source-derived records, with counts covering ALL events.
    # Representatives retain both early and late events for every signal type.
    keys = ("time", "timestamp", "app", "client_ip", "username", "session_id", "path", "status", "user_agent", "auth_result", "requests_in_window", "window_seconds", "response_bytes", "message", "submitted_text", "validation_error", "security_rule")
    string_limit = 700
    def render(rows):
        records = []
        for e in rows:
            f = e["facts"]
            item = {"line": e["line"]}
            truncated = []
            for k in keys:
                if k not in f:
                    continue
                value = f[k]
                if not isinstance(value, (str, int, float, bool, type(None))):
                    value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if isinstance(value, str) and len(value) > string_limit:
                    value = value[:string_limit]
                    truncated.append(k)
                item[k] = value
            if not any(k in f for k in ("path", "auth_result", "message")):
                item["text"] = e["raw"][:string_limit]
                if len(e["raw"]) > string_limit:
                    truncated.append("text")
            if truncated:
                item["shortened_fields_full_source_saved"] = truncated
            records.append(item)
        return safe_data(json.dumps({"summary": summary, "representative_evidence": records}, ensure_ascii=False, separators=(",", ":")))
    while selected and not model.fits(REDUCE_SYSTEM, render(selected), REDUCE_OUTPUT):
        # Prefer one earliest and one latest event for each type; remove redundant
        # second representatives before sacrificing a distinct event kind.
        removable = []
        for i, e in enumerate(selected):
            memberships = [nums for nums in case["kinds"].values() if e["line"] in nums]
            if all(sum(x["line"] in nums for x in selected) > 1 for nums in memberships):
                removable.append(i)
        if not removable:
            if string_limit > 80:
                string_limit = max(80, string_limit // 2)
                continue
            raise ModelError("A case contains more distinct evidence than the bounded review can hold")
        selected.pop(removable[len(removable)//2])
    if not selected:
        raise ModelError("No review evidence fits the token budget")
    return render(selected), {e["line"] for e in selected}


def finding_from(case, review, job_id):
    rows = sorted(case["entries"].values(), key=lambda x: x["line"])
    fail = [e for e in rows if "auth_failure" in e["facts"]["hints"]]
    successes = [e for e in rows if "auth_success" in e["facts"]["hints"]]
    later = [e for e in successes if len({f["canonical"] for f in fail if earlier(f, e)}) >= 3]
    title = TITLES[case["family"]]
    severity = review["severity"]
    if case["family"] == "authentication" and later:
        title = "Possible account compromise after repeated login failures"
    # No supplied response code or model assertion proves exploitation or DoS.
    # Avoid Critical without a separate, implemented impact-verification mechanism.
    if severity == "Critical":
        severity = "High"
    if case["family"] in ("scanning", "unauthorized", "rate", "prompt_injection") and SEVERITY[severity] > 2:
        severity = "Medium"
    evidence_nums = sorted(set(review["lines"]))
    # Include a chronology spine even if the model cites just the last event.
    if case["family"] == "authentication" and later:
        evidence_nums = sorted(set(evidence_nums + [fail[0]["line"], fail[-1]["line"], later[0]["line"]]))
    evidence = []
    for n in evidence_nums:
        e = case["entries"][n]
        evidence.append({"line": n, "text": e["raw"], "time": e["facts"].get("time", e["facts"].get("timestamp")), "canonical_line": e["canonical"]})
    chunk_ids = []
    with db.connect() as c:
        for row in c.execute("SELECT idx,lines FROM chunks WHERE job_id=?", (job_id,)):
            if set(json.loads(row["lines"])) & set(case["entries"]):
                chunk_ids.append(row["idx"] + 1)
    return {"id": hashlib.sha256(case["key"].encode()).hexdigest()[:12], "attack_type": title, "category": case["family"], "severity": severity,
            "entity": case["entity"], "account": case["account"], "scope": case["scope"], "reason": review["reason"], "suggested_action": review["action"],
            "evidence": evidence, "all_evidence_lines": sorted(case["entries"]), "unique_events": len({e["canonical"] for e in rows}),
            "duplicate_lines": [e["line"] for e in rows if e["line"] != e["canonical"]], "chunks": sorted(chunk_ids),
            "cross_chunk": len(chunk_ids) > 1, "status": "Possible compromise" if later else "Suspicious activity / attempt",
            "model_observed_lines": sorted(case["model_lines"]), "provenance": "Source-derived candidates + local model review + evidence gate"}


class Engine:
    def __init__(self, model):
        self.model = model
        self.queue = queue.Queue(maxsize=8)
        self.stopping = threading.Event()
        self.queued = set()
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self.run, name="analysis-queue", daemon=True)
        self.worker.start()

    def submit(self, job_id):
        with self.lock:
            if job_id in self.queued:
                raise ValueError("This analysis is already queued or running")
            if self.queue.full():
                raise queue.Full()
            self.queued.add(job_id)
            db.update_job(job_id, status="queued", error=None)
            self.queue.put_nowait(job_id)

    def run(self):
        while not self.stopping.is_set():
            try:
                job_id = self.queue.get(timeout=.5)
            except queue.Empty:
                continue
            try:
                self.analyze(job_id)
            except Exception as exc:
                db.update_job(job_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:700]}")
            finally:
                with self.lock:
                    self.queued.discard(job_id)
                self.queue.task_done()

    def analyze(self, job_id):
        start = time.monotonic()
        job = db.get_job(job_id)
        fingerprint = f"{self.model.fingerprint}:prompt-v{VERSION}:ctx{CONTEXT}"
        if job["model_fingerprint"] and job["model_fingerprint"] != fingerprint:
            raise ModelError("Model/prompt configuration changed. Start a new analysis to avoid mixing results.")
        db.update_job(job_id, status="running", phase="splitting", model_fingerprint=fingerprint)
        if not self.model.ready:
            raise ModelError(self.model.error or "Model not ready")
        plan_chunks(job_id, self.model)
        db.update_job(job_id, phase="analyzing")
        with db.connect() as c:
            indices = [r[0] for r in c.execute("SELECT idx FROM chunks WHERE job_id=? AND status!='done' ORDER BY idx", (job_id,))]
        for idx in indices:
            if self.stopping.is_set():
                db.update_job(job_id, status="interrupted")
                return
            with db.connect() as c:
                chunk = dict(c.execute("SELECT * FROM chunks WHERE job_id=? AND idx=?", (job_id, idx)).fetchone())
            allowed = set(json.loads(chunk["lines"]))
            memory = memory_for(job_id, min(allowed), self.model)
            user = map_user(chunk["content"], memory)
            if not self.model.fits(MAP_SYSTEM, user, MAP_OUTPUT):
                user = map_user(chunk["content"])
                memory = "None (bounded to preserve budget)"
            result, meta = self.model.call(job_id, "map", idx, MAP_SYSTEM, user, grounded_schema(MAP_SCHEMA, allowed, True), MAP_OUTPUT, lambda x: validate_map(x, allowed))
            with db.connect() as c:
                c.execute("UPDATE chunks SET status='done',result=?,memory=?,prompt_tokens=?,output_tokens=?,duration=?,attempts=? WHERE job_id=? AND idx=?",
                          (json.dumps(result), memory, meta["prompt_tokens"], meta["output_tokens"], meta["duration"], meta["attempts"], job_id, idx))
                complete = c.execute("SELECT COUNT(*) FROM chunks WHERE job_id=? AND status='done'", (job_id,)).fetchone()[0]
            rss = max(self.model.rss(), self.model.peak_rss)
            db.update_job(job_id, completed=complete, peak_rss=max(rss, db.get_job(job_id)["peak_rss"]), elapsed=job["elapsed"] + time.monotonic() - start)
            if rss > MEMORY_GUARD:
                raise ModelError("Memory guard stopped analysis above 3 GiB; completed chunks are saved")
        db.update_job(job_id, phase="correlating")
        cases = build_cases(job_id)
        findings = []
        dismissed = []
        for case in cases:
            if not supported(case):
                dismissed.append({"category": case["family"], "entity": case["entity"], "reason": "Insufficient independent supporting evidence", "lines": sorted(case["entries"])})
                continue
            user, allowed = dossier(case, self.model)
            with db.connect() as c:
                prior = c.execute("SELECT * FROM reviews WHERE job_id=? AND key=? AND status='done'", (job_id, case["key"])).fetchone()
            if prior:
                result = json.loads(prior["result"])
            else:
                result, meta = self.model.call(job_id, "reduce", case["key"], REDUCE_SYSTEM, user, grounded_schema(REVIEW_SCHEMA, allowed), REDUCE_OUTPUT, lambda x: validate_review(x, allowed))
                with db.connect() as c:
                    c.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?,?,?,?)", (job_id, case["key"], "done", json.dumps(result), meta["prompt_tokens"], meta["output_tokens"], meta["duration"], meta["attempts"]))
            if result["report"]:
                findings.append(finding_from(case, result, job_id))
            else:
                dismissed.append({"category": case["family"], "entity": case["entity"], "reason": result["reason"], "lines": sorted(case["entries"])})
            findings.sort(key=lambda f: (-SEVERITY[f["severity"]], f["entity"], f["category"]))
            report = self.report(job_id, findings, dismissed, partial=True)
            db.update_job(job_id, report=json.dumps(report), elapsed=job["elapsed"] + time.monotonic() - start, peak_rss=max(self.model.rss(), self.model.peak_rss, db.get_job(job_id)["peak_rss"]))
        report = self.report(job_id, findings, dismissed, partial=False)
        db.update_job(job_id, status="completed", phase="complete", report=json.dumps(report), elapsed=job["elapsed"] + time.monotonic() - start)

    def report(self, job_id, findings, dismissed, partial):
        with db.connect() as c:
            counts = c.execute("SELECT COUNT(*),COUNT(DISTINCT canonical) FROM entries WHERE job_id=?", (job_id,)).fetchone()
            audit = c.execute("SELECT COUNT(*),SUM(prompt_tokens),SUM(output_tokens),MAX(prompt_tokens+output_budget) FROM calls WHERE job_id=? AND status='ok'", (job_id,)).fetchone()
        return {"version": 1, "partial": partial, "generated": db.now(), "model": self.model.name, "prompt_version": VERSION, "context_limit": CONTEXT,
                "summary": {"findings": len(findings), "severity_counts": dict(Counter(f["severity"] for f in findings)), "cross_chunk_findings": sum(f["cross_chunk"] for f in findings), "lines": counts[0], "unique_events": counts[1], "duplicate_lines": counts[0]-counts[1], "successful_model_calls": audit[0], "input_tokens": audit[1] or 0, "output_tokens": audit[2] or 0, "max_call_budget": audit[3] or 0},
                "findings": findings, "dismissed_candidates": dismissed,
                "caveats": ["Findings are evidence-backed suspicions requiring human review; logs alone may not establish compromise or intent.", "Candidate extraction uses general patterns; every reported finding requires a successful local model review.", "Critical severity is capped at High because this app does not independently verify impact.", "Representative evidence is model-reviewed; all linked line references remain available in the report."]}

    def close(self):
        self.stopping.set()
        self.worker.join(timeout=2)
