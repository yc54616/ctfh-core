BLOCKERS

- [cli.py:162](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/cli.py:162): `converge --out` writes after all snapshot revalidation. The unrestricted destination can overwrite tracked source or ledger files, yet the CLI still emits `PASS` and exits successfully. Output publication must preserve or revalidate the checked snapshot.
- [intent_gate.py:1098](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:1098): The history scan omits merge diffs. An ordinary merge commit can alter/delete accepted history without detection. Removing a trailing event can leave a valid shorter chain, silently retiring its obligations without an authorized cancellation. Merge-introduced mutations must also be rejected.

NON_BLOCKING

- Trusting local principal confirmations and excluding malicious filesystem access or Git-history rewriting are acceptable v0 limitations.
- Strict 1:1 supersession, optional cache checking, and deferred natural-language semantic analysis are acceptable.

VERDICT

- NOT READY