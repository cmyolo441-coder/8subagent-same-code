# FullAgent 3.4 — adaptive API admission and resumable agents

## Kya fix hua?

`/on` mein 8 lead jobs aur optional 64 child jobs ab bhi hain. **Local worker concurrency aur model API concurrency ab alag controls hain.** Har agent apna independent retry loop chala kar same API ko flood nahi karta. Temporary transport faults par job ko failed declare karne ke bajay uska context/receipts preserve karke scheduler mein park kiya jata hai.

**Zero API load, unlimited free inference, or uninterrupted reasoning during a total provider outage is not possible.** The goal is to control bursts, honor available capacity, preserve completed work and recover without restarting the whole mission. Real API calls, provider availability and explicit token/time limits still apply.

This is inspectable orchestration code, not an ARC-AGI benchmark result, newly trained model or certified enterprise platform.

## Install the corrected source

Stop the older FullAgent process first. Extract this ZIP, then install **this extracted directory**, not an older binary/curl release:

```bash
cd aiagent/py.test
python -m pip install -e .
python main.py --version
python main.py
```

The version should be `3.4.0`. Keep your existing `FULLAGENT_HOME`/local configuration and configure provider credentials locally. Do not paste keys into chat or commit them. No API key is supplied by this bundle.

```text
/on
```

Enter a real project goal. Protective API control is automatic for new missions; there is no extra mandatory setup.

```text
/computer api
/computer status
/computer tree
/computer tree a3
/computer inspect a3.2
```

`/computer api` shows actual admission/queue state, recent faults, pacing, quota constraints and recovery probes. A queued/waiting agent is **not** pretending to perform model inference.

## How the control works

### 1. Separate local jobs from API permits

The local executor still defaults to 8 worker slots. A separate governor decides whether a model request may start. A waiting request holds **no HTTP permit and no token reservation**. Worker/decomposition tasks yield their executor slot instead of sleeping inside a per-agent retry loop.

Healthy APIs and ready local tool work can continue. Dependency barriers remain enforced. A coordinator waiting for a required architecture plan stays at that genuine barrier; it cannot invent the missing plan.

### 2. Shared, family-fair admission

Aliases with the same URL origin and credential fingerprint share one traffic domain, including aliases with different display names or URL paths. Actual keys are not logged or stored in quota snapshots. No automatic account/key rotation or provider/model substitution is performed.

Ready requests rotate across lead families. The governor checks:

- Current in-flight window.
- Request pacing and server-provided request quota/reset metadata.
- Estimated token traffic and server-provided token quota/reset metadata.
- Shared cooldown and whether a recovery probe is already active.

The same logical pending request cannot acquire two simultaneous permits. Quota headers are allowlisted and bounded; authorization/cookies are excluded. Older out-of-order responses cannot restore stale quota headroom over a newer lower value.

### 3. Conservative slow-start and feedback

With default settings, each domain starts with **1** in-flight request and a **12 RPM** warmup rate. The automatic RPM ceiling is 60 when no explicit ceiling is configured. Sustained successful responses can gradually increase the window, up to the default cap of **2** per domain. Latency feedback and congestion reduce admission. Server quota information constrains it further.

These values are local policies, **not promises that a provider grants those quotas**. Missing/proprietary headers, other applications using the same account and changing service limits can still cause a 429. A sufficiently low provider quota necessarily means slower work.

### 4. Shared circuit epochs and one recovery probe

Temporary 408/425/429/500/502/503/504/529 responses and supported connection/incomplete-stream failures preserve the job rather than exhausting a fixed three-attempt worker loop.

- Concurrent failures from the same congestion episode are coalesced; they do not cause 64 independent capacity reductions/retry storms.
- A circuit stops extra model calls during cooldown.
- Exactly one queued useful request is admitted as the half-open recovery probe for that domain.
- A stale in-flight success cannot close a newer outage's circuit.
- A successful probe resumes controlled admission, not an immediate all-worker burst.
- Availability failures alone are not treated as evidence of a smaller RPM quota.

Retry-After seconds and HTTP dates, plus supported quota reset durations/timestamps, are honored without the former 15/60-second truncation. Missing signals use a bounded client cooldown with deterministic jitter. **The transport may reattempt a pending inference after admission; this is not a claim that recovery can happen without another request.** What changed is the coordinated admission/recovery architecture, not merely a bigger retry counter.

### 5. Durable conversation and tool receipts

Worker conversations, reasoning-step position, tool-result cursor and hashed repeat counters live in bounded, checksummed files under the mission's `continuations/` directory. The main checkpoint keeps a small index, not 72 full conversations. Unchanged parked state is not repeatedly rewritten.

A temporary API error after a successful write/command resumes from its committed tool receipt. It does not create a fresh worker conversation that blindly executes the action again. API waiting/failures do not consume successful reasoning steps or count as finished/failed task attempts.

Write-ahead uncertainty is recorded at the actual file commit/process-launch boundary, **after** validation, approval and cancellation checks. A cancellation before an effect is not mislabeled as an executed command.

**Not transactional exactly-once execution:** a process crash or lost receipt during a side-effecting tool can leave an unknown outcome. Such a continuation is flagged for human reconciliation and is not automatically replayed or auto-repaired. Inspect actual files/command logs and the local recovery record. Do not manually erase the uncertainty just to suppress the warning.

