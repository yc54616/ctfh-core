BLOCKERS

- [intent_gate.py](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:169): `scope.paths: [".", "src"]` becomes `["src"]`, losing the project-root scope. A read-only in-memory reproduction with that sole requirement returned `PASS` and no result rows for `converge(paths=["docs"])`, despite having no evidence.
- [schema.json](/home/yc54616/Desktop/ctfh-core/.intent/schema.json:78) and [intent_gate.py](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:169): The runtime accepts and deduplicates `scope.paths: ["src", "src"]`, while the declared event schema rejects it. An in-memory genesis-event reproduction confirmed the disagreement.

NON_BLOCKING

- v0 explicitly trusts local Git history and interactive approval, and does not determine whether code satisfies natural-language requirements.

VERDICT

- NOT READY