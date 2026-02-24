#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().with_name("perf_validation.py")
    spec = importlib.util.spec_from_file_location("perf_validation", str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load perf_validation module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = _load_module()
    argv = sys.argv[1:]
    return module.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
