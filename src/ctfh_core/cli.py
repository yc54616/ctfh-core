from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from .benchmark import run_direct, run_matched_pair
from .kernel import run_supervised
from .task_source import LocalTaskSource


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctfh2")
    sub = p.add_subparsers(dest="command", required=True)

    smoke = sub.add_parser("smoke", help="run the local native-agent vertical slice")
    smoke.add_argument(
        "--fixture",
        default=str(
            Path(__file__).resolve().parents[2]
            / "benchmarks"
            / "fixtures"
            / "smoke"
        ),
    )
    smoke.add_argument("--model", default="gpt-6-sol")
    smoke.add_argument(
        "--effort",
        default="high",
        choices=("low", "medium", "high", "xhigh"),
    )
    smoke.add_argument(
        "--mode",
        choices=("direct", "supervised", "pair"),
        default="pair",
    )
    smoke.add_argument(
        "--out",
        help="output root; default .ctfh-runs/smoke-<timestamp>",
    )
    return p


async def _smoke(args: argparse.Namespace) -> dict[str, object]:
    fixture = Path(args.fixture).resolve()
    bundle = LocalTaskSource(fixture).load()
    task = bundle.spec
    if args.out:
        root = Path(args.out).resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        root = Path.cwd() / ".ctfh-runs" / f"smoke-{stamp}"

    if args.mode == "direct":
        return await run_direct(
            task=task,
            run_root=root,
            model=args.model,
            reasoning_effort=args.effort,
            evaluator_data=bundle.evaluator_data,
        )
    if args.mode == "supervised":
        return await run_supervised(
            task=task,
            run_root=root,
            model=args.model,
            reasoning_effort=args.effort,
            evaluator_data=bundle.evaluator_data,
        )
    return await run_matched_pair(
        task=task,
        root=root,
        model=args.model,
        reasoning_effort=args.effort,
        evaluator_data=bundle.evaluator_data,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "smoke":
        result = asyncio.run(_smoke(args))
        print(json.dumps(result, indent=2, sort_keys=True))
        verdict = (
            result["supervised"]["verdict"]
            if args.mode == "pair"
            else result["verdict"]
        )
        return 0 if verdict["passed"] else 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
