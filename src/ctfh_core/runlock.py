from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .agent import CodexCLIAdapter
from .types import RunLock, TaskSpec


def _sha_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        h.update(len(rel).to_bytes(4, "big"))
        h.update(rel)
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def _stable_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _effective_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()


def _codex_behavior_fingerprint(codex_home: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    direct = [
        codex_home / "config.toml",
        codex_home / "AGENTS.md",
        codex_home / "RTK.md",
        codex_home / "memories" / "MEMORY.md",
    ]
    for path in direct:
        digest = _sha_file(path)
        if digest is not None:
            files[str(path.relative_to(codex_home))] = digest

    for base in (
        codex_home / "hooks",
        codex_home / "skills",
        codex_home / "agents",
    ):
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            if path.stat().st_size > 5_000_000:
                continue
            files[str(path.relative_to(codex_home))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()

    plugin_root = codex_home / "plugins" / "cache"
    if plugin_root.is_dir():
        for path in sorted(plugin_root.rglob("SKILL.md")):
            files[str(path.relative_to(codex_home))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()

    return {
        "source_path": str(codex_home),
        "files": files,
        "digest": _stable_hash(files),
    }


def _codex_install_fingerprint(binary: str) -> dict[str, Any]:
    found = shutil.which(binary)
    if found is None:
        raise FileNotFoundError(binary)
    launcher = Path(found)
    resolved = launcher.resolve()
    files: dict[str, str] = {
        str(resolved): hashlib.sha256(resolved.read_bytes()).hexdigest(),
    }
    package_root = resolved.parent.parent if resolved.name == "codex.js" else resolved.parent
    package_json = package_root / "package.json"
    if package_json.is_file():
        files[str(package_json)] = hashlib.sha256(package_json.read_bytes()).hexdigest()
    if package_root.is_dir():
        for path in sorted(package_root.glob("node_modules/@openai/codex-linux-*/vendor/*/codex")):
            if path.is_file():
                files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()

    node_version = subprocess.run(
        ["node", "--version"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    return {
        "launcher": str(launcher),
        "resolved": str(resolved),
        "files": files,
        "node_version": node_version,
        "digest": _stable_hash({"files": files, "node_version": node_version}),
    }


def build_runlock(
    *,
    treatment: str,
    task: TaskSpec,
    adapter: CodexCLIAdapter,
    model: str,
    reasoning_effort: str,
    sandbox: str,
    deadline_seconds: float,
    prompt: str,
) -> RunLock:
    codex_home = _effective_codex_home()
    base = {
        "task_id": task.task_id,
        "task_public_hash": _tree_hash(task.public_dir),
        "target_policy": "local-v0-persisted-epoch",
        "worker_environment": "local-bwrap-pidns-v0",
        "agent": {
            "adapter": adapter.name,
            "adapter_version": adapter.version(),
            "install": _codex_install_fingerprint(adapter.binary),
            "codex_home": _codex_behavior_fingerprint(codex_home),
            "model": model,
            "reasoning_effort": reasoning_effort,
            "sandbox": sandbox,
            "ephemeral": True,
        },
        "permissions": "bubblewrap-fs-isolation+codex-workspace-sandbox",
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "web_search": "prompt-disabled",
        "browser": "disabled",
        "outer_context_optimizers": [],
        "deadline_seconds": deadline_seconds,
        "outcome_authority": "local-exact-candidate-v0",
    }
    base_hash = _stable_hash(base)
    full = {"treatment": treatment, "base": base}
    return RunLock(
        treatment=treatment,
        base_config=base,
        base_hash=base_hash,
        full_hash=_stable_hash(full),
    )


def write_runlock(path: Path, lock: RunLock) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "treatment": lock.treatment,
                "base_config": lock.base_config,
                "base_hash": lock.base_hash,
                "full_hash": lock.full_hash,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