Known completed tasks/receipts survive explicit recovery. Changed owned/upstream files cause affected branches to be re-evaluated rather than reusing stale work. Existing acceptance checks may intentionally be rerun after changes or explicit mission recovery, with the existing approval rules; they are not universally idempotent. When those checks produce fresh logs/evidence, a valid unfinished read-only review is restarted against that new evidence. This does not reset implementation receipts. Missing/corrupt records and uncertain peer-note outcomes still fail closed, and user-action review failures cannot trigger automatic repair that erases their recovery records.

## Defaults and optional tuning

Settings can be changed only while idle and apply to the next/newly resumed mission.

| Setting | Default | Meaning |
|---|---:|---|
| `max_parallel` | 8 | Local worker-pool slots, not API capacity |
| `api_max_parallel` | 2 | Maximum simultaneous model requests per traffic domain |
| `api_adaptive` | true | Start at one API lane and use feedback/slow-start |
| `requests_per_minute` | 0 | Automatic ceiling 60 in adaptive mode; an explicit positive value sets a ceiling |
| `api_tokens_per_minute` | 60000 | Estimated local token-traffic policy; 0 disables this local bucket, not server constraints |
| `api_cooldown_seconds` | 5 | Initial missing-signal cooldown; valid longer server delays are still honored |
| `token_budget` | 400000 | Existing shared mission budget; not multiplied by agent count |
| `child_token_budget` | 50000 | Existing allowance inside the shared budget |
| `wall_minutes` | 60 | Includes approval, pause and API wait time |

With `api_adaptive=false`, the configured API window/pacing is fixed, but server quotas, shared circuits and recovery still apply. With adaptive off and RPM=0 there is no extra fixed RPM pacing; use only when you understand the service capacity. The tests deliberately use explicit unconstrained local-fixture profiles when testing 8/64 worker parallelism. That is not the protective production default.

Typical optional tighter limits:

```text
/computer set api_max_parallel 1
/computer set requests_per_minute 10
```

Do not raise limits blindly when a provider is rejecting calls. Raising local concurrency does not purchase provider capacity. No automatic budget increase or silent provider failover is performed.

Headless equivalents:

```bash
python main.py computer run --root ./project --goal "Your real project goal"
python main.py computer run --root ./project --goal "Your goal" --api-parallel 1 --rpm 10
python main.py computer api MISSION_ID
```

`--parallel` controls local worker slots; `--api-parallel` controls the API limit. `--api-tpm` overrides the estimated traffic budget. `--api-fixed` explicitly selects fixed admission. A new/overriding setting never bypasses approvals or the shared mission budget.

## Which errors need intervention?

- **Temporary congestion/outage:** parked and automatically revisited through the shared governor while the mission remains within its budgets/deadline.
- **401 or recognized invalid authentication:** correct the local credentials. Repeating the same bad key is not recovery.
- **402 or recognized insufficient-quota/billing errors:** correct the account/billing condition. The program does not rotate keys, invent credit or raise spending limits.
- **Invalid request/model/permissions, filtered or oversized results:** the affected work is reported honestly; another model/provider is not silently selected.
- **Uncertain host tool outcome or damaged continuation:** reconcile actual state before further side effects. No automatic replay.
- **User cancellation, time/token limits or safety/approval boundaries:** still stop/pause work as intended. API resilience does not bypass these controls.

One missing client no longer prevents other configured clients from finishing their schedulable work. A required failed dependency can still block integration. If all jobs need the same unavailable provider, they wait: there is no honest way to continue their API-dependent reasoning without a service.

Explicitly resuming a saved mission allows one guarded validation request for a previously corrected credential/billing block; server cooldowns still apply. Use:

```text
/computer list
/computer resume MISSION_ID
```

Checkpoint versions 1 and 2 remain readable and are upgraded to version 3. Old checkpoints cannot retroactively contain new mid-tool receipts. Saved hierarchy mode must still match, and plan approval is requested again. Keep copies of the old state/source until recovery is verified.

## Accounting, security and limits

- Every admitted client attempt has a reservation. Waiting in a queue is not billed as a model attempt. A permit known not to have entered the client is released without inventing token usage.
- Missing/ambiguous remote usage remains **conservatively estimated**, including failed requests. A failure is not advertised as free or as a monetary refund. Provider invoices are authoritative; estimates can exhaust a budget before recovery.
- Budgets/timeouts are not secretly extended. Long outages can exceed them; explicitly inspect and adjust appropriate limits before resuming.
- No new runtime dependency, local model weights, 72 heavy processes or background health-inference service is added.
- The governor covers **one running Computer**. It is not a distributed/cross-process account gateway and cannot control traffic from other apps, computers or independent FullAgent processes.
- Domain grouping is intentionally conservative. Per-model/provider-specific quota schemes may require future adapter tuning; this is not a universal quota-discovery guarantee.
- Continuations contain selected project context and should be kept private. Checksums detect differences, not maliciously rewritten records. They are not signed or tamper-proof compliance evidence.
- Existing scope checks, approvals, backups, HTTPS policy, redirect refusal, per-request authorization and pooled-session isolation remain. Approved commands still run on the host, not in an OS sandbox.
- No new shared streaming-capability cache, batching endpoint, automatic provider failover, RBAC/SSO, multi-tenant service or enterprise certification is claimed.

See `VERIFICATION.md` for observed tests, fixture scope and unverified live-cloud/full-terminal areas. Deterministic model clients exist only under `tests/`; production still needs a real configured service.
