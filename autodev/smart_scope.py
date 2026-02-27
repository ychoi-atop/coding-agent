"""Smart scope validator — narrow validation to actual changes.

After the LLM produces a changeset, this module inspects the *actual*
``Change`` objects to determine which validators are relevant.  This
complements :mod:`adaptive_gate` (which filters by *planned* task files)
by operating on what was **really** modified.

Pure function approach — no side effects, no file writes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from .adaptive_gate import _VALIDATOR_FILE_RELEVANCE
from .workspace import Change


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_VALID_MODES = frozenset({"narrow", "conservative"})


@dataclass(frozen=True)
class SmartScopeConfig:
    """Configuration for smart scope validator narrowing."""

    enabled: bool = False
    mode: str = "narrow"  # "narrow" | "conservative"
    always_run: frozenset[str] = field(default_factory=frozenset)
    test_source_mapping: bool = True


def resolve_smart_scope_config(
    quality_profile: Dict[str, Any] | None,
) -> SmartScopeConfig:
    """Extract smart scope configuration from *quality_profile*.

    Returns a disabled config when the section is absent or malformed.
    """
    if not isinstance(quality_profile, dict):
        return SmartScopeConfig()

    raw = quality_profile.get("smart_scope")
    if not isinstance(raw, dict):
        return SmartScopeConfig()

    enabled = raw.get("enabled", False) is True
    if not enabled:
        return SmartScopeConfig()

    mode = str(raw.get("mode", "narrow"))
    if mode not in _VALID_MODES:
        mode = "narrow"

    always_run_raw = raw.get("always_run", [])
    if isinstance(always_run_raw, list):
        always_run = frozenset(str(v) for v in always_run_raw if isinstance(v, str))
    else:
        always_run = frozenset()

    test_mapping = raw.get("test_source_mapping", True) is not False

    return SmartScopeConfig(
        enabled=True,
        mode=mode,
        always_run=always_run,
        test_source_mapping=test_mapping,
    )


# ---------------------------------------------------------------------------
# Scope result (audit trail)
# ---------------------------------------------------------------------------


@dataclass
class ScopeResult:
    """Audit trail for a single smart-scope application."""

    original_validators: List[str]
    scoped_validators: List[str]
    changed_files: List[str]
    expanded_files: List[str]
    removed_validators: List[str]


# ---------------------------------------------------------------------------
# Change extraction
# ---------------------------------------------------------------------------


def extract_changed_files(changes: List[Change]) -> List[str]:
    """Return deduplicated file paths from *changes* (all op types)."""
    seen: Set[str] = set()
    result: List[str] = []
    for c in changes:
        path = c.path.replace("\\", "/")
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


# ---------------------------------------------------------------------------
# Test ↔ source mapping
# ---------------------------------------------------------------------------


def expand_with_test_mapping(files: List[str]) -> List[str]:
    """Expand file list with bidirectional test ↔ source mapping.

    Rules:
    - ``src/foo.py`` → also consider ``tests/test_foo.py``
    - ``tests/test_foo.py`` → also consider ``foo.py``
    - Only ``.py`` files participate in mapping.
    """
    seen: Set[str] = set(files)
    expanded: List[str] = list(files)

    for fpath in files:
        basename = os.path.basename(fpath)
        _, ext = os.path.splitext(basename)
        if ext.lower() != ".py":
            continue

        counterpart: str | None = None

        if basename.startswith("test_"):
            # test file → source file (basename only, no directory prefix)
            source_name = basename[5:]  # strip "test_"
            counterpart = source_name
        else:
            # source file → test file
            test_name = f"tests/test_{basename}"
            counterpart = test_name

        if counterpart and counterpart not in seen:
            seen.add(counterpart)
            expanded.append(counterpart)

    return expanded


# ---------------------------------------------------------------------------
# Relevance check (reuse adaptive_gate's mapping)
# ---------------------------------------------------------------------------


def _is_relevant(validator_name: str, files: List[str]) -> bool:
    """Check if *validator_name* is relevant to *files*.

    Uses the same ``_VALIDATOR_FILE_RELEVANCE`` mapping from
    :mod:`adaptive_gate`.  Unknown validators are always relevant.
    """
    relevance = _VALIDATOR_FILE_RELEVANCE.get(validator_name)
    if relevance is None:
        return True  # unknown → always relevant

    for fpath in files:
        basename = os.path.basename(fpath)
        _, ext = os.path.splitext(fpath)
        if basename in relevance or ext.lower() in relevance:
            return True
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def apply_smart_scope(
    run_set: List[str],
    changes: List[Change],
    config: SmartScopeConfig,
) -> Tuple[List[str], ScopeResult]:
    """Narrow *run_set* based on actual ``Change`` objects.

    Parameters
    ----------
    run_set:
        Validator names selected by adaptive_gate / _resolve_validators.
    changes:
        Change objects from the LLM-generated changeset.
    config:
        Smart scope configuration.

    Returns
    -------
    (scoped_run_set, result)
        *scoped_run_set* is the narrowed validator list.
        *result* contains the audit trail.

    Behaviour:
    - **disabled** or empty *changes* → passthrough (return *run_set* unchanged).
    - **conservative** mode → only apply ``always_run`` guarantee, no filtering.
    - **narrow** mode → filter by file relevance, keep ``always_run``, safety fallback.
    """
    original = list(run_set)

    # Early exit: disabled or no changes
    if not config.enabled or not changes:
        return list(run_set), ScopeResult(
            original_validators=original,
            scoped_validators=list(run_set),
            changed_files=[],
            expanded_files=[],
            removed_validators=[],
        )

    # Step 1: extract actually changed files
    changed = extract_changed_files(changes)

    # Step 2: expand with test mapping
    if config.test_source_mapping:
        expanded = expand_with_test_mapping(changed)
    else:
        expanded = list(changed)

    # Step 3: conservative mode — no filtering, just ensure always_run
    if config.mode == "conservative":
        scoped = list(run_set)
        # Ensure always_run validators are present
        for v in config.always_run:
            if v not in scoped:
                scoped.append(v)
        return scoped, ScopeResult(
            original_validators=original,
            scoped_validators=scoped,
            changed_files=changed,
            expanded_files=expanded,
            removed_validators=[],
        )

    # Step 4: narrow mode — filter by relevance
    scoped: List[str] = []
    for v in run_set:
        if v in config.always_run:
            scoped.append(v)
        elif _is_relevant(v, expanded):
            scoped.append(v)

    # Step 5: safety — never reduce to empty
    if not scoped:
        scoped = list(run_set)

    removed = [v for v in original if v not in scoped]

    return scoped, ScopeResult(
        original_validators=original,
        scoped_validators=scoped,
        changed_files=changed,
        expanded_files=expanded,
        removed_validators=removed,
    )
