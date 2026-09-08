# FullAgent 3.4 — 8 leads × 8 child workers

**Real configured model calls + real workspace tools. Not a simulated computer, not 72 local LLMs, and not a certification that this is the world's most powerful agent.**

Tumhare 8 existing specialists ab har ek apne 8 internal workers ko delegate kar sakte hain:

- **8 lead sessions:** a1–a8.
- **64 child sessions:** a1.1–a1.8, …, a8.1–a8.8.
- **72 logical sessions total**, with independent role/task context and accounting.
- **Default local worker cap remains 8.** A separate API gate starts at 1 request per domain, with default maximum 2; see `API_RESILIENCE.md`. Worker identities are not an instruction to load 72 models or start 72 concurrent processes.
- The hierarchy remains **ON by default for new missions in 3.4**. `/computer swarm off` restores eight-lead-only mode. A saved user preference still wins.
- The compact dashboard shows eight leads; child work and spending are not concealed. Use the tree and inspector to drill down.

## Install this source update

This ZIP is a local source update, not a published GitHub release or prebuilt binary. Older curl/binary installers can still install an older version.

```bash
cd aiagent/py.test
python -m pip install -e .
python main.py --version
python main.py
```

Expected source version: `3.4.0`. Configure your own valid model/provider API key locally; see `COMPUTER_MODE.md` for provider setup. Never paste secrets into agent goals or source files. The old embedded key defaults were removed in 3.2; rotate/revoke any exposed keys that belonged to you.

Inside FullAgent:

```text
/on /absolute/path/to/your-project
/computer tree
Build the project described in SPEC.md, with real regression tests and documented limitations.
```

`/on` without a path uses the current working directory. There is no model call just from opening the dashboard. Submitting a goal starts billable model activity. Selected source content is sent to the configured model providers.

## What each family contains

| Child suffix | Specialty | Purpose within the parent's workstream |
|---|---|---|
| .1 | scout | Primary sources, constraints and relevant existing code |
| .2 | designer | Interfaces, structure and edge cases |
| .3 | builder | Exclusive implementation scope |
| .4 | connector | Integration and compatibility |
| .5 | tester | Meaningful tests and failure cases |
| .6 | auditor | Correctness, security and permissions |
| .7 | optimizer | Performance and resource use |
| .8 | finisher | Delivery, documentation and completion gaps |

Leads remain Atlas/architect, Scout/researcher, Forge/implementer, Link/integrator, Probe/tester, Shield/security, Pulse/performance and Guide/delivery. These are role prompts and independently scheduled sessions, **not independently trained experts or guaranteed domain qualifications**.

## Actual execution pipeline

1. **64 read-only research/inspection workers.** Each has its own parent-specific specialty. No child writes or runs commands in discovery. Successful reports are checkpointed and reused on recovery.
2. **Eight leads synthesize and refine.** Each sees bounded findings from its eight children plus the other leads' reports. Source text, repository text and peer notes remain untrusted findings, not higher-priority instructions.
3. **Validated top-level plan.** Exactly eight parent workstreams, non-overlapping owned scopes, acyclic dependencies and 1–12 explicit acceptance checks.
4. **Validated child delegation.** Every lead proposes exactly eight substantive child assignments. Child scopes must be subsets of that parent's scopes. Siblings cannot own overlapping files/directories, including case ambiguities. Deeper recursive spawning is rejected. Invalid plans get a bounded correction attempt, not automatic permission.
5. **Human approval.** The approval preview includes the root plan, all 64 child scopes/dependencies, concurrency and budget information. `hierarchy-plan.json` records the detailed child instructions. Nothing is implemented without plan approval. Oversized approval previews fail closed rather than silently hiding scope.
6. **One bounded DAG scheduler.** Ready families get fair turns. A child's sibling dependencies and its parent's upstream dependencies must be satisfied. Parent and descendant execution leases cannot overlap. The lead integrates only after all eight child assignments finish successfully. No pool thread waits for child jobs occupying the same executor.
7. **Real checks and two review layers.** Actual subprocess exit codes and file checks are recorded, followed by 64 read-only child reviews and eight lead reviews. A child's unresolved blocker cannot be erased by a lead saying “done.” Reviews are model opinions, not a substitute for independent audits.
8. **Bounded repair and recheck.** Review-only failures target affected families and their downstream dependents. If an acceptance check fails without a safe deterministic owner mapping, all families are reconsidered rather than guessing a culprit. Repair stops at the configured round, token or time limit.

