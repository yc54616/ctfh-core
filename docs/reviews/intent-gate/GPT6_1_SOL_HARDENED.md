BLOCKERS

- [intent_gate.py:170](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:170): Scope normalization silently drops the root marker when combined with another path. Reproduction: commit a ledger containing one agent/manual requirement with `scope.paths: [".", "src"]`. Calling `converge(root, paths=["docs"], execute_commands=False)` without evidence returns `PASS` with `results: []`. Using `scope.paths: ["."]` correctly returns `BLOCKED` with missing evidence. Adding a redundant subpath therefore removes obligations.

- [intent_gate.py:912](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:912): Append can overwrite or delete an existing tracked approval. Reproduction: commit an orphan approval matching a pending principal candidate, then append that candidate with another approver. Append succeeds, but subsequent preflight rejects the modified approval. Deletion also reproduces with the same approver: make ignored `.intent/generated/active-contract.json` a directory, then retry append. Cache publication raises `IsADirectoryError`; rollback deletes the pre-existing tracked approval, and preflight reports its deletion.

NON_BLOCKING

- v0 explicitly trusts local approval files, the interactive principal channel, and ordinary Git history; malicious filesystem or Git-history manipulation is outside its threat model.
- Natural-language semantic checking, RESTORE, split/merge, and partial-scope supersession are explicitly deferred.

VERDICT

- NOT READY