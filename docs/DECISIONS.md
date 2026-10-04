# Architecture Decisions

Date: 2026-10-04

## Decision 1 — Thin supervisor, not a second agent framework

Accepted.

Native agent harnesses are first-class execution units. Codex CLI, Claude Code
and future native agents keep their own reasoning/tool/context semantics.

CTFH supervises lifecycle, isolation, verification and timing.

Reason:
- CAGE/AgentCyberRange evaluates integrated native harnesses.
- recent studies show strong general-purpose native agents are already difficult
  baselines to beat.
- duplicating native loops creates latency and hides provider-specific strengths.

## Decision 2 — Environment and agent are separate benchmark dimensions

Accepted.

A model name is insufficient. A deployed profile includes native agent version,
effective model, environment image/tooling, prompts/context/skills, strategy,
browser configuration and deadline/concurrency parameters.

The experimental unit is one task instance × frozen RunLock × evaluation
protocol × repetition.

## Decision 3 — Split TargetProvider from WorkerEnvironment

Accepted after Astra review.

Target lifecycle and attacker/worker lifecycle are different:
- restarting a worker must not reset a remote challenge target
- parallel attempts may use independent workers while sharing or cloning targets
- target epoch controls effect freshness

## Decision 4 — One kernel loop, one authoritative writer

Accepted after Opus 5.5 review.

Do not run per-task state coordinators with competing SQLite writers.

Use:
- one host lock
- one kernel epoch
- one bounded inbox
- one SQLite writer
- short batched commits
- dispatch only after causal state commit

## Decision 5 — Environment is the local transaction boundary

Accepted.

Do not journal every shell/tool call.

Ordinary local effects happen inside a disposable, attempt-owned environment.

Journal only consequential external effects that escape that boundary.

## Decision 6 — No universal lease framework in v0

Accepted.

A lease row cannot revoke an unmediated browser socket/process.

v0 isolation:
- private environment per attempt
- OwnerTag
- host epoch
- hard process-tree kill
- orphan sweep
- exclusive resource transfer only after old owner is confirmed dead

Specific brokers/leases are introduced only for actual shared resources.

## Decision 7 — UNKNOWN belongs to external operations, not attempts

Accepted.

Attempt lifecycle and external-delivery uncertainty are different facts.

A worker may terminate cleanly while a remote submission remains UNKNOWN.

## Decision 8 — No global base-revision stale rejection

Accepted after Sol/Astra review.

Concurrent evidence should not be invalidated because unrelated facts changed.

Use:
- target instance + target epoch for action freshness
- attempt identity/owner tag
- final outcome compare-and-swap
- evidence dependency provenance

## Decision 9 — Direct strong native agent is the baseline

Accepted.

Planner/executor, heterogeneous mixing and parallel strategies must beat direct
baseline on verified points by deadline.

Evidence:
- 200-task study: Pro/Pro greatly outperformed Pro/Flash and Flash/Pro in its
  tested D-CIPHER configuration.
- 30-task second-look study: direct GPT-5, evaluator, and planner pipelines had
  the same peak 19/30 coverage while orchestration increased elapsed time.

## Decision 10 — pass@k before complex multi-agent orchestration

Planned, not v0.

After baseline:
- independent pass@k
- then hedged pass@k (launch extra attempts after historical p50 if unsolved)
- cancel siblings only after authoritative success

This is simpler to measure and isolate than shared-context multi-agent teams.

## Decision 11 — Browser is a capability, not a global control plane

Accepted after Sol/Astra review.

Do not open a browser if HTTP/shell/source tools are sufficient.

When a browser is required:
- explicit owner and profile
- GhostChrome for managed browser sessions
- chrome-use when the real authenticated profile is required
- never silently migrate auth state
- Jev later as an action-decision overlay, not a second browser owner

## Decision 12 — Context systems are experimental views

Accepted.

Do not blindly stack native compaction + RTK + context-mode + codebase-memory.

- RTK: output-compaction view only
- context-mode: large-output sandbox/index only when benchmarked useful
- codebase-memory: large source-tree structural retrieval only when useful

Raw/provenance rules apply before transformations where the underlying agent
exposes raw output.

RTK rewrite/auto-allow is not an authorization boundary.

## Decision 13 — Skills later, using Agent Skills format

Accepted.

Technique/workflow knowledge is packaged with progressive disclosure and hashed
into RunLock.

Do not build skill loading into the kernel.

## Decision 14 — Static benchmark scores are not promotion evidence alone

Accepted.

CTFusion shows contamination/search-time solution retrieval can inflate static
CTF benchmark results.

Promotion requires fresh/live/private holdouts and leakage controls.

## Decision 15 — No generic plugin ecosystem in v0

Accepted by all three reviews.

Explicit factories/configuration first.

Only after multiple real implementations reveal stable replacement pressure do
we freeze a Protocol and add Python entry-point discovery.
