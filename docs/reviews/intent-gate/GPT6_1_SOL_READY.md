BLOCKERS

- [cli.py:179](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/cli.py:179): External `--out` hard links bypass the project-write restriction. Reproduced in a clean committed fixture with passing checks: hard-link an accepted event file to an external `report.json`, then converge with that output path. The CLI exits 0 with `PASS`, but `write_text()` overwrites the accepted event through the shared inode. Subsequent replay fails with missing event fields. This produces false PASS and an unreplayable ledger.

NON_BLOCKING

- optional hardening only

VERDICT

- NOT READY