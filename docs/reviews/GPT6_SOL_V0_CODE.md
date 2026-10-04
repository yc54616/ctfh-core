BLOCKERS

- [environment.py:115](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/environment.py:115): The worker is an ordinary host process with no filesystem isolation. It can read the evaluator fixture outside its copied workspace, violating evaluator secret isolation and invalidating the score.
- [environment.py:127](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/environment.py:127): Kill and sweep signal only recorded process groups, do not confirm descendants are dead, and discard PID records even after a wait times out. [kernel.py:250](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:250) can therefore mark an attempt CANCELLED while a child survives.
- [environment.py:80](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/environment.py:80): STARTING cleanup handles `CancelToken` but not cancellation of the `create()` task. The background copy can publish resources after cancellation; [kernel.py:295](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:295) only closes SQLite on that path.
- [kernel.py:114](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:114): Recovery opens and sweeps only the current run directory. The CLI normally creates a new directory each run, so a restart leaves prior RUNNING attempts and orphan processes untouched despite advancing the host epoch.
- [kernel.py:174](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:174): The deadline starts after environment setup; launch/version checks, preparation, verification, and cleanup have no task deadline. A result can be counted as passing after the documented deadline. A native launch exception also bypasses cleanup and leaves the attempt RUNNING.
- [kernel.py:196](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:196): The attempt becomes FINISHED before its process tree is stopped. Artifacts are then published before sealing, and the returned content hashes are never recorded in SQLite ([kernel.py:232](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:232)). Mutable or partial output can be treated as evidence.
- [target.py:15](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/target.py:15): Target epochs exist only in provider memory. A new provider returns epoch 1 for the same instance, and [kernel.py:150](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/kernel.py:150) writes that value over a persisted epoch; reset freshness can regress.
- [benchmark.py:77](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/benchmark.py:77): The “direct” arm uses CTFH’s deadline supervisor, process kill, and environment teardown. It is not the documented direct native baseline, so the measured difference omits those wrapper costs.
- [runlock.py:41](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/runlock.py:41): RunLock hashes `~/.codex` rather than the effective `CODEX_HOME` configuration and records a version string rather than executable content. Different native configurations can receive the same base hash and be reported as matched.

NON-BLOCKING

- [agent.py:95](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/agent.py:95): “First candidate” records when `candidate.txt` first exists, including an empty or partial file.

VERDICT

- NOT READY.