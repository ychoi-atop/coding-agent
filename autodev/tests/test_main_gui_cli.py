from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autodev.main as main
import autodev.gui_mvp_server as gui_server


def test_cli_dispatches_gui_subcommand(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_serve(host: str, port: int, runs_root: Path) -> None:
        captured["host"] = host
        captured["port"] = port
        captured["runs_root"] = runs_root

    monkeypatch.setattr(gui_server, "serve", _fake_serve)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autodev",
            "gui",
            "--host",
            "0.0.0.0",
            "--port",
            "9898",
            "--runs-root",
            "generated_runs",
        ],
    )

    main.cli()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9898
    assert captured["runs_root"] == tmp_path / "generated_runs"
