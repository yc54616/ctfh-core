**BLOCKERS**

1. **A Git merge can silently drop or rewrite accepted history, including principal-approved events** (`check_history_immutability`, lines 1098–1127)
   - **Cause:** `git log --name-status --diff-filter=MDRCTU` prints nothing for merge commits unless a `--diff-merges` option (`-m`, `-c`, `--cc`) is passed. The `git diff HEAD` check is also clean once the merge is committed. So if a merge resolution deletes or replaces an accepted event or approval, neither check sees it.
   - **Concrete scenario:**
     1. `main` appends principal event `0005-proposal.yaml` (ADD INV-0009, with its approval).
     2. `feat` appends agent event `0005-proposal.yaml`. The slug comes from the proposal filename, so this name clash is likely.
     3. On `feat`, `git merge main` gives an add/add conflict. The agent resolves it with `--ours` and commits; `main` fast-forwards.
   - **Result:** replay is valid (feat's event 0005 chains correctly). INV-0009 is gone with no approval and no history error, and its approval file sits orphaned. `preflight`, `append` and `converge` all pass, and `converge` can return PASS without the principal invariant.
   - The same applies when the two branches used different filenames and someone deletes one of the forked event files inside the merge commit.
   - **Why it blocks:** this is an ordinary merge, not the "history rewrite" the threat model excludes. It breaks both "ordinary committed rewrites/deletions are rejected" and "an agent event cannot cancel a principal atom".
   - **Fix:** add `--diff-merges=separate` (`-m`) to the log scan. Real deletions or rewrites then show up as D or M against one of the parents, while normal merges only show A.
   - **Missing test:** an add/add merge on the same event path resolved with `--ours`.

**NON_BLOCKING**

1. **Approvals are read after commands run.** Evidence approvals are loaded at line 1481, after the verification commands run (line 1428). A command can write `.intent/evidence-approvals/<digest>.yaml` (an ignored directory) during the run and satisfy the principal check. Under the stated threat model this is no worse than writing the file directly, but you should snapshot approvals before the command loop.
2. **Hidden `.gitignore` requirement.** `.intent/evidence-approvals/` must be gitignored, or a principal manual PASS can never happen:
   - an untracked approval makes the worktree dirty;
   - committing it moves HEAD, so the evidence `commit` is stale;
   - new evidence has a new digest, which needs a new approval, and the loop repeats.

   Only the test fixture sets this up. `.intent/.lock` also shows up untracked after the first append. Check the real repo's `.gitignore` and document this. A side effect is that evidence approvals have no Git audit trail.
3. **The clean-worktree check can miss modified tracked files.** `git status` hides tracked files marked `assume-unchanged` or `skip-worktree`, and files outside a sparse checkout. HEAD then may not match the tree that was tested. Reject index entries with those bits set.
4. **The interactive-approval guarantee is unverified because the CLI source was not provided.**
   - The library records `channel: interactive-confirmation` no matter how it is called (line 971, and the `approve_evidence` default at line 1333). `confirmed=True` and `approved_*_hash` are trivial for any caller to supply.
   - The CLI must require a TTY, offer no hash or `--yes` flag, and show the canonical event rather than the raw proposal YAML. `safe_load` silently accepts duplicate and merge keys, so the raw text can differ from what is hashed.
5. **`body_ref` can point anywhere in the project.** It can reference mutable files, including `.intent/generated/active-contract.json`. An ordinary edit to that file makes the ledger unreplayable, and the only recovery is restoring the exact bytes. Such files are also outside the `.intent/decisions` history check. Restrict `body_ref` to `.intent/decisions/`.
6. **Unapproved "principal" labels.** An event with `actor=principal` that touches only agent atoms needs no approval. It gains no privileges, but the audit label is unapproved.
7. **No recovery path after a recorded rewrite.** Once Git history records a deletion or modification, the history check fails on every later run; there is no baseline to accept it. Recovery needs a history rewrite. This is consistent with branching being deferred, but document it.
8. **Rollback is not durable.** The rollback unlinks (lines 989–991) are not followed by a directory fsync, so a crash during rollback can bring the event back. The outcome is fail-closed or a still-valid event.
9. **Schema/runtime "parity" is overstated.**
   - The runtime accepts things the schema rejects: padded ids, uppercase `body_hash`, and `./` or duplicate scope paths.
   - The schema accepts things the runtime rejects: absolute or `..` paths, a string `prev` at seq 0, and a null `prev` at seq > 0.

   The runtime is the authority, so this is harmless, but the test name claims exact parity.
10. **Proposal input is parsed loosely.** `note: null` becomes `"None"` (line 880), and unknown proposal keys are silently ignored.
11. **Test gaps.**
    - The two command-mutation tests are identical, and they only assert that an error is raised, so a failed precondition would also pass.
    - There is no merge test and no test of concurrent appends under the lock.
    - The test file has two `__main__` blocks.
12. **Minor robustness issues.**
    - `preflight` raises instead of returning a report on bad paths or a missing `git` binary.
    - `bash -lc` loads login profiles, so verification results depend on the host's shell setup.

**VERDICT**

NOT READY. The only blocker is the merge blind spot (blocker 1); the fix is one flag plus one regression test. This review is static only: running tests or probes needed permission I didn't have in this session. The CLI wasn't provided, so CLI bypasses are reviewed only at the library entry points.