`completed` means the declared checks passed, all eight parent tasks and all 64 child tasks reported done, and both review layers passed. It does **not** prove all possible requirements, security properties or real-world outcomes.

A genuine read-only child may own `files=[]`: it can inspect/research and hand off findings, but cannot edit or execute host commands. It is not a fake writer. One-file projects cannot be safely given eight simultaneous writers to that same file.

## Live controls

| Command inside FullAgent | Result |
|---|---|
| `/computer status` | Current overview, aggregate usage and checks |
| `/computer api` | Actual API queue, admission window, pacing, faults and recovery probes |
| `/computer tree` | Print all eight families and 64 child rows |
| `/computer tree a3` | Switch the persistent dashboard to a3's eight children |
| `/computer tree overview` | Return to compact eight-lead view |
| `/computer inspect a3.2` | Actual assignment, model, usage, activity and task result |
| `/computer pause` | Pause new actions; in-flight requests may finish |
| `/computer resume` | Unpause the current mission |
| `/computer cancel` | Cancel and drain active workers; preserve completed writes |
| `/computer report` | Final report path once the mission stops |
| `/computer list` | Saved missions for this workspace |
| `/computer resume <id>` | Resume a checkpoint with fresh approval |
| `/computer swarm off` | Eight-lead-only mode for the next mission; idle only |
| `/computer swarm on` | 8×8 hierarchy for the next mission; idle only |
| `/off` | Cancel/drain if needed, then restore legacy chat |

The family view separates **session activity** from **implementation task state**, and running children from API-waiting children. A waiting-only family is not labeled as running. A finished research response is not a finished implementation. No private chain-of-thought is retained or displayed.

## Model inheritance

Use existing tool-capable model IDs listed by `/model`:

```text
/computer model a3 <existing-model-id>
/computer model a3.2 <existing-model-id>
/computer models
```

A child uses its explicit override, otherwise its parent's override, otherwise the global selected model. Model choices and effort are frozen for a mission. Child models need valid local credentials just like lead models. No production fallback to scripted clients exists.

## Resource and cost controls

Settings can be changed only while idle, using `/computer set <name> <value>`.

| Setting | Default | Meaning |
|---|---:|---|
| `max_parallel` | 8 | Local worker slots; allowed 1–64; API waiters yield their slot |
| `api_max_parallel` | 2 | Per-domain API cap, also bounded by local worker slots |
| `api_adaptive` | true | Initial API lane 1, conservative pacing and capacity feedback |
| `api_tokens_per_minute` | 60000 | Estimated local token-traffic policy, not provider entitlement |
| `token_budget` | 400000 | Shared mission token allowance, **not per agent** |
| `child_token_budget` | 50000 | Per-child allowance within the global limit |
| `wall_minutes` | 60 | Cumulative active elapsed time, including pause/approval/API waits |
| `child_research_steps` | 3 | Bounded tool/model steps per discovery child |
| `child_work_steps` | 12 | Steps per child execution attempt |
| `child_review_steps` | 3 | Steps per child review round |
| `child_output_tokens` | 2048 | Child response ceiling, also capped by the global response ceiling |
| `child_context_chars` | 32000 | Child context ceiling, also capped by the global context ceiling |
| `requests_per_minute` | 0 | Adaptive ceiling 60 when unset; with adaptive off, 0 disables extra fixed RPM pacing |
| `repair_rounds` | 2 | Maximum additional repair rounds |

Examples:

```text
/computer set max_parallel 4
/computer set requests_per_minute 60
/computer set token_budget 800000
```

Changing `token_budget` increases potential paid usage. Set it deliberately. A long goal or large parent assignment can exceed the bounded child context; prefer concise goals and actual files rather than pasting an entire repository into the goal.

Reported provider usage is distinguished from conservative estimates when usage is missing or a request fails. Every HTTP attempt, including compatibility retries, reserves budget. Outstanding reservations after a crash are conservatively charged and attributed to their child where the checkpoint contains that information. Provider invoices remain the financial authority; this is not dollar-accurate billing.

A child allowance failure blocks that child/family, not an unlimited automatic top-up. The shared mission budget can stop the entire run. There is no hidden multiplication of the budget by 64.

### Why this can stay lightweight

