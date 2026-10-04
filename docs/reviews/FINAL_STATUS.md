# Final Architecture Validation

Date: 2026-10-04

## Result

The architecture is frozen for v0 implementation.

### GPT-6 Sol

Initial review:
- rejected fake-only v0
- required a real native-agent vertical slice
- required target-epoch freshness rather than global revision invalidation
- required process-level cancellation and honest external-effect semantics
- required a matched benchmark of deployed configurations

Final blocker validation:
- first pass found one blocker: direct-native vs CTFH wrapper comparison was not strictly matched
- architecture/benchmark docs were changed to freeze all non-treatment variables
- regression validation result: BLOCKERS none / VERDICT READY

### GPT-6 Astra

Initial review:
- rejected premature plugin/strategy abstractions
- required target lifecycle to be separate from worker-environment lifecycle
- required explicit ownership/enforcement gaps
- required execution/verification/cleanup to remain separate facts

Final blocker validation found three issues:
1. target resources and worker resources needed distinct ownership scopes
2. STARTING cancellation needed a provisioning-completion barrier
3. native-agent opaque model calls could not honestly promise request-level quota enforcement

Changes made:
- introduced WorkerOwnerTag and TargetOwnerTag
- WorkerEnvironment reaper cannot touch target resources
- TargetProvider owns restart reconciliation and TargetEpoch
- cancellation cannot become terminal while create can still publish resources
- native adapters declare usage/budget/rate-control capability
- hard quota configs are rejected when an adapter cannot enforce them

Regression validation result: BLOCKERS none / VERDICT READY

### Claude Opus 5.5

A full domain-neutral architecture review completed successfully before the final rewrite. Its main recommendations were incorporated:

- one kernel loop / one writer
- disposable attempts
- environment as the local transaction boundary
- narrow external-effect journal
- no generic lease/plugin framework in v0
- direct strong worker baseline
- pure/replayable strategy
- measure latency before sophisticated scheduling
- do not resume attempts after crash
- add pass@k only after baseline

A later blocker-only regression retry was rejected by Claude Code's [cyber] safeguard despite the domain-neutral prompt. That retry produced no architecture verdict and is not treated as evidence either for or against the final design. The successful full Opus 5.5 review is preserved as OPUS_5_5.md.

## Frozen v0 principle

Build the smallest real vertical slice:

LocalTaskSource -> LocalTargetProvider -> LocalWorkerEnvironment -> pinned Codex CLI -> LocalOutcomeAuthority -> measured authoritative result

Do not implement deferred modules until the direct baseline exists and paired measurements show a gain.
