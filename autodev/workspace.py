from __future__ import annotations
import os, shutil
from dataclasses import dataclass
from typing import List
from .patch_utils import apply_unified_diff

@dataclass
class Change:
    op: str   # write|delete|patch
    path: str
    content: str | None = None

class Workspace:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def apply_template(self, template_dir: str) -> None:
        if not os.path.isdir(template_dir):
            raise FileNotFoundError(f"Template not found: {template_dir}")
        for base, _, files in os.walk(template_dir):
            rel = os.path.relpath(base, template_dir)
            dest_base = self.root if rel == "." else os.path.join(self.root, rel)
            os.makedirs(dest_base, exist_ok=True)
            for fn in files:
                src = os.path.join(base, fn)
                dst = os.path.join(dest_base, fn)
                if os.path.exists(dst):
                    continue
                shutil.copy2(src, dst)

    def _abs(self, rel_path: str) -> str:
        # strip leading slash so os.path.join treats it strictly as relative
        rel_path = rel_path.lstrip("/\\")
        abs_path = os.path.abspath(os.path.join(self.root, rel_path))
        try:
            common = os.path.commonpath([self.root, abs_path])
        except ValueError as exc:
            raise ValueError(f"Invalid path: {rel_path}") from exc
        if common != self.root:
            raise ValueError(f"Path escapes workspace root: {rel_path}")
        return abs_path

    def write_text(self, rel_path: str, content: str) -> None:
        abs_path = self._abs(rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

    def delete(self, rel_path: str) -> None:
        abs_path = self._abs(rel_path)
        if os.path.exists(abs_path):
            os.remove(abs_path)

    def read_text(self, rel_path: str) -> str:
        abs_path = self._abs(rel_path)
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read()

    def exists(self, rel_path: str) -> bool:
        return os.path.exists(self._abs(rel_path))

    def list_files(self, max_files: int = 1200) -> List[str]:
        out: List[str] = []
        for base, _, files in os.walk(self.root):
            for fn in files:
                rel = os.path.relpath(os.path.join(base, fn), self.root)
                out.append(rel)
                if len(out) >= max_files:
                    return sorted(out)
        return sorted(out)

    def apply_changes(self, changes: List[Change]) -> None:
        for c in changes:
            if c.op == "write":
                if c.content is None:
                    raise ValueError("write op requires content")
                self.write_text(c.path, c.content)
            elif c.op == "delete":
                self.delete(c.path)
            elif c.op == "patch":
                if c.content is None:
                    raise ValueError("patch op requires content(diff)")
                if not self.exists(c.path):
                    # fallback: if patch is requested on missing file, treat as write (full rewrite)
                    updated = apply_unified_diff("", c.content)
                    self.write_text(c.path, updated)
                else:
                    original = self.read_text(c.path)
                    updated = apply_unified_diff(original, c.content)
                    self.write_text(c.path, updated)
            else:
                raise ValueError(f"Unknown op: {c.op}")
