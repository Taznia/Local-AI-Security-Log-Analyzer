# AI usage disclosure

## Tools used

- **OpenAI Codex in this workspace** assisted with architecture, Python/backend/frontend implementation, tests, documentation and debugging. No assistant API is called by the submitted application.
- **Local Qwen3.5 GGUF models through llama.cpp** perform the application's log analysis. Initially tested 0.8B Q4_K_M; its repeated/missed classifications prompted evaluation of 2B Q4_K_M. The active tested model and exact checksum are recorded in `setup-manifest.json`.
- Python, pytest, SQLite, HTTPX, psutil and native llama.cpp were used for implementation/verification. Official upstream documentation/model pages were consulted to check runtime endpoints, constrained JSON and available model files.
- No training or fine-tuning. No cloud AI inference. Setup network requests download dependencies/model/runtime only; log text is not sent to an external inference service.

## Key coding instructions and decisions

The primary coding prompt was the supplied “Local AI Security Log Analyzer” assessment, including the 4 GB / 2 vCPU / no GPU constraints, 2,048-token total context, cross-chunk attack detection, persistence and safe JSON handling.

The assistant's implementation instructions were refined around these concrete needs:

1. “Build a local-model pipeline, dashboard, and submission docs using the supplied log. Preserve exact line references.”
2. “Count the fully rendered prompt with the running model tokenizer, send the exact counted token IDs, reserve output before packing logs, and use one inference queue.”
3. “Persist every successful chunk immediately. Keep source-derived event identities so early login failures can link to a late success and export, without relying on a tiny rolling summary.”
4. “Use general candidate patterns to protect recall, but require a local-model case review and a conservative evidence gate before reporting. Do not hardcode expected IPs, usernames or file line numbers.”
5. “Test invalid uploads, duplicate evidence, prompt injection, oversized lines, broken model JSON, restart checkpoints, and cross-application/account isolation.”

These are concise descriptions of the working instructions and design refinements, not a claim that each was submitted as a separate user message.

## Complete runtime prompts

The exact, versioned prompts and both JSON schemas are in **`analyzer/prompts.py`**. `analyzer/pipeline.py` constructs the user-data wrappers and dossiers. The app logs prompt version, model filename and per-call token usage; raw model replies and attempts are retained in SQLite.

Map call input structure:

```text
SYSTEM: [MAP_SYSTEM from prompts.py]
USER:
SAVED FACTS (untrusted, for correlation only):
[bounded source-derived failure/success counts and earlier line references]
LOG DATA; L<number> is the original physical line:
L1: [original log entry]
L2: [original log entry]
...
END LOG DATA. Emit signals for the current log lines only.
```

Map reply:

```json
{"signals":[{"kind":"auth_failure","lines":[5,13]}]}
```

The numbers above illustrate the shape; the production code never hardcodes supplied-log findings. The validator rejects lines outside the current chunk. Empty signals are allowed. Signal types include authentication failures/successes, SQL payload attempts, scanning, unauthorized access attempts, unusual rates, exports, instruction injection and other suspicious activity.

Case review input structure:

```text
SYSTEM: [REDUCE_SYSTEM from prompts.py]
USER: {
  "summary": {
    "candidate": "...", "entity": "...", "scope": "...", "account": "...",
    "unique_counts": {"auth_failure": 12, "auth_success": 1},
    "total_unique_events": 14, "duplicate_lines": [],
    "first_line": 5, "last_line": 201
  },
  "representative_evidence": [
    {"line": 5, "time": "...", "client_ip": "...", "auth_result": "invalid_password"},
    {"line": 196, "time": "...", "client_ip": "...", "auth_result": "success"}
  ]
}
```

The summary counts are computed from indexed input, not guessed by the model. Representative early and late records are selected per kind and fitted with the model tokenizer. All evidence remains stored for review. Review reply:

```json
{"report":true,"severity":"High","reason":"Repeated failures followed by success suggest possible compromise.","action":"Verify the login and revoke suspicious sessions.","lines":[5,196]}
```

