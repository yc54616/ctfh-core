from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from .agent import AgentAdapter, ClaudeCLIAdapter, CodexCLIAdapter
from .digest import tree_snapshot_digest
from .trace import TRACE_MANIFEST_SCHEMA
from .types import RunLock, TaskSpec


@lru_cache(maxsize=64)
def _cached_sha256(path_text: str, size: int, mtime_ns: int) -> str:
    del size, mtime_ns
    return hashlib.sha256(Path(path_text).read_bytes()).hexdigest()


def _sha_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return _cached_sha256(str(path), stat.st_size, stat.st_mtime_ns)


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


def _optional_tree_hash(root: Path) -> str | None:
    return tree_snapshot_digest(root) if root.is_dir() else None


def _effective_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()


def _codex_behavior_fingerprint(codex_home: Path) -> dict[str, Any]:
    # Only inputs that are actually mounted into the attempt belong here.
    # Host config/plugins/hooks/memory are intentionally excluded because
    # AgentAdapter.materialize() supplies the attempt-local native config.
    files: dict[str, str] = {}
    for path in (
        codex_home / "AGENTS.md",
        codex_home / "RTK.md",
    ):
        digest = _sha_file(path)
        if digest is not None:
            files[str(path.relative_to(codex_home))] = digest

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
    files: dict[str, str] = {}
    digest = _sha_file(resolved)
    if digest is not None:
        files[str(resolved)] = digest
    package_root = resolved.parent.parent if resolved.name == "codex.js" else resolved.parent
    package_json = package_root / "package.json"
    package_digest = _sha_file(package_json)
    if package_digest is not None:
        files[str(package_json)] = package_digest
    if package_root.is_dir():
        for path in sorted(package_root.glob("node_modules/@openai/codex-linux-*/vendor/*/codex")):
            digest = _sha_file(path)
            if digest is not None:
                files[str(path)] = digest

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


def _generic_install_fingerprint(binary: str) -> dict[str, Any]:
    found = shutil.which(binary)
    if found is None:
        raise FileNotFoundError(binary)
    launcher = Path(found)
    resolved = launcher.resolve()
    digest = _sha_file(resolved)
    files = {} if digest is None else {str(resolved): digest}
    return {
        "launcher": str(launcher),
        "resolved": str(resolved),
        "files": files,
        "digest": _stable_hash({"files": files}),
    }


def _effective_claude_home() -> Path:
    return (Path.home() / ".claude").resolve()


def _adapter_runtime_profile(
    adapter: AgentAdapter,
    requested_skill_mode: str | None,
    sandbox: str,
) -> dict[str, Any]:
    if isinstance(adapter, CodexCLIAdapter):
        codex_home = _effective_codex_home()
        skill_mode = requested_skill_mode or "host-pinned"
        if skill_mode not in {"host-pinned", "off"}:
            raise ValueError(f"unsupported Codex skill mode: {skill_mode}")
        skill_digest = (
            _optional_tree_hash(codex_home / "skills")
            if skill_mode == "host-pinned"
            else None
        )
        return {
            "install": _codex_install_fingerprint(adapter.binary),
            "native_home": {
                "kind": "codex",
                **_codex_behavior_fingerprint(codex_home),
            },
            "native_config": {
                "policy": "ctfh-minimal-v1",
                "digest": adapter.materialized_config_digest(),
            },
            "skills": {
                "mode": skill_mode,
                "digest": skill_digest,
                "read_only": True,
                "materializer": (
                    "codex-native-copy-v1"
                    if skill_mode == "host-pinned"
                    else "none"
                ),
            },
            "permissions": (
                "worker-environment-bwrap-isolation;"
                f"native-agent-inner-sandbox={sandbox}"
            ),
            "web_search": "native-config-disabled",
        }

    if isinstance(adapter, ClaudeCLIAdapter):
        skill_mode = requested_skill_mode or "off"
        if skill_mode != "off":
            raise ValueError("Claude safe-mode adapter requires skills=off")
        claude_home = _effective_claude_home()
        return {
            "install": _generic_install_fingerprint(adapter.binary),
            "native_home": {
                "kind": "claude",
                "source_path": str(claude_home),
                "files": {},
                "digest": _stable_hash({}),
            },
            "native_config": {
                "policy": adapter.native_config_policy,
                "digest": adapter.native_config_digest(),
            },
            "skills": {
                "mode": "off",
                "digest": None,
                "read_only": True,
                "materializer": "none",
            },
            "permissions": (
                "worker-environment-bwrap-isolation;"
                "claude-permission-mode=bypassPermissions;"
                "claude-safe-mode"
            ),
            "web_search": "tool-list-excluded",
        }

    raise TypeError(f"unsupported agent adapter: {type(adapter).__name__}")


FROZEN_KNOWLEDGE_GUIDANCE = (
    "\n\nA pinned read-only knowledge snapshot is available at "
    "$CTFH_KNOWLEDGE_DIR (/ctfh-knowledge). Consult it only when relevant; "
    "do not modify it."
)


