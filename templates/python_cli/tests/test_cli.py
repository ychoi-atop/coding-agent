# ruff: noqa: S101

from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app.cli import build_parser, main, _cached_greeting_outputs


def test_cli_contract_structure_and_examples(capsys):
    contract = json.loads(Path("contracts/cli_contract.json").read_text(encoding="utf-8"))

    p = build_parser()
    assert p.prog == contract.get("prog", p.prog)

    flag_to_action = {}
    for a in p._actions:
        for opt in a.option_strings:
            flag_to_action[opt] = a

    for a in contract.get("args", []):
        flag = a["flag"]
        assert flag in flag_to_action, f"Missing CLI flag in parser: {flag}"
        action = flag_to_action[flag]
        if "default" in a:
            assert action.default == a["default"], f"Default mismatch for {flag}"

    for ex in contract.get("examples", []):
        argv = ex.get("argv", [])
        expected_rc = ex.get("exit_code", 0)
        expected_out = ex.get("stdout", "")
        expected_err = ex.get("stderr_contains")

        rc = main(argv)
        captured = capsys.readouterr()
        assert rc == expected_rc
        if expected_out:
            assert captured.out == expected_out
        if expected_err:
            assert captured.err and expected_err in captured.err


def test_cli_error_and_fallback_paths(capsys):
    rc = main(["--repeat", "0"])
    err = capsys.readouterr()
    assert rc == 2
    assert "--repeat must be between 1 and 3" in err.err

    rc = main([])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out == "hello world\n"


def test_cli_greeting_cache_is_reused():
    first = _cached_greeting_outputs("agent", 2)
    second = _cached_greeting_outputs("agent", 2)
    assert first == second
    assert first is second
