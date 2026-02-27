"""Template Registry — discovers template directories and reads their manifests.

Each template directory may contain a ``manifest.json`` describing the template's
language, runtime, supported validators, and build/lint/test commands.
Templates without a manifest are treated as Python templates for backward compatibility.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class TemplateManifest:
    """Parsed manifest for one project template."""

    name: str
    language: str
    runtime: str
    scaffold_files: List[str] = field(default_factory=list)
    validators: List[str] = field(default_factory=list)
    test_command: str = ""
    lint_command: str = ""
    type_check_command: str = ""
    build_command: str = ""
    allowed_executables: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "TemplateManifest":
        return cls(
            name=name,
            language=data.get("language", "python"),
            runtime=data.get("runtime", "cpython"),
            scaffold_files=data.get("scaffold_files", []),
            validators=data.get("validators", []),
            test_command=data.get("test_command", ""),
            lint_command=data.get("lint_command", ""),
            type_check_command=data.get("type_check_command", ""),
            build_command=data.get("build_command", ""),
            allowed_executables=data.get("allowed_executables", []),
        )


# Default manifest for legacy Python templates without a manifest.json.
_PYTHON_DEFAULT_MANIFEST = {
    "language": "python",
    "runtime": "cpython",
    "validators": ["ruff", "mypy", "pytest", "pip_audit", "bandit", "semgrep", "sbom", "docker_build"],
}


class TemplateRegistry:
    """Discovers and indexes templates from a root directory.

    Usage::

        registry = TemplateRegistry("/path/to/templates")
        names = registry.list_templates()     # ["python_fastapi", "python_cli", ...]
        manifest = registry.get("python_fastapi")
    """

    def __init__(self, template_root: str):
        self.root = template_root
        self._manifests: Dict[str, TemplateManifest] = {}
        self._discover()

    def _discover(self) -> None:
        if not os.path.isdir(self.root):
            return
        for entry in sorted(os.listdir(self.root)):
            if entry.startswith("_") or entry.startswith("."):
                continue
            entry_path = os.path.join(self.root, entry)
            if not os.path.isdir(entry_path):
                continue
            manifest_path = os.path.join(entry_path, "manifest.json")
            if os.path.isfile(manifest_path):
                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._manifests[entry] = TemplateManifest.from_dict(entry, data)
            else:
                # Backward compatibility: Python templates without manifest.
                self._manifests[entry] = TemplateManifest.from_dict(entry, _PYTHON_DEFAULT_MANIFEST)

    def list_templates(self) -> List[str]:
        """Return sorted list of discovered template names."""
        return sorted(self._manifests.keys())

    def get(self, name: str) -> TemplateManifest | None:
        """Return the manifest for *name*, or ``None`` if not found."""
        return self._manifests.get(name)

    def exists(self, name: str) -> bool:
        return name in self._manifests

    def template_dir(self, name: str) -> str:
        """Return the absolute path to the template directory."""
        return os.path.join(self.root, name)