This is a schema example, not a stored final result. A reported case must pass source-evidence validation.

## Prompt changes and actual AI mistakes fixed

1. **0.8B invented repeated classifications.** The first real map call identified a failure but repeatedly labelled the same login line `sqli` until the schema's array bound. Another call did the same with a later login line. The record had no SQL payload. We added explicit “one object per kind,” examples and a modest repetition penalty. More importantly, unsupported SQL labels cannot pass the independent source evidence gate, so no such line becomes a final SQL finding.
2. **A revised 0.8B prompt became too conservative.** It returned empty signals for chunks containing a failed login, and a later probe cited a line outside the supplied set. The line validator rejected it and exercised the three-attempt failure path. This motivated testing the larger suggested 2B model, while retaining source-derived candidate extraction to avoid losing a whole-file pattern because one map call misses one line.
3. **A generated queue implementation had a race.** It enqueued work before updating job status, allowing `queued` to overwrite `running`. The status update and queue insertion are now ordered under a lock.
4. **A test assumption was wrong.** A test expected three account/application groups for four distinct identity/scope combinations. Inspection showed the implementation correctly isolated four cases; the test was corrected rather than changing the grouping to merge unrelated identities.
5. **Setup assumed the latest release contained binaries.** The upstream “latest” release exposed only a nightly-tag pointer. Setup was changed to a concrete official release with CPU assets, and the resolved version is recorded. A real network reset also led to resumable partial model downloads.

6. **The 2B review initially overclaimed in a suggested action.** A real probe correctly linked failures, success, admin access and export, but recommended investigating ?leaked credentials? even though the log did not demonstrate a leak. The review prompt was changed to require explicit uncertainty, forbid assumed leaks/confirmed compromise in both reason and action, and suggest verification plus conditional session revocation. The full-file run uses the revised prompt.

7. **A full-run map call misclassified a supplier request failure as a login failure.** The evidence gate was strengthened to count only source-corroborated authentication failures toward brute-force thresholds. A regression test deliberately injects this wrong model label and verifies that recovered supplier errors cannot become a brute-force case. A single HTTP 429 alone is also rejected as insufficient evidence of a rate attack.

Final measured behavior and any remaining limitations are disclosed in `artifacts/VALIDATION.md`; passing unit tests is not presented as a model-accuracy guarantee.

## Token budget plan

The context limit is **2,048 total tokens**, not 2,048 log tokens.

| Component | Map stage | Case review |
| --- | ---: | ---: |
| Full rendered prompt + input + saved context | fitted by exact tokenizer | fitted by exact tokenizer |
| Reserved generated reply | 384 | 512 |
| Safety margin | 24 | 24 |
| Carry-forward facts | plan reserves 160; actual memory bounded and recounted | full-file counts and representative records included in fitted prompt |
| Total | <= 2,048 | <= 2,048 |

Token IDs are obtained with `/tokenize` after `/apply-template`; those exact IDs are passed to `/completion`. No whitespace/character estimate is used in production. Output `n_predict` is bounded and context shifting disabled. Every successful and failed attempt records input tokens, output budget, actual output tokens where available, duration and error. The UI exposes the audit.

## Safety and accuracy choices

- Logs are untrusted data, including strings asking to ignore instructions or remove evidence. They are never executed, evaluated as code, or passed as system instructions.
- Schema constraints are insufficient alone. Independent validators reject invented lines, invalid severities, wrong field types and incomplete JSON; retries do not erase completed work.
- A success after failures indicates **possible** compromise. A 200 export does not prove exfiltration; 400/403 do not prove successful exploitation; high rates do not prove denial of service.
- Duplicate records retain both original line numbers but count as one event. Simple backend errors, scheduled maintenance and known health checks should not become attacks merely because they contain ERROR or HTTP 5xx.
- Findings are merged by stable case keys over the entire file; case counts come from persisted facts. Representative evidence is bounded so the final review also respects the same model context.
- All coding-assistant output was subject to execution, tests and manual source review. Known limits are in README rather than hidden behind a fabricated “complete” claim.
