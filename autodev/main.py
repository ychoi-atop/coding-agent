from __future__ import annotations
import argparse
import os
import re
from datetime import datetime
from pathlib import Path
from .config import load_config
from .llm_client import LLMClient
from .workspace import Workspace
from .loop import run_autodev_enterprise
from .report import write_report

def _slugify_prd_stem(prd_path: str) -> str:
    stem = Path(prd_path).stem.strip()
    if not stem:
        return "prd"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return slug or "prd"

def _resolve_output_dir(prd_path: str, out_root: str) -> str:
    prd_slug = _slugify_prd_stem(prd_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(out_root).expanduser()
    candidate = root / f"{prd_slug}_{ts}"
    suffix = 1
    while candidate.exists():
        candidate = root / f"{prd_slug}_{ts}_{suffix:02d}"
        suffix += 1
    return str(candidate)

def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prd", required=True)
    ap.add_argument(
        "--out",
        required=True,
        help="Output root directory. A run folder named '<prd-file-stem>_<timestamp>' is created inside it.",
    )
    ap.add_argument("--profile", default="enterprise")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    prof = cfg["profiles"][args.profile]

    with open(args.prd, "r", encoding="utf-8") as f:
        prd_md = f.read()

    llm_cfg = cfg["llm"]
    client = LLMClient(
        base_url=llm_cfg["base_url"],
        api_key=llm_cfg.get("api_key",""),
        model=llm_cfg["model"],
        timeout_sec=int(llm_cfg.get("timeout_sec", 240)),
    )

    run_out = _resolve_output_dir(args.prd, args.out)
    ws = Workspace(run_out)
    template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates"))

    import asyncio
    ok, prd_struct, plan, last_validation = asyncio.run(
        run_autodev_enterprise(
            client=client,
            ws=ws,
            prd_markdown=prd_md,
            template_root=template_root,
            template_candidates=prof["template_candidates"],
            validators_enabled=prof["validators"],
            audit_required=bool(prof.get("security", {}).get("audit_required", False)),
            max_fix_loops_total=int(cfg["run"].get("max_fix_loops_total", 10)),
            max_fix_loops_per_task=int(cfg["run"].get("max_fix_loops_per_task", 4)),
            max_json_repair=int(cfg["run"].get("max_json_repair", 2)),
            verbose=bool(cfg["run"].get("verbose", True)),
        )
    )

    write_report(ws.root, prd_struct, plan, last_validation, ok)
    print({"ok": ok, "out": os.path.abspath(run_out)})
    if not ok:
        raise SystemExit(1)

if __name__ == "__main__":
    cli()
