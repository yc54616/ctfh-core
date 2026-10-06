BLOCKERS

- **Approval can bind to an unapproved event.** [cli.py `_intent_main`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/cli.py:99) confirms hash A, then [append_event](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:720) reloads the proposal and rebuilds the event. Changing the proposal during the prompt produced a successful append and approval for hash B without confirming B.

- **Root-path filtering can falsely PASS.** [_normalize_scope / _scope_overlap](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:113) retain root aliases as `[""]`, which do not overlap `"src"`. Reproduced `converge(paths=["."])` returning PASS with zero results despite an active, unverified scoped requirement. Root-scoped atoms also evade applicability and collision checks.

- **Canonical event hashes and contract semantics disagree.** [normalize_atom / _binding_projection](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:156) leave keys and verification references without NFC normalization, while `_event_hash` normalizes them. Unicode-equivalent keys produced identical event hashes but different contract hashes, violating the documented normalization contract.

- **Convergence does not establish the verified code snapshot.** [_git_commit / converge](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:926) accept `NO_GIT_COMMIT` and check only HEAD, without checking uncommitted code changes. Reproduced PASS on a dirty tree with HEAD-stamped evidence, and PASS without any Git commit.

- **Concurrent appends can overwrite accepted history.** [append_event](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:720) checks the base and destination before publication; [_atomic_write](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:259) subsequently uses overwriting `os.replace`. Two concurrent proposals with the same stem both returned success, while the later publication erased the earlier accepted event.

- **Malformed ledger events are silently accepted.** [_validate_event_shape](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:350) and `normalize_atom` accept `v: true`, `seq: false`, missing `note`, and missing `scope`. All four were rejected by [.intent/schema.json](/home/yc54616/Desktop/ctfh-core/.intent/schema.json:6) but accepted by runtime validation, contradicting v0’s malformed-event rejection rule.

NON_BLOCKING

- Defer improving malformed-proposal diagnostics: unknown targets currently escape candidate validation as `KeyError`.

VERDICT

- NOT READY