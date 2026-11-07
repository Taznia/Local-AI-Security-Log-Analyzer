# Tracewatch — Local AI Security Log Analyzer

A local web app that follows security activity across an entire server log. Upload a `.log` or `.txt`, watch sequential local-model analysis, inspect a merged report, and open every finding's original evidence lines.

**Runtime inference is local only. No cloud AI APIs, telemetry, CDN assets, or external fonts.** Setup downloads the model, CPU runtime, and Python packages; subsequent analysis works offline.

## Quick start (Windows)

Requires 64-bit Windows, Python 3.12+ and approximately 2 GB free disk for the runtime/model (more for the virtual environment). The tested interpreter is Python 3.13.4.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/setup.py
.\.venv\Scripts\python.exe run.py
```

Open **http://127.0.0.1:8000**, select your assessment download or another UTF-8 `.log` / `.txt` file, then click **Start analysis**. Logs and reports containing raw evidence are intentionally excluded from Git; place the supplied assessment file beside `run.py` if using the CLI example below. Keep one app instance running. The app starts and stops its own model server. Never use multiple Uvicorn workers: each would load another model.

Setup downloads are resumable after a connection reset. The model's exact Hugging Face revision, SHA-256, byte size and llama.cpp release are recorded in `setup-manifest.json`. Model weights and executables are ignored by Git and are not uploaded to this repository.

## Linux / constrained server

Use Python 3.12+, install the requirements in a virtual environment, then run `python scripts/setup.py` and `python run.py`. The setup helper selects the matching official CPU runtime archive. If no compatible archive exists for your distribution, build the pinned llama.cpp release and place `llama-server` and its shared libraries under `bin/`:

```bash
git clone --branch b12182 --depth 1 https://github.com/ggml-org/llama.cpp.git /tmp/tracewatch-llama
cmake -S /tmp/tracewatch-llama -B /tmp/tracewatch-llama/build -DGGML_CUDA=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF
cmake --build /tmp/tracewatch-llama/build --config Release -j2 --target llama-server
mkdir -p bin
cp /tmp/tracewatch-llama/build/bin/* bin/
```

Use a CPU-compatible build. The application binds both ports to loopback and is intended for one trusted local user. For remote access, use an SSH tunnel to port 8000 instead of exposing it directly. It does not implement multi-user authentication.

## Model and resource choices

- **Qwen3.5 2B, Q4_K_M GGUF**, text only, distributed by [Unsloth](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF); no vision projector is loaded.
- **llama.cpp b12182**, native CPU server, `-ngl 0`, two generation threads, two prompt-processing threads, one slot, 2,048-token context, small batch/ubatch sizes, context shifting disabled.
- **FastAPI + SQLite + vanilla HTML/CSS/JavaScript**. No Node build or browser package is required to run the app.
- Exactly one queue worker invokes the model. Upload data is read in 64 KiB blocks; SQLite cursors stream entries. The entire log is never copied into a model prompt.
- Original files, indexed entries, chunk payloads, responses and reports live on disk in `runtime/`. This intentionally spends disk space to keep resident memory small and make recovery auditable.
- The dashboard shows backend + owned model process RSS. A memory sampler runs every 250 ms and terminates the owned model above 3 GiB. This is a guard, not an OS memory reservation; leave headroom for the operating system and use container/cgroup limits when deploying on a shared server.
- Model switching: set `TRACEWATCH_MODEL` to another compatible local GGUF before startup. Start a fresh analysis after changing model or prompts. Different chat templates and capabilities need verification; switching is not a promise that every GGUF performs equally well.

See `artifacts/VALIDATION.md` for measured execution and test results from the supplied file.

## How the 2,048-token budget is enforced

The application asks the **running model's tokenizer** to count the fully rendered model chat template, including special tokens and any forced closing reasoning tags. It sends those exact token IDs to `/completion`, avoiding a second, different chat-template expansion.

For map calls:

```text
rendered system + user + original numbered log entries + saved facts
+ 384 reserved output tokens + 24 safety tokens <= 2,048
```

During planning an additional 160 tokens are reserved for carry-forward memory. Actual memory is counted and bounded before each call. If necessary it is omitted for that call, while the full persistent ledger remains available to the final correlation stage. Review calls reserve **512 output tokens** and the same 24-token safety margin. The final fitted prompt is checked again before every model call.

Complete physical log lines are packed greedily without overlap. An entry that cannot fit on its own is split by a binary search over character boundaries using actual tokenizer counts; every fragment retains the original physical line and character range. A warning records this. Multi-line stack traces retain each physical line number but can cross chunk boundaries; automatic grouping of an arbitrary multi-line log format is a known limitation. No entries are silently truncated during ingestion or chunking.

## Whole-file correlation

1. Stream UTF-8 input into an indexed SQLite ledger with original 1-based physical line numbers. Parse JSON lines and common access/auth text formats. Keep raw text for evidence.
2. Exact duplicate records share a canonical event reference. Both physical lines remain visible, but duplicates do not inflate event counts or brute-force thresholds.
3. Tokenize and plan chunks. Read each chunk with the local model and persist its structured signals immediately. Carry bounded, source-derived authentication facts into later calls.
4. Build whole-file candidate cases from **both** model signals and general source-derived patterns. Patterns aid recall when a small model misses an individual signal; they are not final attack verdicts.
5. Group authentication by IP, application/host and account. Group reconnaissance by IP across applications. Other cases retain application scope. Early failures, later successes and exports can therefore meet even when many chunks apart.
6. Give the local model a bounded dossier for each case: deduplicated full-file counts plus representative early/late source records for each signal kind. Every reported case requires a successful local-model review and an independent evidence gate. A rules-only final finding is never emitted.
7. Merge by case key into one report, with severity, actor/account, original evidence, reason, action, all linked lines and contributing chunk IDs. This is not a concatenation of per-chunk reports.

Evidence gates require at least three independent login failures for a brute-force candidate and multiple sensitive-path probes for scanning. The app does not turn a database timeout into SQL injection. HTTP 400/403 are described as attempts/denials rather than successful exploitation; Critical is capped at High because the app does not independently verify impact. Human review remains necessary.

## Reliability and data handling

- `.log` / `.txt`, UTF-8, maximum 8 MiB, 100,000 lines and 512 KiB per physical line. Reject empty, binary/NUL and invalid UTF-8 uploads. Failed uploads leave no database job or retained uploaded file.
- No shell execution, tool use, or command dispatch is available to the model. Log content stays in the user/data role. Chat control-token lookalikes are escaped in prompts. Frontend evidence is HTML-escaped.
- Schema-constrained JSON generation plus independent semantic validation. Fenced complete JSON can be read; truncated JSON, invalid severities and invented evidence lines trigger a retry rather than a crash or fabricated finding.
- Three bounded attempts per failed call; each attempt is audited. After a timeout, wait for the model slot to become idle before retrying. Completed chunks and reviews remain saved on disk.
- Restart marks unfinished jobs interrupted. **Resume from checkpoint** skips saved completed chunk calls and case reviews. A partially generated chunk plan is discarded and rebuilt safely.
- The final report may be partial while correlation is running or after a later failure; the UI labels it. Raw per-call progress and evidence remain available even before a report exists.
- Same-origin mutation checks, trusted loopback hosts, restrictive content security policy and no external assets. The owned model server uses an automatically generated, in-memory API key and a restricted CORS origin. No external API key is needed. Do not expose either local port to untrusted networks.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Model readiness, queue and RSS |
| `POST /api/jobs` | Multipart `file` upload, validation and indexing |
| `POST /api/jobs/{id}/start` | Start or resume an analysis |
| `GET /api/jobs` | Saved analyses |
| `GET /api/jobs/{id}` | Progress, saved report and chunk budget metadata |
| `GET /api/jobs/{id}/evidence?start=1&limit=50` | Bounded original source lines |
| `GET /api/jobs/{id}/audit` | Every model attempt, budget, timing and error |
| `GET /api/jobs/{id}/report` | Download merged JSON report |

## Interface and offline assets

The interface uses locally bundled **Open Sans**, a white and pale-gray canvas, engineering-blue actions, and a restrained **Silk Waves** background from [Beautiful Backgrounds](https://github.com/Niekes/beautiful-backgrounds). The selected source is pinned and vendored; full font/background/dependency notices are in `THIRD_PARTY_NOTICES.md`.

The background uses 2D canvas, is capped at 12 FPS and 1,280 backing-canvas pixels in width, and pauses when offscreen, when the tab is hidden, or during active analysis. The system reduced-motion preference shows a static wave rendering. Fonts and scripts are served from this app; no CDN is contacted at runtime.

The generated background bundle is committed. **Node is not required to run Tracewatch.** For optional interface development and DOM checks:

```text
npm ci
npm run build:background
npm run test:ui
```

The DOM tests check report filtering, search, safely escaped evidence, dialogs, history, actual background registration, the frame cap, reduced motion, and pause/resume behavior. They do not replace a visual browser test.

## Optional CLI client

With the app running, the same API can be exercised without a browser:

```powershell
.\.venv\Scripts\python.exe scripts/analyze.py Cultive8_Workspace_Agrigen_Server.log --output artifacts/supplied-log-report.json
```

Use `--resume JOB_ID` after restarting the app to continue an interrupted run. This client sends data only to the local backend.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests cover invalid uploads, same-origin checks, evidence grounding, prompt-injection preservation, encoded payloads, duplicate events, cross-chunk association, account/application isolation, malformed replies, and lossless oversized-line splitting under a deliberately conservative test tokenizer. These tests do not claim to measure model accuracy; the separate full-file real-model run does that.

## Known limits

- Small quantized LMs can miss signals, misclassify events, or phrase a reason too strongly. Candidate gates and cautious severity limits reduce these risks but cannot eliminate them.
- A bounded dossier uses representative evidence rather than every raw event in one call. All source lines remain available for human inspection. Very long fields in review dossiers are explicitly labelled as shortened; their complete raw evidence and map-stage fragments remain saved. Unusual formats, IPv6 in unstructured text, session-only identity changes and distributed attacks across unrelated IPs need stronger parsers/correlation.
- The application does not learn your normal traffic baseline. Rate findings require explicit counters/429 evidence or supported timestamped bursts. Authorized scanners/automation may still require manual dismissal.
- No streaming cancellation, multi-user login, archive rotation, or background job scheduling. Failed jobs are resumable, not automatically resumed without an operator.
- Raw logs and analysis are retained in plaintext on local disk for evidence and restart recovery. Use an encrypted disk for sensitive data.
- Resource figures are measured process RSS on the test machine, not proof of a complete 4 GiB operating-system environment. The configuration is deliberately CPU-only and well below the model/backend allocation limit; see measured validation.

## Source map

`analyzer/ingest.py` indexes facts and deduplicates; `prompts.py` contains the complete versioned local-model prompts and schemas; `llm.py` owns runtime, token counts and retries; `pipeline.py` plans chunks, persists progress, correlates and merges; `db.py` defines checkpoints; `app.py` serves the API; `web/` is the offline dashboard. `AI_USAGE.md` documents coding assistance and observed model mistakes.
