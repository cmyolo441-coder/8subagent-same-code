# Enterprise readiness and security boundaries

FullAgent 3.4 adds enterprise-oriented orchestration and recovery controls to a local CLI. It is **not represented as enterprise-certified, fully audited, multi-tenant, bug-free, or the world's most capable AI system**. Agent count establishes none of those properties.

## Implemented in computer mode

- Fixed 8-lead / 64-child topology; independent task contexts and usage accounting, no model-authorized deeper recursion.
- One bounded executor, backpressure and fair ready-family selection; validated parent and sibling dependencies.
- Child write scopes inherited from parent scopes; no sibling overlap, including case ambiguities. Parent and descendant execution leases cannot overlap.
- Current-hash checks, serialized atomic text-file writes, pre-write backups and bounded action records.
- Explicit plan approval including all child scopes; separate exact-command grants, independent of legacy global auto-approve.
- Shared global and individual child token allowances; bounded context/output/steps; per-attempt reservations and conservative unknown/crash usage accounting.
- Separate family-fair API admission, token/request traffic policy, slow-start/latency feedback, full server cooldowns, shared circuit epochs and one useful-work recovery probe per origin/credential domain.
- Durable conversation/tool receipts; API-waiting jobs release local worker slots. Actual effect-boundary markers distinguish pre-effect cancellation from unknown outcomes.
- Fresh read-only reviews after changed verification evidence; uncertain records and user-action review failures are not erased by auto-repair.
- Versioned validated recovery; interrupted/changed branch reevaluation and dependency invalidation.
- Actual retrieved-source metadata, tool receipts, command logs, exit codes and failed checks.
- Completion gated on parent and child task reports, declared acceptance checks and both review layers.
- Lead overview, live family view, full tree, individual assignment/model/usage inspection and final reports.
- Shared bounded per-thread HTTP sessions with per-request authorization, no ambient netrc/proxy credentials or retained cookies, HTTPS outside loopback, and no automatic model POST redirects.
- Bounded single-flight research caching: identical in-flight queries share a fetch, and failures remain errors rather than invented results.

These controls apply to `/on` / `computer` workflows. They do not create a security boundary around all legacy FullAgent tools or arbitrary host code.

## Boundaries not provided

**No OS sandbox.** Approved commands execute real host code. They can access files/network outside text-tool scopes, spawn processes and consume substantial RAM/CPU. A minimal environment and `shell=False` are not isolation. Exact argv grants include reruns after project code changes; inspect the current code. Subprocess mutations do not receive automatic per-file backups.

**No tamper-proof or compliance audit.** Checkpoints and rotated logs are local files controlled by the OS user. The SHA256 manifest detects byte differences, not maliciously rewritten manifests. There is no signed attestation or immutable centralized audit store.

**No cluster-wide API gateway.** Admission covers one running Computer, not other applications/processes sharing the account. No automatic provider/key rotation, batching entitlement or guaranteed zero-error/zero-load service is provided. Temporary recovery stays inside the configured token/time limits; credentials/billing need explicit correction. Unknown remote usage remains conservatively estimated.

**No organization identity/distributed platform.** No tenant isolation, enterprise RBAC/SSO, approval quorum, managed key vault, centralized policy service, distributed scheduler, remote-control service, multi-host failover or published availability SLO is included. The cooperative workspace lease coordinates processes using the same state store, not arbitrary editors or independent stores.

**No infallible expertise.** Role names describe prompts, not independently trained or qualified experts. Models can make correlated mistakes across both review layers. Checks only cover their declared requirements; use independent security/code review.

**No exhaustive data-loss prevention.** Common secret-path and credential-pattern checks are defense in depth. Selected source content is sent to configured model providers. Review retention, training-use, region and confidentiality policies. Public-source URL checks are application-level defenses, not DNS-pinned egress enforcement or a firewall.

**No universal speed/RAM guarantee.** More agents can add overhead and cost. Local builds, browsers and model servers still need resources. A small deterministic fixture is not a production or 4 GB hardware benchmark.

**No new trained-model breakthrough is claimed.** This update is a real, inspectable hierarchy, scheduler and recovery implementation using configured models and ordinary Python/networking components. No ARC-AGI score, new trained model or research breakthrough is claimed.

## Recommended rollout

1. Review the source; run the included tests on your target OS/Python; install reviewed/pinned dependencies and validate the actual terminal UI.
2. Configure least-privilege provider keys through local/approved secret tooling. Do not commit keys. Rotate/revoke credentials exposed in older copies/history.
3. Use a separate non-root container/VM for untrusted projects. Mount only intended project/state directories. Enforce CPU, memory, process, filesystem and egress limits outside FullAgent.
4. Establish approved provider data handling and an HTTPS gateway where required. Ambient proxy/netrc settings are deliberately ignored by model HTTP sessions.
5. Start with `--plan-only`; inspect parent plans, all child scopes and acceptance commands. Reject excessive scope or weakened tests.
6. Start at low concurrency and an explicit shared token/time budget. Observe actual provider latency, quota limits and spend. Local worker slots and API capacity are separate controls; higher capacity is an opt-in policy, not an automatic optimization.
7. Maintain source control and independent backups. Add project-specific tests, dependency/license scanning, independent security review, staged deployment and human sign-off.
8. Export required audit data to controlled retention/logging systems. Define incident response, budget ownership, key revocation and recovery procedures.
9. Run live provider and end-to-end staging tests before operational promises. See `VERIFICATION.md` for what was tested during this source update and remaining gaps.

Deterministic clients exist under `tests/` only. They exercise real scheduling, file tools, local HTTP and subprocess checks. They are not a production fallback and do not prove remote-model reasoning quality.
