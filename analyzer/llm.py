import json
import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
import httpx
import psutil
from . import db
from .config import ROOT, DATA, MODEL, MODEL_URL, CONTEXT, GUARD, MEMORY_GUARD


class ModelError(RuntimeError):
    pass


def parse_json(text):
    """Accept a complete fenced/wrapped object; never salvage truncated JSON silently."""
    text = text.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise ModelError("Model reply did not contain JSON")
        try:
            value, end = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            raise ModelError("Model returned incomplete or broken JSON") from exc
    if not isinstance(value, dict):
        raise ModelError("Model reply must be a JSON object")
    return value


class LocalModel:
    def __init__(self):
        parsed = urlparse(MODEL_URL)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.username or parsed.password:
            raise ValueError("Only a loopback HTTP model endpoint is allowed")
        self.client = httpx.Client(base_url=MODEL_URL, timeout=httpx.Timeout(180, connect=3), trust_env=False)
        self.process = None
        self.log = None
        self.lock = threading.Lock()
        self.ready = False
        self.error = None
        self.name = MODEL.name
        self.fingerprint = MODEL.name
        self.peak_rss = 0
        self.monitor_stop = threading.Event()
        self.monitor = None

    def start(self):
        try:
            health = self.client.get("/health")
            if health.status_code == 200:
                self.error = "The model port is already occupied. Stop the other llama-server before starting Tracewatch."
                return
        except httpx.HTTPError:
            pass
        binaries = list((ROOT / "bin").rglob("llama-server.exe" if os.name == "nt" else "llama-server"))
        if not MODEL.exists() or not binaries:
            self.error = "Local model is not installed. Run python scripts/setup.py, then restart."
            return
        DATA.mkdir(parents=True, exist_ok=True)
        self.log = (DATA / "model-server.log").open("ab")
        args = [str(binaries[0]), "-m", str(MODEL), "--host", "127.0.0.1", "--port", str(urlparse(MODEL_URL).port or 8091),
                "-c", str(CONTEXT), "-np", "1", "-t", "2", "-tb", "2", "-ngl", "0", "-b", "128", "-ub", "64",
                "--jinja", "--reasoning-budget", "0", "--no-context-shift", "--no-webui", "--slots"]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT, creationflags=flags, cwd=str(ROOT))
        for _ in range(120):
            if self.process.poll() is not None:
                self.error = "Local model process exited. See runtime/model-server.log."
                return
            try:
                if self.client.get("/health").status_code == 200:
                    self._verify()
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.5)
        self.error = "Model startup timed out. See runtime/model-server.log."

    def _verify(self):
        props = self.client.get("/props").json()
        settings = props.get("default_generation_settings", {})
        size = settings.get("n_ctx", props.get("n_ctx", CONTEXT))
        if size > CONTEXT:
            raise ModelError("Model server must use a context of at most 2048 tokens")
        with MODEL.open("rb") as source:
            self.fingerprint = hashlib.file_digest(source, "sha256").hexdigest()
        self.ready = True
        self.error = None
        self.monitor = threading.Thread(target=self.sample_memory, name="memory-monitor", daemon=True)
        self.monitor.start()

    def sample_memory(self):
        while not self.monitor_stop.wait(.25):
            try:
                self.peak_rss = max(self.peak_rss, self.rss())
                if self.peak_rss > MEMORY_GUARD:
                    self.error = "Memory guard stopped the model above 3 GiB. Completed work is saved; restart before resuming."
                    self.ready = False
                    if self.process and self.process.poll() is None:
                        self.process.terminate()
                    return
            except psutil.Error:
                pass

    def template(self, system, user):
        r = self.client.post("/apply-template", json={"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "chat_template_kwargs": {"enable_thinking": False}})
        r.raise_for_status()
        prompt = r.json()["prompt"]
        # The pinned Qwen model's template can open a think block. Close it explicitly
        # before schema-constrained generation; these tokens are counted too.
        if prompt.rstrip().endswith("<think>"):
            prompt += "\n</think>\n\n"
        return prompt

    def tokens(self, text):
        r = self.client.post("/tokenize", json={"content": text, "add_special": True, "parse_special": True})
        r.raise_for_status()
        return r.json()["tokens"]

    def count(self, text):
        return len(self.tokens(text))

    def fits(self, system, user, output):
        return self.count(self.template(system, user)) + output + GUARD <= CONTEXT

    def rss(self):
        total = psutil.Process().memory_info().rss
        if self.process and self.process.poll() is None:
            try:
                total += psutil.Process(self.process.pid).memory_info().rss
            except psutil.Error:
                pass
        return total

    def call(self, job_id, stage, unit, system, user, schema, output, validate):
        if not self.ready:
            raise ModelError(self.error or "Local model not ready")
        prompt = self.template(system, user)
        tokens = self.tokens(prompt)
        if len(tokens) + output + GUARD > CONTEXT:
            raise ModelError(f"Token budget exceeded: {len(tokens)} + {output} + {GUARD} > {CONTEXT}")
        last = None
        for attempt in range(1, 4):
            start = time.monotonic()
            response = None
            result = None
            out_count = None
            status = "failed"
            error = None
            try:
                # One queue worker, one request lock, and one llama slot.
                with self.lock:
                    r = self.client.post("/completion", json={"prompt": tokens, "n_predict": output, "temperature": 0,
                        "seed": 42, "json_schema": schema, "cache_prompt": True, "stream": False, "repeat_penalty": 1.12,
                        "stop": ["<|im_end|>", "<|endoftext|>"], "id_slot": 0})
                    r.raise_for_status()
                    result = r.json()
                response = result.get("content", "")
                out_count = result.get("tokens_predicted")
                if out_count is None:
                    out_count = self.count(response)
                if result.get("stopped_limit") or result.get("truncated"):
                    raise ModelError("Model reply reached a token limit; no incomplete findings were saved")
                parsed = parse_json(response)
                validate(parsed)
                status = "ok"
                return parsed, {"prompt_tokens": len(tokens), "output_tokens": out_count, "duration": time.monotonic() - start, "attempts": attempt}
            except (httpx.HTTPError, ValueError, KeyError, TypeError, ModelError) as exc:
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
                last = error
                if self.process and self.process.poll() is not None:
                    self.ready = False
                    self.error = self.error or "The local model stopped. Restart Tracewatch, then resume the saved analysis."
                if isinstance(exc, httpx.TimeoutException):
                    # Do not dispatch a retry until the previous generation has released
                    # the only slot. A timeout may leave the server completing its call.
                    self.wait_idle()
            finally:
                with db.connect() as c:
                    c.execute("INSERT INTO calls(job_id,stage,unit,attempt,prompt_tokens,output_budget,output_tokens,duration,status,error,response,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                              (job_id, stage, str(unit), attempt, len(tokens), output, out_count, time.monotonic()-start, status, error, response, db.now()))
            time.sleep(.5 * attempt)
        raise ModelError(f"Call failed after 3 attempts: {last}")

    def wait_idle(self):
        for _ in range(60):
            try:
                slots = self.client.get("/slots").json()
                if isinstance(slots, list) and all(not x.get("is_processing", False) for x in slots):
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(1)
        raise ModelError("Timed-out model did not become idle; resume after restarting it")

    def close(self):
        self.monitor_stop.set()
        if self.monitor:
            self.monitor.join(timeout=1)
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.client.close()
        if self.log:
            self.log.close()
