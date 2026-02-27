"""Diff-aware incremental context cache for repair loop iterations.

Tracks file content hashes between iterations of the same task.
Unchanged files are replaced with compact structural stubs to save
LLM tokens, while changed files are sent in full.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Tuple

if TYPE_CHECKING:
    from .context_engine import CodeIndex

# ---------------------------------------------------------------------------
# Language detection (mirrors context_engine._detect_language)
# ---------------------------------------------------------------------------

_LANGUAGE_MAP: Dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
}


def _detect_language(path: str) -> str:
    _, ext = os.path.splitext(path)
    return _LANGUAGE_MAP.get(ext.lower(), "unknown")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileSnapshot:
    """Hash and metadata for a single file at a specific iteration."""

    content_hash: str  # SHA-256 hex digest
    char_count: int
    line_count: int
    symbols: List[str]  # ["class Foo", "def bar"] from CodeIndex
    imports: List[str]  # ["os", "pathlib"] from CodeIndex
    language: str


@dataclass
class CacheSavings:
    """Metrics for a single ``record_and_transform`` call."""

    files_total: int = 0
    files_unchanged: int = 0
    files_changed: int = 0
    files_new: int = 0
    chars_original: int = 0
    chars_actual: int = 0

    @property
    def chars_saved(self) -> int:
        return self.chars_original - self.chars_actual

    @property
    def savings_pct(self) -> float:
        if self.chars_original <= 0:
            return 0.0
        return (self.chars_saved / self.chars_original) * 100.0

    @classmethod
    def empty(cls) -> CacheSavings:
        return cls()

    def accumulate(self, other: CacheSavings) -> None:
        """Add another savings record into this one (in-place)."""
        self.files_total += other.files_total
        self.files_unchanged += other.files_unchanged
        self.files_changed += other.files_changed
        self.files_new += other.files_new
        self.chars_original += other.chars_original
        self.chars_actual += other.chars_actual


# ---------------------------------------------------------------------------
# Incremental context cache
# ---------------------------------------------------------------------------


class IncrementalContextCache:
    """Diff-aware context cache for repair loop iterations.

    On the **first** call for a task, returns files unchanged and records
    content hashes.  On subsequent calls, files whose content hash has not
    changed are replaced with a compact stub (structural summary or simple
    line-count indicator), saving LLM tokens.

    Parameters
    ----------
    code_index:
        Optional ``CodeIndex`` for extracting symbol/import info for stubs.
    enabled:
        Set to ``False`` to disable all transformation (pass-through mode).
    stub_format:
        ``"structural"`` (default) includes symbol/import info.
        ``"hash_only"`` produces a minimal line-count stub.
    """

    def __init__(
        self,
        code_index: CodeIndex | None = None,
        enabled: bool = True,
        stub_format: str = "structural",
    ) -> None:
        self._code_index = code_index
        self._enabled = enabled
        self._stub_format = stub_format
        # task_id → list of per-iteration snapshot dicts
        self._iterations: Dict[str, List[Dict[str, FileSnapshot]]] = {}
        self._cumulative = CacheSavings()

    # -- Public API ---------------------------------------------------------

    def record_and_transform(
        self,
        task_id: str,
        files_context: Dict[str, str],
    ) -> Tuple[Dict[str, str], CacheSavings]:
        """Record current file state and return (possibly stubbed) context.

        Returns
        -------
        (transformed_context, savings)
            ``transformed_context`` maps filepath → content (full or stub).
            ``savings`` reports how many chars/files were saved.
        """
        if not self._enabled or not files_context:
            return dict(files_context), CacheSavings.empty()

        # Build snapshots for current files
        current_snapshots: Dict[str, FileSnapshot] = {}
        for path, content in files_context.items():
            current_snapshots[path] = self._make_snapshot(path, content)

        # Get previous iteration (if any)
        previous = self._get_last_iteration(task_id)

        savings = CacheSavings(files_total=len(files_context))
        result: Dict[str, str] = {}

        for path, content in files_context.items():
            snap = current_snapshots[path]
            original_chars = len(content)
            savings.chars_original += original_chars

            if previous is not None and path in previous:
                prev_snap = previous[path]
                if snap.content_hash == prev_snap.content_hash:
                    # UNCHANGED — replace with stub
                    stub = self._build_stub(path, snap)
                    result[path] = stub
                    savings.files_unchanged += 1
                    savings.chars_actual += len(stub)
                    continue
                else:
                    # CHANGED — full content
                    savings.files_changed += 1
            else:
                # NEW file (first iteration or new file)
                savings.files_new += 1

            result[path] = content
            savings.chars_actual += original_chars

        # Record this iteration
        self._record_iteration(task_id, current_snapshots)

        # Accumulate into cumulative savings
        self._cumulative.accumulate(savings)

        return result, savings

    def invalidate_task(self, task_id: str) -> None:
        """Clear all iteration records for a task."""
        self._iterations.pop(task_id, None)

    def get_cumulative_savings(self) -> CacheSavings:
        """Return running total savings across all tasks and iterations."""
        return self._cumulative

    # -- Internal -----------------------------------------------------------

    def _make_snapshot(self, path: str, content: str) -> FileSnapshot:
        """Build a FileSnapshot from file path and content."""
        content_hash = self._compute_hash(content)
        symbols = self._extract_symbols(path)
        imports = self._extract_imports(path)
        language = _detect_language(path)
        return FileSnapshot(
            content_hash=content_hash,
            char_count=len(content),
            line_count=content.count("\n") + (1 if content and not content.endswith("\n") else 0),
            symbols=symbols,
            imports=imports,
            language=language,
        )

    def _compute_hash(self, content: str) -> str:
        """SHA-256 hex digest of content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _extract_symbols(self, path: str) -> List[str]:
        """Extract symbol descriptions from CodeIndex, if available."""
        if self._code_index is None:
            return []
        meta = self._code_index.files.get(path)
        if meta is None:
            return []
        return [f"{s.kind} {s.name}" for s in meta.symbols[:12]]

    def _extract_imports(self, path: str) -> List[str]:
        """Extract import names from CodeIndex, if available."""
        if self._code_index is None:
            return []
        meta = self._code_index.files.get(path)
        if meta is None:
            return []
        return list(meta.imports[:12])

    def _build_stub(self, path: str, snapshot: FileSnapshot) -> str:
        """Generate a compact stub for an unchanged file."""
        lang_str = snapshot.language if snapshot.language != "unknown" else ""

        if self._stub_format == "hash_only" or not snapshot.symbols:
            # Minimal stub
            parts = [f"[unchanged — {snapshot.line_count} lines"]
            if lang_str:
                parts[0] += f", {lang_str}"
            parts[0] += f", {snapshot.char_count} chars]"
            return parts[0]

        # Structural stub with symbols and imports
        header = f"[unchanged since last iteration — {snapshot.line_count} lines"
        if lang_str:
            header += f", {lang_str}"
        header += "]"

        lines = [header]
        if snapshot.symbols:
            lines.append("exports: " + ", ".join(snapshot.symbols))
        if snapshot.imports:
            lines.append("imports: " + ", ".join(snapshot.imports))
        return "\n".join(lines)

    def _get_last_iteration(
        self, task_id: str
    ) -> Dict[str, FileSnapshot] | None:
        """Return the most recent iteration record for a task, or None."""
        history = self._iterations.get(task_id)
        if not history:
            return None
        return history[-1]

    def _record_iteration(
        self, task_id: str, snapshots: Dict[str, FileSnapshot]
    ) -> None:
        """Append a snapshot dict to the task's iteration history."""
        if task_id not in self._iterations:
            self._iterations[task_id] = []
        self._iterations[task_id].append(snapshots)
