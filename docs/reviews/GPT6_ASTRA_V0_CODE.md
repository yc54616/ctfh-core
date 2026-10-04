BLOCKERS

- [environment.py:127](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/environment.py:127): Kill tracks process groups, not the complete process tree, and clears ownership without confirming descendant death. Reproduced: a child in another session remained running after `kill()` returned with empty tracking.
- [environment.py:115](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/environment.py:115): Processes start before their PID is persisted; marker writes are non-atomic, and sweep silently skips malformed markers. A crash can leave live workers undiscoverable.
- [environment.py:80](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/environment.py:80): Cancelling the provisioning coroutine bypasses the token-based barrier while its copy thread continues. Reproduced: cancellation returned, then an untracked workspace appeared.
- [kernel.py:295](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:295), [benchmark.py:77](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/benchmark.py:77): Exceptional exits lack guaranteed environment cleanup and failure-result recording. An injected native launch failure left the attempt RUNNING, its environment present, and no result; matched-pair execution aborts instead of retaining the infrastructure failure.
- [kernel.py:142](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:142): Host-wide locking/epochs protect recovery scoped only to the current `run_root`. Restarting with the CLI’s new default output directory neither marks earlier attempts LOST nor sweeps their workers.
- [environment.py:115](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/environment.py:115), [agent.py:59](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/agent.py:59): Changing cwd and using `workspace-write` does not isolate host-readable evaluator files or previous results. Copying only public files and instructing the agent not to inspect parents does not enforce evaluator-secret separation.
- [kernel.py:201](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:201), [target.py:28](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/target.py:28): Freshness is checked before awaited verification, while target reset updates only provider memory. Reproduced: resetting during verification still committed SOLVED at epoch 1 with the provider at epoch 2.
- [kernel.py:158](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:158), [kernel.py:174](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:174): The deadline covers only agent execution; provisioning and verification remain outside it. Reproduced: an 80 ms setup with a 20 ms task deadline still returned PASS. Provisioning cancellation can also wait indefinitely for its copy thread.
- [kernel.py:214](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:214), [kernel.py:232](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:232): SOLVED is committed before process-tree shutdown, sealing, and artifact publication; publication references are discarded. Reproduced: publication failure left SOLVED/FINISHED with no result record and an undeleted environment.
- [runlock.py:59](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/runlock.py:59), [agent.py:50](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/agent.py:50): RunLock hashes fixed `~/.codex` paths while native execution inherits `CODEX_HOME` and shared configuration/context. Different `CODEX_HOME` values produced identical hashes. `--ephemeral` only disables session-file persistence; effective configuration and context isolation are therefore not established by a matching hash.
- [benchmark.py:77](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/benchmark.py:77): The direct arm uses the same CTFH deadline, cancellation, and provider kill machinery as the supervised arm. This includes lifecycle supervision in the baseline, contrary to the documented treatment, excluding that shared overhead from the comparison.

NON-BLOCKING

- All 10 existing tests passed; the targeted probes above exposed uncovered failures.
- No native model run was performed during this review.

VERDICT

- NOT READY.