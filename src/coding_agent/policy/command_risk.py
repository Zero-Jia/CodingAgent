"""High-confidence command risk detection before sandbox execution."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal

CommandRiskLevel = Literal["normal", "suspicious", "dangerous"]


@dataclass(frozen=True)
class CommandRisk:
    level: CommandRiskLevel
    reason: str = ""


_DANGEROUS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)(^|[;&|]\s*)rm\s+-(?=[\w-]*r)(?=[\w-]*f)[\w-]*\s+"
            r"(?:--\s+)?/\s*($|[;&|])"
        ),
        "recursive forced deletion of filesystem root",
    ),
    (
        re.compile(
            r"(?i)(^|[;&|]\s*)rm\s+-(?=[\w-]*r)(?=[\w-]*f)[\w-]*\s+"
            r"(?:--\s+)?\.\s*($|[;&|])"
        ),
        "recursive forced deletion of the sandbox workspace root",
    ),
    (
        re.compile(
            r"(?i)(^|[;&|]\s*)rm\s+-(?=[\w-]*r)(?=[\w-]*f)[\w-]*\s+"
            r"(?:--\s+)?\*\s*($|[;&|])"
        ),
        "recursive forced deletion of all files in the sandbox workspace",
    ),
    (re.compile(r"(?i)(^|[;&|]\s*)mkfs(?:\.|\s)"), "filesystem formatting command"),
    (
        re.compile(r"(?i)(^|[;&|]\s*)dd\s+[^;&|]*\bof=/dev/(?:sd|vd|xvd|nvme|hd)"),
        "raw write to a block device",
    ),
    (
        re.compile(r"(?i)>\s*/dev/(?:sd|vd|xvd|nvme|hd)"),
        "output redirection to a block device",
    ),
    (
        re.compile(r"(?i)(^|[;&|]\s*)chmod\s+-R\s+777\s+/(\s*$|\s|[;&|])"),
        "recursive world-writable permissions on filesystem root",
    ),
    (
        re.compile(r"(?i)(^|[;&|]\s*)chown\s+-R\s+\S+\s+/(\s*$|\s|[;&|])"),
        "recursive ownership change on filesystem root",
    ),
    (re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:"), "fork bomb"),
    (
        re.compile(r"(?i)\b(?:curl|wget)\b[^;&]*\|\s*(?:ba)?sh\b"),
        "remote script piped directly into a shell",
    ),
    (
        re.compile(r"(?i)\b(?:ba)?sh\s*<\s*\(\s*(?:curl|wget)\b"),
        "remote script executed through process substitution",
    ),
    (
        re.compile(r"(?i)(^|[;&|]\s*)git\s+reset\s+--hard(?:\s|$)"),
        "hard reset would discard workspace changes in the sandbox snapshot",
    ),
    (
        re.compile(r"(?i)(^|[;&|]\s*)git\s+clean\s+-(?=[\w-]*x)(?=[\w-]*d)(?=[\w-]*f)[\w-]*(?:\s|$)"),
        "git clean -xdf can delete untracked workspace files in the sandbox snapshot",
    ),
    (
        re.compile(r"(?i)(^|[;&|]\s*)find\s+\.\s+[^;&|]*-delete(?:\s|$|[;&|])"),
        "find . -delete can remove large portions of the sandbox workspace",
    ),
)

_SUSPICIOUS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b(?:curl|wget)\b"),
        "network download command; sandbox networking is currently disabled "
        "but this should be reviewed",
    ),
    (
        re.compile(r"(?i)\b(?:npm|pnpm|yarn|pip|uv|cargo|go)\s+(?:install|add|get)\b"),
        "dependency installation command should be reviewed before sandbox execution",
    ),
)


class CommandRiskDetector:
    """Classifies shell commands before they are sent to the Docker sandbox.

    This is intentionally a high-confidence detector, not a complete shell
    parser. Docker isolation and patch validation remain the primary boundary;
    this layer blocks clearly dangerous commands and escalates ambiguous ones.
    """

    def evaluate(self, command: str) -> CommandRisk:
        normalised = _normalise(command)
        if not normalised:
            return CommandRisk("normal")
        for pattern, reason in _DANGEROUS_PATTERNS:
            if pattern.search(normalised):
                return CommandRisk("dangerous", reason)
        for segment in _segments(normalised):
            segment_risk = _segment_risk(segment)
            if segment_risk.level != "normal":
                return segment_risk
        for pattern, reason in _SUSPICIOUS_PATTERNS:
            if pattern.search(normalised):
                return CommandRisk("suspicious", reason)
        return CommandRisk("normal")


def _normalise(command: str) -> str:
    collapsed_lines = command.replace("\\\r\n", " ").replace("\\\n", " ")
    return re.sub(r"\s+", " ", collapsed_lines.strip())


def _segments(command: str) -> list[str]:
    return [
        segment.strip()
        for segment in re.split(r"\s*(?:&&|\|\||[;|])\s*", command)
        if segment.strip()
    ]


def _segment_risk(segment: str) -> CommandRisk:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        tokens = segment.split()
    if not tokens:
        return CommandRisk("normal")
    command = _first_command(tokens)
    if command is None:
        return CommandRisk("normal")
    lowered = [token.lower() for token in tokens]
    if command == "rm" and _has_recursive_force(lowered) and _touches_workspace_root(lowered):
        return CommandRisk("dangerous", "recursive forced deletion of the sandbox workspace")
    if command == "git" and len(lowered) >= 3:
        if lowered[1:3] == ["reset", "--hard"]:
            return CommandRisk(
                "dangerous", "hard reset would discard workspace changes in the sandbox snapshot"
            )
        if lowered[1] == "clean" and any(_is_git_clean_xdf(token) for token in lowered[2:]):
            return CommandRisk(
                "dangerous",
                "git clean -xdf can delete untracked workspace files in the sandbox snapshot",
            )
    return CommandRisk("normal")


def _first_command(tokens: list[str]) -> str | None:
    for token in tokens:
        if re.fullmatch(r"[A-Za-z_]\w*=.*", token):
            continue
        return token.rsplit("/", maxsplit=1)[-1].lower()
    return None


def _has_recursive_force(tokens: list[str]) -> bool:
    return any(token.startswith("-") and "r" in token and "f" in token for token in tokens)


def _touches_workspace_root(tokens: list[str]) -> bool:
    return any(token in {"/", ".", "./", "*", "./*"} for token in tokens)


def _is_git_clean_xdf(token: str) -> bool:
    return token.startswith("-") and all(flag in token for flag in ("x", "d", "f"))
