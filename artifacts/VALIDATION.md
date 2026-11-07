# Verification results

Date: 7 November 2026. Tested on Windows 11 with Python 3.13.4.

## Automated tests

- 28 tests passed (`python -m pytest -q`).
- One deprecation warning comes from Starlette TestClient using an AnyIO alias; it did not fail a check.
- Python modules compile and frontend JavaScript passes `node --check`.
- HTML/JavaScript identifier checks found 44 unique DOM IDs and no missing referenced IDs.
- The live HTML, CSS and JavaScript endpoints return HTTP 200 with restrictive content-security headers.

## Real local-model probes

Qwen3.5 2B Q4_K_M correctly identified a failed login, encoded SQL payloads, and an instruction-injection attempt in focused supplied-log probes. A case review linked early failed logins with a later success, same-session admin access and a large export. The first review action overclaimed a credential leak; the runtime prompt was revised before the full-file run. Smaller-model mistakes and conservative gates are documented in AI_USAGE.md.

## Supplied-file run

The complete supplied-file run is in progress. Its 258 original lines were planned into 38 chunks using the model tokenizer. Measured totals will be added here after completion; raw log evidence and report JSON remain local and are excluded from Git. This file does not yet claim final model accuracy.

A real backend/model restart was performed after five completed chunks. The application resumed from that checkpoint. Audit inspection confirmed one successful map call each for chunks 1?5, followed by a new call for chunk 6; completed calls were not repeated.

Observed network connections for the backend and model were loopback-only. The owned runtime is configured for two CPU threads, one slot, no GPU and a 2,048-token context.

## Verification limits

Native-browser visual QA was stopped by a computer-use URL safety check. The frontend has source/identifier/syntax checks and live HTTP verification, but a visual browser check is not claimed. The measured process footprint is backend plus its model, not the entire Windows desktop. A separate hard-limited 4 GB Linux VM/container run has not been performed.
