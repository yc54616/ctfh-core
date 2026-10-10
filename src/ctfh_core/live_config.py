"""Checkout-local native launcher selection, separate from global Codex config."""

from dataclasses import dataclass
from pathlib import Path
import os
import tomllib


@dataclass(frozen=True)
class NativeLaunch:
    binary: str = "codex"
    full_access: bool = False

    @property
    def command(self) -> str:
        return "codex --secondary" if self.binary == "codex-secondary" else self.binary


def codex_environment(binary: str) -> dict[str, str]:
    """Keep the ordinary account separate from an invoking secondary shell."""
    environment = dict(os.environ)
    if Path(binary).name == "codex":
        environment["CODEX_HOME"] = str(Path.home() / ".codex")
        environment.pop("CODEX_SQLITE_HOME", None)
    return environment


def native_launch(root: Path, binary: str | None = None) -> NativeLaunch:
    path = root / "ctfh.toml"
    config = tomllib.loads(path.read_text()).get("native", {}) if path.is_file() else {}
    if not isinstance(config, dict):
        raise ValueError("ctfh.toml의 [native] 설정이 올바르지 않습니다.")
    configured = config.get("binary", "codex")
    full_access = config.get("dangerously_bypass_approvals_and_sandbox", False)
    if not isinstance(configured, str) or not configured.strip() or type(full_access) is not bool:
        raise ValueError("ctfh.toml: binary는 실행 파일, dangerously_bypass_approvals_and_sandbox는 불리언이어야 합니다.")
    secondary = config.get("secondary", {})
    if not isinstance(secondary, dict) or type(secondary.get("dangerously_bypass_approvals_and_sandbox", False)) is not bool:
        raise ValueError("ctfh.toml: native.secondary의 dangerously_bypass_approvals_and_sandbox는 불리언이어야 합니다.")
    selected = binary if binary is not None else configured
    if Path(selected).name == "codex-shift":
        raise ValueError("codex-shift 연결은 제거됐습니다. codex 또는 codex-secondary를 선택하세요.")
    # A different explicit CLI binary retains its own defaults; it does not
    # accidentally inherit the selected wrapper's unrestricted configuration.
    if binary is not None and binary != configured:
        if binary == "codex-secondary":
            return NativeLaunch(binary, secondary.get("dangerously_bypass_approvals_and_sandbox", False))
        return NativeLaunch(binary)
    return NativeLaunch(configured, full_access)
