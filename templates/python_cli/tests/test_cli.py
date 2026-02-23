import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app.cli import build_parser, main

def test_cli_contract_structure_and_examples(capsys):
    contract = json.loads(Path("contracts/cli_contract.json").read_text(encoding="utf-8"))

    # parser-level checks
    p = build_parser()
    assert p.prog == contract.get("prog", p.prog)

    # Collect flags and defaults
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

    # runtime example checks
    for ex in contract.get("examples", []):
        argv = ex.get("argv", [])
        expected_rc = ex.get("exit_code", 0)
        expected_out = ex.get("stdout", "")

        rc = main(argv)
        assert rc == expected_rc
        out = capsys.readouterr().out
        assert out == expected_out
