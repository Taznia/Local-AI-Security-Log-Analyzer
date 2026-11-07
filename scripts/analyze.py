"""Optional CLI client for the same local web API; never performs inference itself."""
import argparse
import json
import time
from pathlib import Path
import httpx


def main():
    parser = argparse.ArgumentParser(description="Upload and analyze a log through local Tracewatch")
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--resume", metavar="JOB_ID")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.resume and not args.file:
        parser.error("provide a log file or --resume JOB_ID")
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=60) as client:
        for _ in range(120):
            response = client.get("/api/health")
            response.raise_for_status()
            health = response.json()
            if health["status"] == "ready":
                break
            if health["status"] == "error":
                raise SystemExit(health["model_error"])
            time.sleep(1)
        else:
            raise SystemExit("Model is still loading; inspect the app health page")
        if args.resume:
            job_id = args.resume
        else:
            with args.file.open("rb") as source:
                response = client.post("/api/jobs", files={"file": (args.file.name, source, "text/plain")})
            response.raise_for_status()
            job_id = response.json()["id"]
        response = client.post(f"/api/jobs/{job_id}/start")
        response.raise_for_status()
        print(f"Analysis {job_id}", flush=True)
        previous = None
        while True:
            response = client.get(f"/api/jobs/{job_id}")
            response.raise_for_status()
            job = response.json()
            current = (job["status"], job["phase"], job["completed"], job["total"], job["reviews_done"])
            if current != previous:
                print(f"{job['status']} / {job['phase']}: {job['completed']}/{job['total']} chunks, {job['reviews_done']} reviewed cases, peak {job['peak_rss']/1048576:.0f} MiB", flush=True)
                previous = current
            if job["status"] == "completed":
                response = client.get(f"/api/jobs/{job_id}/report")
                response.raise_for_status()
                report = response.json()
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                    print(f"Saved {args.output}", flush=True)
                print(json.dumps(report["analysis"]["summary"], indent=2), flush=True)
                return
            if job["status"] in ("failed", "interrupted"):
                raise SystemExit(f"{job['error']}\nResume with --resume {job_id}")
            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as exc:
        raise SystemExit(f"Local API request failed: {exc}\nStart/restart Tracewatch, then use --resume with the analysis ID printed above.") from None