- One bounded executor, not eight nested executors creating 64/512 unbounded threads.
- Backpressure: only available slots are submitted; ready parent families are alternated.
- Shared per-thread HTTP sessions across logical agents. The model-transport pool is bounded by `max_parallel + 1` (the coordinator sometimes plans while workers are idle), rather than 72 separate persistent connection pools.
- Remote provider endpoints require HTTPS, except explicit loopback endpoints. Model POST redirects are refused; ambient `.netrc`/proxy credentials are ignored and cookies are cleared. Use an approved HTTPS API gateway if your company requires one.
- Identical public research queries/fetches share one in-flight request. Successes have a bounded cache; failures are briefly shared as errors, never invented results.
- Bounded reports, shared-board projections, logs, file reads and response buffers. Dashboard refreshes do not copy all 72 reports/fingerprint maps.
- File mutations and approved host commands remain serialized. More logical agents does not mean 64 simultaneous builds.

**4 GB is not a blanket performance guarantee.** Model inference runs on the configured provider, but compilers, browsers, containers and project commands can still exceed available memory. `max_parallel` is a logical job cap, not an OS-wide process or RAM limit. Start at 2–8 on a modest computer. Changing `max_parallel` alone cannot bypass the default API cap. Increasing API capacity is a separate explicit setting for known resources, quotas and budgets; it does not guarantee increased throughput. Small tasks can be cheaper/faster with `/computer swarm off`.

## Headless use

```bash
python main.py computer run --root ./project --goal "Implement SPEC.md and test it" --swarm --parallel 8 --plan-only
python main.py computer run --root ./project --goal "Implement SPEC.md and test it" --swarm --parallel 4 --rpm 60
python main.py computer tree <mission-id>
python main.py computer inspect <mission-id> a3.2
python main.py computer status <mission-id>
python main.py computer api <mission-id>
python main.py computer report <mission-id>
```

For scripts, unapproved actions are denied. `--approve-plan` explicitly approves the proposed file scopes. Repeated `--allow-command '["python","-m","unittest","discover"]'` arguments grant only those exact commands in the workspace root for that mission, including reruns after code changes. Inspect the code before granting execution. TUI pause/resume is in-process control; the headless inspection commands are one-shot checkpoint reads, not a remote control service.

`--child-tokens` and `--tokens` set the child and global allowance. `--no-swarm` selects legacy eight-lead scheduling. `--no-research` disables public source adapters, **not model API calls**.

## Recovery and audit boundaries

Checkpoints now use version 3; version-1 and version-2 checkpoints remain readable. Durable mid-tool continuations exist only for work executed by the new code; old checkpoints cannot retroactively contain those receipts. To resume an old eight-lead mission, select `/computer swarm off` or `--no-swarm`. A saved mission cannot silently switch to a different hierarchy. Start a new goal to change topology.

On resume, child plans are revalidated against their saved parent scopes and approval is requested again. Interrupted/changed branches and their dependent parents re-inspect current files. Successful discovery reports are reused. Temporary API faults preserve the pending job instead of marking it finished/failed. Completed writes/committed commands are not blindly repeated; changed verification evidence refreshes safe read-only reviews. Unknown action outcomes require reconciliation and are not erased by repair. Completed writes are not automatically rolled back.

- `state.json`: bounded checkpoint, tasks, reports, API policy state, per-agent usage and in-flight reservation ledger.
- `continuations/`: bounded worker context/tool cursor/receipt records, with unknown side effects explicitly blocked from automatic replay.
- `hierarchy-plan.json`: eight validated child plans.
- `events.jsonl` / previous rotated segment: bounded real-action audit trail, not tamper-proof centralized logging.
- `backups/`: content-addressed copies before file-tool overwrites.
- `report.md`: current outcomes, child/parent assignments, checks, sources and gaps.

Subprocess changes are not automatically snapshotted. File tools reject traversal, common secret paths, symlinks/hardlinks and stale hashes; that is defense in depth, not a kernel security boundary.

## Enterprise deployment caveat

See `ENTERPRISE_READINESS.md`. This is a stronger **single-user, single-host source implementation**, not a hosted multi-tenant platform, certified autonomous engineer, SOC 2 claim or guaranteed NASA-grade system. For untrusted repositories, use a separate least-privilege container/VM with host-enforced CPU, RAM and network limits. Review model-provider data handling and licenses. Independent security review and live staging validation are still required.

See `VERIFICATION.md` for the exact executed checks and remaining validation gaps. Test fixtures are deliberately isolated under `tests/`; their screenshots and metrics are labeled as local test evidence, not live external-model achievements.
