#!/usr/bin/env python3
"""Parity and drift checks for generated artifact + template workflows/docs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _norm_cmd(cmd: str) -> str:
    return " ".join(cmd.strip().split())


def _extract_run_commands(workflow_text: str) -> list[str]:
    cmds: list[str] = []
    for line in workflow_text.splitlines():
        m = re.match(r"\s*-\s*run:\s*(.+)$", line)
        if m:
            cmds.append(_norm_cmd(m.group(1)))
    return cmds


def _check_python_version(path: Path, text: str, version: str, errors: list[str]) -> None:
    markers = [f'python-version: "{version}"', f"python-version: '{version}'"]
    if not any(marker in text for marker in markers):
        errors.append(f"[ERROR] {path}: missing python-version {version}")


def _check_tool_env(path: Path, text: str, tool_versions: dict[str, str], env_names: dict[str, str], errors: list[str]) -> None:
    for tool, version in tool_versions.items():
        env_name = env_names.get(tool)
        if not env_name:
            continue
        marker = f'{env_name}: "{version}"'
        if marker not in text:
            errors.append(f"[ERROR] {path}: missing pinned env {marker}")


def _check_required_commands(path: Path, cmds: list[str], required: list[str], errors: list[str]) -> None:
    norm_cmds = set(cmds)
    for required_cmd in required:
        if _norm_cmd(required_cmd) not in norm_cmds:
            errors.append(f"[ERROR] {path}: missing required command '{required_cmd}'")


def _check_shared_ci_reference(path: Path, cmds: list[str], shared_cmds: list[str], errors: list[str]) -> None:
    if cmds != shared_cmds:
        errors.append(
            f"[ERROR] {path}: workflow run-command sequence differs from templates/_shared/ci/ci.yml"
        )


def _check_doc_references(path: Path, required_refs: list[str], errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    missing = [ref for ref in required_refs if ref not in text]
    if missing:
        errors.append(f"[ERROR] {path}: missing docs references {', '.join(sorted(missing))}")


def run_check(repo_root: Path) -> int:
    contract_path = repo_root / "docs" / "ops" / "template-validation-contract.json"
    if not contract_path.exists():
        print(f"[ERROR] contract file missing: {contract_path}")
        return 1

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    required_commands = contract.get("required_commands", [])
    python_version = str(contract.get("python_version", ""))
    tool_versions = contract.get("tool_versions", {})
    env_names = contract.get("tool_version_env", {})

    shared_ci = repo_root / "templates" / "_shared" / "ci" / "ci.yml"
    if not shared_ci.exists():
        print(f"[ERROR] shared CI template missing: {shared_ci}")
        return 1

    shared_cmds = _extract_run_commands(shared_ci.read_text(encoding="utf-8"))
    if not shared_cmds:
        print(f"[ERROR] shared CI template has no run commands: {shared_ci}")
        return 1

    checks = {
        "generated_repo/.github/workflows/ci.yml": "generated_repo workflow",
        "templates/python_fastapi/.github/workflows/ci.yml": "template workflow: python_fastapi",
        "templates/python_cli/.github/workflows/ci.yml": "template workflow: python_cli",
    }
    doc_refs = {
        "templates/python_fastapi/README.md": "template docs: python_fastapi",
        "templates/python_cli/README.md": "template docs: python_cli",
    }
    required_docs = [
        "docs/onboarding.md",
        "docs/deployment.md",
        "docs/monitoring.md",
        "docs/failure-handling.md",
    ]

    errors: list[str] = []

    for rel_path_str, label in checks.items():
        path = repo_root / rel_path_str
        if not path.exists():
            errors.append(f"[ERROR] {path}: missing {label}")
            continue

        text = path.read_text(encoding="utf-8")
        cmds = _extract_run_commands(text)

        _check_python_version(path, text, python_version, errors)
        _check_tool_env(path, text, tool_versions, env_names, errors)
        _check_required_commands(path, cmds, required_commands, errors)
        _check_shared_ci_reference(path, cmds, shared_cmds, errors)

    for rel_path_str, label in doc_refs.items():
        path = repo_root / rel_path_str
        if not path.exists():
            errors.append(f"[ERROR] {path}: missing {label}")
            continue
        _check_doc_references(path, required_docs, errors)

    if errors:
        for err in errors:
            print(err)
        return 1

    print("Template parity + drift audit passed.")
    print(
        " - workflow checks: generated project + python_fastapi/python_cli share synced CI command sequence"
    )
    print(" - docs checks: onboarding/deployment/monitoring/failure references present")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check generated template parity and CI/docs drift."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Repo root path. Defaults to current directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_check(Path(args.root).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
