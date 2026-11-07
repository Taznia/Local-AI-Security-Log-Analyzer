# Verification results

Date: 7 November 2026. Tested on Windows 11 with Python 3.13.4.

## Automated checks

- **29 tests passed** (`python -m pytest -q`).
- One deprecation warning comes from Starlette TestClient using an AnyIO alias; it did not fail a check.
- Python modules compile and frontend JavaScript passes `node --check`.
- HTML/JavaScript identifier checks found 44 unique DOM IDs and no missing referenced IDs.
- The live HTML, CSS and JavaScript endpoints return HTTP 200 with restrictive content-security headers.
- Tests include broken JSON, failed calls, timeout/idle-wait/retry ordering, checkpoint reuse, identity/scope isolation, false model labels on supplier errors, and rejection of an isolated 429 as a rate attack.

## Complete supplied-file run

This was a real local-model run over the supplied file, not a fixture-fed UI or a hardcoded report. Raw log evidence and report JSON remain local and are excluded from Git.

| Measurement | Observed result |
| --- | ---: |
| Input | 94,398 bytes / 258 original physical lines |
| Unique events / exact duplicate copies | 257 / 1 |
| Completed chunks | 38 / 38 |
| Successful completed local-model calls | 44 (38 map + 6 whole-file reviews) |
| Merged findings | 6 (1 High, 5 Medium) |
| Findings spanning multiple chunks | 5 |
| Unsupported candidate cases excluded | 21 |
| Total prompt tokens across successful calls | 61,293 |
| Total generated tokens | 1,274 |
| Largest input + reserved reply + safety margin | 1992 + 24 = **2016 / 2,048** |
| Peak observed backend + model RSS | **1,719 MiB (1.68 GiB)** |
| Recorded processing elapsed | 2357.6 seconds |
| Median completed model-call wall time | 27.55 seconds |

The elapsed total includes an isolated 1116.2-second call wall-time outlier. These are recorded wall times, not a controlled throughput benchmark or a guaranteed runtime on another machine.

The report covers repeated authentication failures followed by successful session activity/export, SQL injection attempts, sensitive-endpoint scanning, denied access/traversal attempts, unusual request rates, and instruction injection in log content. No health monitor, recovered database timeout, supplier retry, scheduled maintenance error, or ordinary isolated application error was reported as an attack. This is a qualitative supplied-file check, not a general precision/recall benchmark.

The authentication case links 15 source events across 14 chunks. Repeated SQL-payload events are merged into one case; an exact duplicate retains both physical references but counts once. Every displayed raw evidence string was checked against its original input line. All 258 lines are covered by the chunk plan, and every call's reserved budget plus margin is within 2,048 tokens.

## Restart and final transport checks

A real backend/model restart was performed after five completed chunks. The application resumed from that checkpoint. Audit inspection confirmed one successful map call each for those chunks; saved successful calls were not repeated. The completed merged report also survived a later final-build restart.

After adding the model's in-memory authentication key and restricted CORS origin, a fresh real-model smoke analysis completed through the backend. A benign health request produced zero findings. Direct unauthenticated model metadata and inference requests both returned **HTTP 401**. No external API key is required from the user.

Observed network connections for the backend and model were loopback-only. The owned runtime uses two CPU threads, one slot, no GPU and a 2,048-token context. A 250 ms memory sampler covers startup and inference and stops the owned model above 3 GiB.

## Verification limits

Native-browser visual QA was stopped by a computer-use URL safety check. The frontend has source/identifier/syntax checks and live HTTP verification, but a visual browser check is not claimed. The measured process footprint is backend plus its model, not the entire Windows desktop. A separate hard-limited 4 GB Linux VM/container run has not been performed.

The small model occasionally emits wrong map labels and broadly worded recommendations, including calling an IP an account. Source-corroboration gates prevent unsupported map labels from becoming final cases, but human review of reasons/actions is still needed. AI_USAGE.md documents observed mistakes and prompt changes. Models are not claimed to be perfectly accurate.