def resolve_knowledge_snapshot(
    snapshot_dir: Path | None,
) -> tuple[Path | None, str, str | None]:
    if snapshot_dir is None:
        return None, "off", None
    source = snapshot_dir.resolve()
    if not source.is_dir():
        raise ValueError(f"knowledge snapshot is not a directory: {source}")
    return source, "frozen", tree_snapshot_digest(source)


def apply_knowledge_prompt(prompt: str, memory_mode: str) -> str:
    if memory_mode == "off":
        return prompt
    if memory_mode == "frozen":
        return prompt.rstrip() + FROZEN_KNOWLEDGE_GUIDANCE
    raise ValueError(f"unsupported memory mode: {memory_mode}")


def build_runlock(
    *,
    treatment: str,
    task: TaskSpec,
    adapter: AgentAdapter,
    model: str,
    reasoning_effort: str,
    sandbox: str,
    deadline_seconds: float,
    prompt: str,
    base_prompt: str | None = None,
    admitted_capabilities: tuple[str, ...] = ("native-shell", "workspace-files"),
    skill_mode: str | None = None,
    memory_mode: str = "off",
    knowledge_snapshot_digest: str | None = None,
    environment_services: dict[str, str] | None = None,
    browser_allowed_domains: tuple[str, ...] = (),
) -> RunLock:
    adapter_profile = _adapter_runtime_profile(adapter, skill_mode, sandbox)
    if memory_mode not in {"off", "frozen"}:
        raise ValueError(f"unsupported memory mode: {memory_mode}")
    if memory_mode == "off" and knowledge_snapshot_digest is not None:
        raise ValueError("memory=off cannot include a knowledge snapshot")
    if memory_mode == "frozen" and not knowledge_snapshot_digest:
        raise ValueError("memory=frozen requires a knowledge snapshot digest")

    if base_prompt is None:
        if (
            memory_mode == "frozen"
            and prompt.endswith(FROZEN_KNOWLEDGE_GUIDANCE)
        ):
            base_prompt_value = prompt[: -len(FROZEN_KNOWLEDGE_GUIDANCE)]
        else:
            base_prompt_value = prompt
    else:
        base_prompt_value = base_prompt

    if memory_mode == "off":
        if prompt != base_prompt_value:
            raise ValueError(
                "memory=off effective prompt must equal the base prompt"
            )
        prompt_policy = "none"
    else:
        expected_prompt = apply_knowledge_prompt(
            base_prompt_value,
            memory_mode,
        )
        if prompt != expected_prompt:
            raise ValueError(
                "frozen knowledge prompt does not match the fixed guidance policy"
            )
        prompt_policy = "frozen-knowledge-guidance-v1"

    services = dict(sorted((environment_services or {
        "interactive-session": "disabled",
        "browser": "disabled",
    }).items()))
    base = {
        "task_id": task.task_id,
        "task_public_hash": _tree_hash(task.public_dir),
        "target_policy": "local-v0-persisted-epoch",
        "worker_environment": "local-bwrap-pidns-v0",
        "agent": {
            "adapter": adapter.name,
            "adapter_version": adapter.version(),
            "install": adapter_profile["install"],
            "native_home": adapter_profile["native_home"],
            "model": model,
            "reasoning_effort": reasoning_effort,
            "sandbox": sandbox,
            "ephemeral": True,
            "native_config": adapter_profile["native_config"],
        },
        "permissions": adapter_profile["permissions"],
        "capabilities": {
            "policy": "static-narrow-only-v3",
            "admitted": sorted(set(admitted_capabilities)),
        },
        "skills": adapter_profile["skills"],
        "memory": {
            "mode": memory_mode,
            "knowledge_snapshot_digest": knowledge_snapshot_digest,
            "native_memory_isolated": True,
            "read_only": True,
            "materializer": (
                "snapshot-copy-v1" if memory_mode == "frozen" else "none"
            ),
        },
        "environment_services": services,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "prompt_base_sha256": hashlib.sha256(
            base_prompt_value.encode()
        ).hexdigest(),
        "prompt_policy": prompt_policy,
        "web_search": adapter_profile["web_search"],
        "browser": services.get("browser", "disabled"),
        "browser_policy": {
            "allowed_domains": sorted(set(browser_allowed_domains)),
            "profile": "disposable-attempt-local",
            "cross_attempt_reuse": False,
        },
        "trace": {
            "manifest_schema": TRACE_MANIFEST_SCHEMA,
            "agent_coverage": adapter.capabilities().trace_coverage,
            "bulk_path": "attempt-private-files",
            "kernel_sqlite_hot_path": False,
        },
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
    if path.exists():
        raise FileExistsError(f"runlock is write-once: {path}")

    payload = (
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
    ).encode()
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temp.open("xb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        temp.chmod(0o444)
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        temp.unlink(missing_ok=True)
