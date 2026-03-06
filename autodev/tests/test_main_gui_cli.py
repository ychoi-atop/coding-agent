from pathlib import Path
import sys

import pytest

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


def test_cli_dispatches_local_simple_subcommand(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_serve(host: str, port: int, runs_root: Path) -> None:
        captured["host"] = host
        captured["port"] = port
        captured["runs_root"] = runs_root

    monkeypatch.setattr(gui_server, "serve", _fake_serve)
    (tmp_path / "config.yaml").write_text("profiles: {}\n", encoding="utf-8")
    examples = tmp_path / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    (examples / "PRD.md").write_text("# PRD\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTODEV_GUI_ROLE", raising=False)
    monkeypatch.delenv("AUTODEV_GUI_LOCAL_SIMPLE", raising=False)
    monkeypatch.delenv("AUTODEV_GUI_DEFAULT_CONFIG", raising=False)
    monkeypatch.delenv("AUTODEV_GUI_DEFAULT_PRD", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autodev",
            "local-simple",
            "--port",
            "9999",
            "--runs-root",
            "generated_runs",
        ],
    )

    main.cli()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9999
    assert captured["runs_root"] == tmp_path / "generated_runs"
    assert main.os.environ["AUTODEV_GUI_ROLE"] == "developer"
    assert main.os.environ["AUTODEV_GUI_LOCAL_SIMPLE"] == "1"
    assert main.os.environ["AUTODEV_GUI_DEFAULT_CONFIG"] == str((tmp_path / "config.yaml").resolve())
    assert main.os.environ["AUTODEV_GUI_DEFAULT_PRD"] == str((examples / "PRD.md").resolve())

    # Avoid leaking process-wide env toggles to other tests.
    main.os.environ.pop("AUTODEV_GUI_ROLE", None)
    main.os.environ.pop("AUTODEV_GUI_LOCAL_SIMPLE", None)
    main.os.environ.pop("AUTODEV_GUI_AUTH_CONFIG", None)
    main.os.environ.pop("AUTODEV_GUI_DEFAULT_CONFIG", None)
    main.os.environ.pop("AUTODEV_GUI_DEFAULT_PRD", None)


def test_cli_local_simple_rejects_non_localhost_without_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["autodev", "local-simple", "--host", "0.0.0.0"])

    with pytest.raises(SystemExit, match="localhost-first"):
        main.cli()


def test_cli_local_simple_does_not_open_browser_by_default(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {"opened": False}

    def _fake_serve(host: str, port: int, runs_root: Path) -> None:  # noqa: ARG001
        captured["served"] = True

    def _fake_open(url: str, new: int = 0, autoraise: bool = True) -> bool:  # noqa: ARG001
        captured["opened"] = True
        return True

    monkeypatch.setattr(gui_server, "serve", _fake_serve)
    monkeypatch.setattr(main.webbrowser, "open", _fake_open)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["autodev", "local-simple"])

    main.cli()

    assert captured["served"] is True
    assert captured["opened"] is False

    main.os.environ.pop("AUTODEV_GUI_ROLE", None)
    main.os.environ.pop("AUTODEV_GUI_LOCAL_SIMPLE", None)
    main.os.environ.pop("AUTODEV_GUI_AUTH_CONFIG", None)


def test_cli_local_simple_open_flag_opens_browser(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_serve(host: str, port: int, runs_root: Path) -> None:
        captured["served"] = True

    def _fake_open(url: str, new: int = 0, autoraise: bool = True) -> bool:  # noqa: ARG001
        captured["url"] = url
        captured["new"] = new
        return True

    monkeypatch.setattr(gui_server, "serve", _fake_serve)
    monkeypatch.setattr(main.webbrowser, "open", _fake_open)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["autodev", "local-simple", "--open", "--port", "9010"])

    main.cli()

    assert captured["served"] is True
    assert captured["url"] == "http://127.0.0.1:9010"
    assert captured["new"] == 2

    main.os.environ.pop("AUTODEV_GUI_ROLE", None)
    main.os.environ.pop("AUTODEV_GUI_LOCAL_SIMPLE", None)
    main.os.environ.pop("AUTODEV_GUI_AUTH_CONFIG", None)


def test_cli_local_simple_open_failure_is_non_fatal(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_serve(host: str, port: int, runs_root: Path) -> None:
        captured["served"] = (host, port, runs_root)

    def _failing_open(url: str, new: int = 0, autoraise: bool = True) -> bool:  # noqa: ARG001
        raise RuntimeError("boom")

    monkeypatch.setattr(gui_server, "serve", _fake_serve)
    monkeypatch.setattr(main.webbrowser, "open", _failing_open)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["autodev", "local-simple", "--open"])

    main.cli()

    assert captured["served"] == ("127.0.0.1", 8787, tmp_path / "generated_runs")

    main.os.environ.pop("AUTODEV_GUI_ROLE", None)
    main.os.environ.pop("AUTODEV_GUI_LOCAL_SIMPLE", None)
    main.os.environ.pop("AUTODEV_GUI_AUTH_CONFIG", None)
