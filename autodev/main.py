from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

from .config import load_config
from .llm_client import LLMClient
from .loop import run_enterprise_autodev
from .prd_parser import parse_prd_markdown
from .report import write_report
from .workspace import Workspace


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prd", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--profile", default="enterprise")
    parser.add_argument("--config", default="config.yaml")
    return parser


def cli() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    cfg: dict[str, Any] = load_config(args.config)
    profile = cfg["profiles"][args.profile]
    validators_enabled = profile["validators"]
    template_name = profile["repo_template"]

    with open(args.prd, "r", encoding="utf-8") as f:
        prd_md = f.read()
    prd_struct = parse_prd_markdown(prd_md)

    llm_cfg = cfg["llm"]
    client = LLMClient(
        base_url=llm_cfg["base_url"],
        api_key=llm_cfg.get("api_key", ""),
        model=llm_cfg["model"],
        timeout_sec=int(llm_cfg.get("timeout_sec", 180)),
        max_tokens=int(llm_cfg["max_tokens"]) if llm_cfg.get("max_tokens") is not None else None,
    )

    workspace = Workspace(args.out)
    template_dir = os.path.join(os.path.dirname(__file__), "..", "templates", template_name)

    async def run() -> dict[str, Any]:
        try:
            result = await run_enterprise_autodev(
                client=client,
                ws=workspace,
                template_dir=os.path.abspath(template_dir),
                prd_struct=prd_struct,
                validators_enabled=validators_enabled,
                max_fix_loops=int(cfg["run"].get("max_fix_loops", 6)),
                max_role_retries=int(cfg["run"].get("max_role_retries", 1)),
            )
        except Exception as exc:
            result = {
                "ok": False,
                "reason": "orchestrator_exception",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        write_report(workspace.root, prd_struct, result)
        return result

    result = asyncio.run(run())
    print(result)
    if not result.get("ok", False):
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
