from __future__ import annotations

import asyncio
from typing import Any

from .types import TaskSpec, TargetHandle, Verdict, WorkerEnvironment


class LocalOutcomeAuthority:
    authoritative = True
    effect = "local"

    def __init__(self, evaluator_data: dict[str, Any]):
        self._expected = str(evaluator_data["expected_candidate"])

    async def verify(
        self,
        task: TaskSpec,
        target: TargetHandle,
        env: WorkerEnvironment,
        candidate: str | None,
    ) -> Verdict:
        await asyncio.sleep(0)
        passed = candidate == self._expected
        return Verdict(
            passed=passed,
            authoritative=True,
            reason="candidate matched evaluator secret" if passed else "candidate mismatch",
            candidate=candidate,
        )
