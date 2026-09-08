# FullAgent 3.4 verification record

## Observed final results

- **175 automated tests discovered: 174 passed, 1 skipped.** Final exit code 0; runtime 247.245 seconds in this Linux sandbox.
- **55 existing module self-tests passed**, exit code 0, rerun after the final code corrections.
- All application/test Python sources passed `compileall`.
- CLI version/help/doctor, headless argument validation and API diagnostics are exercised without live model calls.
- **50 API admission/continuation/fault regressions** were added to the previous 125-test suite. The tests use stdlib unittest; no extra application runtime dependency was added.
- Eight final renderer states were individually opened and visually inspected: ready, queued eight-agent view at 80 columns, shared cooldown, single recovery probe, completed, permanent authentication block, queued 72-session overview, and a3 family. No overlap/overflow or false running/completed labels were observed in these captured states.

Evidence: `verification/computer-tests.txt`, `verification/legacy-selftests.txt`, `verification/api-tests.txt`, `verification/runtime-fixture.json`, and `verification/visual-review.json`. Hashes identify the exact reviewed images, not an independent certification.

## New recovery evidence

- Eight local worker slots remain distinct from API permits. Default API warmup is one lane, cap two/domain; same-origin/account aliases cannot multiply capacity.
- Family-fair queues, no permits for waiters, explicit request/token admission, oversized-estimate debt, quota resets and conservative older-header handling.
- Numeric/HTTP-date cooldowns are not shortened to the old 15/60-second caps. Nonfinite/malformed metadata is rejected or ignored appropriately.
- Coalesced congestion epochs, one useful-work half-open probe, stale-success protection, learned server ceilings, and availability failures not misrepresented as smaller RPM quotas.
- More than three simulated transient failures after a successful write do not poison the worker or reset its successful task-attempt count.
- A real approved counter command stays at **run_count = 1** after repeated later model failures.
- Actual localhost HTTP transport covers overload recovery, lower HTTP concurrency with eight active logical identities, shared aliases, and permanent authentication blocking without repeating a bad request indefinitely.
- A separate healthy domain completes its schedulable research while another domain remains in a long cooldown.
- Cancel/resume during cooldown preserves committed tool history; cancellation during a held recovery probe drains permits/reservations.
- Resume after new acceptance-check logs refreshes safe read-only reviews rather than rejecting old evidence bindings or repeating completed implementation.
- Lost post-command/post-peer-note receipts remain uncertain across restart. These records are not replayed or erased by automatic repair.
- One invalid client configuration does not prevent seven healthy clients from doing research; all-invalid and permanent/budget boundaries remain honest failures.
- Research, planning, child decomposition/build/review can defer/recover. A complete protected hierarchy fixture produces **64 actual child files** and passes real subprocess checks.
- Global/child budgets, scope leases, approvals, checks and both review layers remain completion gates. Fixed/adaptive and disabled-pacing diagnostics have regressions.

Earlier core/hierarchy/pooling tests also cover eight and explicit 64-worker overlap, dependency barriers, read-only scopes, bad-output repair/backups, parent/child lease exclusion, cancellation, bounded context/boards, unknown-token accounting, current-hash file safety, command output/timeouts, research single-flight, real local JSON/SSE framing and transport auth/cookie/redirect/HTTPS protections. Older concurrency tests now explicitly declare their known-capacity local-fixture policy; their unconstrained profile is not the production API default.

Two additional final-review issues were reproduced and fixed before these final results: malformed custom response metadata, and stale review bindings after rerunning acceptance checks. A review-side uncertainty guard was also added and tested so automatic repair cannot erase an unknown peer-note outcome.

## Renderer and resource fixture

`verification/runtime-fixture.json` records a deterministic local HTTP mission, a permanent-authentication fixture and a held hierarchy scheduler:

- Eight logical agents; **8 actual output files**, real approved command and actual acceptance subprocesses.
- **4 injected API failures recovered**; counter command executed **once**.
- Actual peak simultaneous localhost HTTP requests: **1**, under configured per-domain cap **2**. A cap is an upper limit, not a promise that two requests will always run.
- Observed executor threads: **8**.
- Hierarchy: **72 logical sessions**, one held fixture model call and **58 queued child requests** at capture time.
- Successful real checks and `completed` outcome. Permanent-authentication case: `needs_attention` after **one injected authentication fault**, not endless bad-key requests.
- Fixture process peak RSS: **51.23 MiB**, using `resource.getrusage(RUSAGE_SELF)`.

**Not a cloud-model, speed, full TTY or real 4 GB hardware benchmark.** Scheduler time is accelerated in the protocol/recovery fixture; model responses are deterministic test code. This measures the small fixture process (including Pillow), not arbitrary project builds or remote inference. Screenshots use the application's pure renderer, not a running prompt_toolkit terminal capture. Every image is explicitly labeled.

An optional portable fixture renderer is included as `verification/render_api_fixture.py`. It needs Pillow in addition to the application dependencies and writes under a fresh temporary directory (or `FULLAGENT_QA_OUTPUT`). It is test evidence generation, not a production fallback or extra required application dependency.

## Skipped / unverified

The skipped test is `test_actual_prompt_toolkit_ui_constructs_and_handles_on`: `rich` and `prompt_toolkit` were unavailable. Pure renderer/bridge tests, actual approval handler, dispatch hooks and headless paths ran; the full interactive application was not end-to-end validated here.

No valid cloud-provider credentials or usable external connectivity were available for live API/search evaluation. Deterministic clients are isolated under `tests/`; actual production execution still needs configured providers. Local HTTP tests do not establish remote reasoning quality, provider entitlement, invoices or availability.

Only Linux CPython **3.13.14** was exercised. Other declared Python/OS versions, wheel/binary builds, editable dependency installation and published installers remain unverified. No GitHub push/tag/release, deployment or Notion workspace mutation occurred. This source ZIP is not Git history.

No zero-error/unlimited-recovery guarantee, arbitrary-command exactly-once semantics, OS sandbox, enterprise certification, ARC-AGI ranking, new trained-model claim or universal RAM/speed promise is made. See `API_RESILIENCE.md` and `ENTERPRISE_READINESS.md`.

## Reproduce

```bash
python -m pip install -e .
python -m compileall -q fullagent tests
python -m unittest discover -s tests -v
python run_selftests.py
python main.py computer doctor
```

Use a separate `FULLAGENT_HOME` and nonproduction temporary project. Review code before running tests/commands. Live runs require real keys/connectivity; the deterministic test suite does not. A SHA256 manifest and ZIP CRC verify delivered bytes, not signed provenance or a security audit.
