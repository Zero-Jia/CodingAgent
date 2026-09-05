"""Deterministic code chunking for workspace semantic indexing."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from coding_agent.semantic.contracts import CodeChunk
from coding_agent.workspace.security import WorkspacePathPolicy

LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".pyi": "Python",
    ".md": "Markdown",
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".ini": "INI",
    ".cfg": "Config",
    ".txt": "Text",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".css": "CSS",
    ".html": "HTML",
    ".sql": "SQL",
}

_PY_SYMBOL = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)")
_JS_SYMBOL = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_]\w*)")


@dataclass(frozen=True)
class ChunkingConfig:
    max_file_bytes: int = 800_000
    max_chunk_chars: int = 6_000
    overlap_chars: int = 600


class WorkspaceCodeChunker:
    """Builds semantic chunks from safe, bounded workspace text files."""

    def __init__(self, workspace: Path, config: ChunkingConfig) -> None:
        self.workspace = workspace.resolve()
        self.config = config
        self.paths = WorkspacePathPolicy(self.workspace)
        self.workspace_id = _sha256_text(str(self.workspace).lower())[:24]

    def chunks(self) -> tuple[list[CodeChunk], int, int]:
        chunks: list[CodeChunk] = []
        indexed_files = 0
        skipped_files = 0
        for path in self._candidate_files():
            file_chunks = self._file_chunks(path)
            if not file_chunks:
                skipped_files += 1
                continue
            indexed_files += 1
            chunks.extend(file_chunks)
        return chunks, indexed_files, skipped_files

    def _candidate_files(self) -> Iterator[Path]:
        for root, directories, names in os.walk(self.workspace, followlinks=False):
            current = Path(root)
            directories[:] = [
                name
                for name in directories
                if not (current / name).is_symlink()
                and not self.paths.is_excluded_from_snapshot(current / name)
            ]
            for name in sorted(names):
                path = current / name
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.suffix.lower() in LANGUAGE_BY_SUFFIX
                    and not self.paths.is_protected(path)
                ):
                    yield path

    def _file_chunks(self, path: Path) -> list[CodeChunk]:
        try:
            size = path.stat().st_size
        except OSError:
            return []
        if size <= 0 or size > self.config.max_file_bytes:
            return []
        try:
            data = path.read_bytes()
        except OSError:
            return []
        if b"\x00" in data[:8192]:
            return []
        text = data.decode("utf-8", errors="replace")
        if not text.strip():
            return []
        relative = path.relative_to(self.workspace).as_posix()
        language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "Text")
        file_hash = hashlib.sha256(data).hexdigest()
        lines = text.splitlines()
        return [
            CodeChunk(
                chunk_id=_chunk_id(self.workspace_id, relative, file_hash, ordinal),
                workspace_id=self.workspace_id,
                path=relative,
                language=language,
                symbol=_symbol_for(lines, language, start_line),
                start_line=start_line,
                end_line=end_line,
                content=content,
                content_hash=_sha256_text(content),
                file_hash=file_hash,
            )
            for ordinal, start_line, end_line, content in _line_chunks(
                lines, self.config.max_chunk_chars, self.config.overlap_chars
            )
        ]


def _line_chunks(
    lines: list[str], max_chars: int, overlap_chars: int
) -> Iterator[tuple[int, int, int, str]]:
    ordinal = 0
    index = 0
    while index < len(lines):
        start = index
        total = 0
        while index < len(lines):
            line_chars = len(lines[index]) + 1
            if index > start and total + line_chars > max_chars:
                break
            total += line_chars
            index += 1
        content = "\n".join(lines[start:index]).strip()
        if content:
            ordinal += 1
            yield ordinal, start + 1, index, content
        if index >= len(lines):
            break
        index = _overlap_start(lines, start, index, overlap_chars)


def _overlap_start(lines: list[str], start: int, end: int, overlap_chars: int) -> int:
    if overlap_chars <= 0:
        return end
    total = 0
    cursor = end
    while cursor > start:
        next_total = total + len(lines[cursor - 1]) + 1
        if next_total > overlap_chars:
            break
        total = next_total
        cursor -= 1
    return max(cursor, start + 1)


def _symbol_for(lines: list[str], language: str, start_line: int) -> str:
    pattern = _PY_SYMBOL if language == "Python" else _JS_SYMBOL
    if language not in {"Python", "TypeScript", "JavaScript"}:
        return ""
    start = max(0, start_line - 1)
    for line in reversed(lines[: start + 1]):
        match = pattern.match(line)
        if match:
            return match.group(1)
    return ""


def _chunk_id(workspace_id: str, path: str, file_hash: str, ordinal: int) -> str:
    return _sha256_text(f"{workspace_id}:{path}:{file_hash}:{ordinal}")[:32]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
