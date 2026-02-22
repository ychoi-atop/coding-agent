from __future__ import annotations

import json
from typing import Any

from jsonschema import validate

from .exec_kernel import ExecKernel
from .llm_client import LLMClient
from .prd_parser import PRDStruct
from .roles import prompts
from .schemas import ROLE_SCHEMA
from .validators import Validators
from .workspace import FileWrite, Workspace


def _json_load(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def run_enterprise_autodev(
    client: LLMClient,
    ws: Workspace,
    template_dir: str,
    prd_struct: PRDStruct,
    validators_enabled: list[str],
    max_fix_loops: int,
    max_role_retries: int = 1,
) -> dict[str, Any]:
    forbidden_paths = {"ruff.py", "mypy.py", "pip_audit.py", "bandit.py", "docker"}
    ws.apply_template(template_dir)

    prompt_map = prompts()
    role: str | None = "planner"
    fix_loops = 0
    last_validation: list[dict[str, Any]] | None = None

    kernel = ExecKernel(cwd=ws.root, timeout_sec=900)
    validators = Validators(kernel)

    while role is not None:
        file_list = ws.list_files()
        data: dict[str, Any] | None = None
        retry_error: str | None = None
        last_raw: str = ""

        for attempt in range(max_role_retries + 1):
            user_payload = {
                "prd_struct": prd_struct.__dict__,
                "repo_files": file_list[:200],
                "last_validation": last_validation,
                "task": prompt_map[role]["task"],
                "role_retry_error": retry_error,
                "role_retry_attempt": attempt,
            }

            last_raw = await client.chat(
                _messages(
                    prompt_map[role]["system"],
                    json.dumps(user_payload, ensure_ascii=False),
                ),
                temperature=0.2,
            )
            try:
                candidate = _json_load(last_raw)
                validate(instance=candidate, schema=ROLE_SCHEMA)
                data = candidate
                break
            except Exception as exc:
                retry_error = f"{type(exc).__name__}: {exc}"

        if data is None:
            return {
                "ok": False,
                "reason": "invalid_role_output",
                "role": role,
                "error": retry_error,
                "raw_tail": last_raw[-4000:],
                "last_validation": last_validation,
            }

        writes = [FileWrite(path=item["path"], content=item["content"]) for item in data["writes"]]
        invalid_paths = [
            write.path
            for write in writes
            if write.path.strip().lstrip("./").replace("\\", "/") in forbidden_paths
        ]
        if invalid_paths:
            return {
                "ok": False,
                "reason": "forbidden_generated_file",
                "role": role,
                "paths": invalid_paths,
                "last_validation": last_validation,
            }
        ws.write_files(writes)
        role = data["next_role"]

        if role == "validator":
            results = validators.run_all(validators_enabled)
            last_validation = [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "cmd": r.result.cmd,
                    "returncode": r.result.returncode,
                    "stdout": r.result.stdout[-6000:],
                    "stderr": r.result.stderr[-6000:],
                }
                for r in results
            ]
            if all(result["ok"] for result in last_validation):
                role = None
            else:
                role = "fixer"
                fix_loops += 1
                if fix_loops > max_fix_loops:
                    return {
                        "ok": False,
                        "reason": "max_fix_loops_exceeded",
                        "last_validation": last_validation,
                    }

    return {"ok": True, "last_validation": last_validation}
