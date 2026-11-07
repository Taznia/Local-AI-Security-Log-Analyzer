import asyncio
import json
import mimetypes
import queue
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path, PureWindowsPath
from urllib.parse import urlparse
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import db
from .config import DATA, ROOT, MAX_UPLOAD
from .ingest import ingest
from .llm import LocalModel
from .pipeline import Engine

# Windows MIME registrations are inconsistent for local font files. Use the
# explicit web font type so preload + nosniff work in Chromium and Firefox.
mimetypes.add_type("font/ttf", ".ttf")

model = LocalModel()
engine = None


@asynccontextmanager
async def lifespan(app):
    global engine
    db.initialize()
    engine = Engine(model)
    def start_model():
        try:
            model.start()
        except Exception as exc:
            model.ready = False
            model.error = f"Model startup failed: {type(exc).__name__}: {str(exc)[:300]}"
    starter = threading.Thread(target=start_model, name="model-startup", daemon=True)
    starter.start()
    yield
    engine.close()
    model.close()


app = FastAPI(title="Tracewatch", version="1.0.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])


@app.middleware("http")
async def local_security(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin writes are disabled"}, status_code=403)
        length = request.headers.get("content-length")
        try:
            if length and int(length) > MAX_UPLOAD + 64 * 1024:
                return JSONResponse({"detail": "File is too large. Maximum upload is 8 MiB."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ready" if model.ready else ("error" if model.error else "loading"), "model": model.name,
            "model_error": model.error, "context": 2048, "threads": 2, "slots": 1,
            "rss_bytes": model.rss(), "queue_size": engine.queue.qsize() if engine else 0, "offline": True}


@app.get("/api/jobs")
def list_jobs():
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT id,filename,status,phase,created,updated,lines,completed,total,elapsed,peak_rss FROM jobs ORDER BY created DESC LIMIT 30")]


@app.post("/api/jobs", status_code=201)
async def upload(file: UploadFile = File(...)):
    filename = PureWindowsPath(file.filename or "").name
    if Path(filename).suffix.lower() not in (".log", ".txt"):
        raise HTTPException(400, "Choose a .log or .txt file")
    with db.connect() as c:
        count = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    if count >= 100:
        raise HTTPException(409, "100 saved analyses reached. Archive the runtime folder before adding more.")
    job_id = uuid.uuid4().hex
    folder = DATA / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{job_id}.log"
    size = 0
    try:
        with path.open("wb") as target:
            while block := await file.read(65536):
                size += len(block)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "File is too large. Maximum upload is 8 MiB.")
                if b"\x00" in block:
                    raise HTTPException(400, "Binary files are not supported. Use UTF-8 plain text.")
                target.write(block)
        if not size:
            raise HTTPException(400, "The uploaded file is empty")
        with db.connect() as c:
            c.execute("INSERT INTO jobs(id,filename,status,created,updated,bytes) VALUES(?,?,?,?,?,?)", (job_id, filename[:200], "uploaded", db.now(), db.now(), size))
        try:
            await asyncio.to_thread(ingest, job_id, path)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return db.get_job(job_id)
    except Exception:
        path.unlink(missing_ok=True)
        with db.connect() as c:
            c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        raise
    finally:
        await file.close()


def require_job(job_id):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Analysis not found")
    return job


@app.post("/api/jobs/{job_id}/start")
def start(job_id: str):
    job = require_job(job_id)
    if job["status"] == "completed":
        raise HTTPException(409, "Analysis already completed")
    if not model.ready:
        raise HTTPException(503, model.error or "Model is still loading; try again shortly")
    try:
        engine.submit(job_id)
    except (ValueError, queue.Full) as exc:
        raise HTTPException(409, str(exc) or "Queue is full; try again after a job finishes") from exc
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = require_job(job_id)
    with db.connect() as c:
        job["chunks"] = [dict(r) for r in c.execute("SELECT idx,lines,status,prompt_tokens,output_tokens,duration,attempts,memory FROM chunks WHERE job_id=? ORDER BY idx", (job_id,))]
        for chunk in job["chunks"]:
            chunk["lines"] = json.loads(chunk["lines"])
        job["reviews_done"] = c.execute("SELECT COUNT(*) FROM reviews WHERE job_id=? AND status='done'", (job_id,)).fetchone()[0]
        job["call_failures"] = c.execute("SELECT COUNT(*) FROM calls WHERE job_id=? AND status='failed'", (job_id,)).fetchone()[0]
    return job


@app.get("/api/jobs/{job_id}/evidence")
def evidence(job_id: str, start: int = 1, limit: int = 50):
    require_job(job_id)
    if start < 1 or not 1 <= limit <= 100:
        raise HTTPException(400, "start must be positive; limit must be 1–100")
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT line,raw,canonical FROM entries WHERE job_id=? AND line>=? ORDER BY line LIMIT ?", (job_id, start, limit))]


@app.get("/api/jobs/{job_id}/audit")
def audit(job_id: str):
    require_job(job_id)
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT stage,unit,attempt,prompt_tokens,output_budget,output_tokens,duration,status,error,created FROM calls WHERE job_id=? ORDER BY id", (job_id,))]


@app.get("/api/jobs/{job_id}/report")
def download_report(job_id: str):
    job = require_job(job_id)
    if not job["report"]:
        raise HTTPException(409, "No report yet; completed chunk replies are saved")
    return Response(json.dumps({"filename": job["filename"], "analysis": job["report"], "runtime": {"elapsed_seconds": job["elapsed"], "peak_rss_bytes": job["peak_rss"]}}, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="tracewatch-{job_id[:8]}.json"'})


app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
