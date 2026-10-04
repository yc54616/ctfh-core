# Benchmark Rules

CTFH optimizes verified outcomes under real wall-clock/resource constraints.

## Experimental unit

One observation is:

```
task instance
x frozen RunLock
x evaluation protocol
x repetition
```

A HarnessProfile is a configuration label, not a causal explanation.

## Required metrics

- authoritative completion
- completion at deadline
- elapsed time to authoritative completion
- queue wait
- environment startup/readiness
- native agent duration
- verification duration
- cleanup duration
- infrastructure errors
- refusal/policy errors
- leaked resources after termination
- model/tool usage when observable
- cost when applicable

Failures and timeouts stay in the denominator.

Never publish solved-only latency as the main timing metric.

## Paired comparison

Every middleware/module experiment must freeze all non-treatment conditions.

For the v0 wrapper-overhead gate, direct-native and CTFH-supervised arms must
match on:

- task/input hashes
- initial target state and reset policy
- WorkerEnvironment image/tool versions and resource limits
- native agent binary/version
- model/reasoning/provider class
- prompts/rules/skills/native context
- search/browser availability
- permissions/sandbox
- deadline, quota/budget and retry policy
- OutcomeAuthority/evaluator

Both arms emit an effective-configuration manifest. The pair is invalid when a
non-treatment field differs.

Examples of later paired treatments:
- CTFH wrapper supervision itself
- Ubuntu-like vs ready Kali environment
- k=1 vs pass@k
- native context only vs context-mode
- no RTK vs explicit RTK output compaction
- direct vs planner/executor

## Leakage controls

For promotion tests:
- prefer fresh/live/private tasks
- isolate native agent memory
- isolate retrieval/context indexes
- fresh workspace
- no prior browser history
- no previous trajectory injection
- evaluator secrets unavailable to worker
- record web-search availability
- audit surprising successes for known-solution retrieval

Static NYU/CyBench results are regression signals, not final proof.

## Competition replay

Scheduling policies must be evaluated at event level:
- same total attempt slots
- same model/provider quotas
- same deadline
- same budget guard
- same task arrival/unlock schedule

Unconstrained pass@k is not evidence of a better competition scheduler.

## Promotion gate

A new module/profile is promoted only when paired evaluation shows a meaningful
gain in at least one:
- verified solve probability
- verified points by deadline
- tail latency on successful tasks without unacceptable solve loss
- recovery/reliability

and does not introduce unacceptable contamination, cleanup leaks, or measurement
blind spots.
