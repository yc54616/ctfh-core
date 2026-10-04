from __future__ import annotations

import json
from pathlib import Path

from .types import TaskBundle, TaskSpec


class LocalTaskSource:
    def __init__(self, fixture_root: Path):
        self.fixture_root = fixture_root.resolve()

    def load(self) -> TaskBundle:
        meta = json.loads((self.fixture_root / "task.json").read_text())
        evaluator = json.loads((self.fixture_root / "evaluator.json").read_text())
        public_dir = self.fixture_root / "public"
        if not public_dir.is_dir():
            raise FileNotFoundError(public_dir)
        return TaskBundle(
            spec=TaskSpec(
                task_id=meta["task_id"],
                title=meta["title"],
                public_dir=public_dir,
                deadline_seconds=float(meta.get("deadline_seconds", 900)),
                points=int(meta.get("points", 1)),
            ),
            evaluator_data=evaluator,
        )
