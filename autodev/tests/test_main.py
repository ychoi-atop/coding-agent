from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # noqa: E402

import autodev.main as main  # noqa: E402


def _build_fake_args(tmp_path: Path, cfg_name: str = "config.yaml"):
    cfg_path = tmp_path / cfg_name
    prd_path = tmp_path / "prd.md"
    prd_path.write_text("# test", encoding="utf-8")
    return cfg_path, prd_path


def test_cli_rejects_unknown_profile_if_explicitly_requested(tmp_path, monkeypatch):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
"""
    cfg_path, prd_path = _build_fake_args(tmp_path)
    cfg_path.write_text(cfg, encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autodev",
            "--prd",
            str(prd_path),
            "--out",
            str(tmp_path / "runs"),
            "--profile",
            "enterprise",
            "--config",
            str(cfg_path),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main.cli()

    assert "Profile 'enterprise' not found" in str(exc.value)
    assert "Available profiles: minimal" in str(exc.value)


def test_cli_auto_selects_single_profile_when_omitted(tmp_path, monkeypatch):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
    quality_profile:
      name: balanced
"""
    cfg_path, prd_path = _build_fake_args(tmp_path)
    cfg_path.write_text(cfg, encoding="utf-8")

    async def _fake_run_autodev_enterprise(*_args, **_kwargs):
        return True, {"project": {}}, {"project": {}}, []

    monkeypatch.setattr(main, "run_autodev_enterprise", _fake_run_autodev_enterprise)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autodev",
            "--prd",
            str(prd_path),
            "--out",
            str(tmp_path / "runs"),
            "--config",
            str(cfg_path),
        ],
    )

    # Should pass profile resolution and attempt to run; fake engine avoids external dependencies.
    main.cli()


def test_cli_requires_profile_when_multiple_profiles_are_configured(tmp_path, monkeypatch):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
  strict:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
"""
    cfg_path, prd_path = _build_fake_args(tmp_path, "config.yaml")
    cfg_path.write_text(cfg, encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["autodev", "--prd", str(prd_path), "--out", str(tmp_path / "runs"), "--config", str(cfg_path)],
    )

    with pytest.raises(SystemExit) as exc:
        main.cli()

    assert "Profile was not provided" in str(exc.value)
    assert "Available profiles: minimal, strict" in str(exc.value)


def test_cli_prefers_cli_model_overrides_over_env_and_config(tmp_path, monkeypatch):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: config-model
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
"""
    cfg_path, prd_path = _build_fake_args(tmp_path)
    cfg_path.write_text(cfg, encoding="utf-8")

    captured: dict[str, str] = {}

    async def _fake_run_autodev_enterprise(*_args, **kwargs):
        captured["model"] = kwargs["client"].model
        return True, {"project": {}}, {"project": {}}, []

    monkeypatch.setattr(main, "run_autodev_enterprise", _fake_run_autodev_enterprise)
    monkeypatch.setenv("AUTODEV_LLM_MODEL", "env-model")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autodev",
            "--prd",
            str(prd_path),
            "--out",
            str(tmp_path / "runs"),
            "--config",
            str(cfg_path),
            "--profile",
            "minimal",
            "--model",
            "cli-model",
        ],
    )

    main.cli()

    assert captured["model"] == "cli-model"


def test_cli_uses_env_model_override_when_cli_model_is_missing(tmp_path, monkeypatch):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: config-model
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
"""
    cfg_path, prd_path = _build_fake_args(tmp_path)
    cfg_path.write_text(cfg, encoding="utf-8")

    captured: dict[str, str] = {}

    async def _fake_run_autodev_enterprise(*_args, **kwargs):
        captured["model"] = kwargs["client"].model
        return True, {"project": {}}, {"project": {}}, []

    monkeypatch.setattr(main, "run_autodev_enterprise", _fake_run_autodev_enterprise)
    monkeypatch.setenv("AUTODEV_LLM_MODEL", "env-model")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autodev",
            "--prd",
            str(prd_path),
            "--out",
            str(tmp_path / "runs"),
            "--config",
            str(cfg_path),
            "--profile",
            "minimal",
        ],
    )

    main.cli()

    assert captured["model"] == "env-model"


def test_cli_emits_run_and_request_identifiers(tmp_path, monkeypatch):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
    quality_profile:
      name: balanced
"""
    cfg_path, prd_path = _build_fake_args(tmp_path)
    cfg_path.write_text(cfg, encoding="utf-8")

    captured: dict[str, str] = {}

    async def _fake_run_autodev_enterprise(*_args, **kwargs):
        run_id = kwargs.get("run_id")
        request_id = kwargs.get("request_id")
        profile = kwargs.get("profile")

        assert isinstance(run_id, str)
        assert isinstance(request_id, str)
        assert isinstance(profile, str)

        captured["run_id"] = run_id
        captured["request_id"] = request_id
        captured["profile"] = profile
        return True, {"project": {}}, {"project": {}}, []

    monkeypatch.setattr(main, "run_autodev_enterprise", _fake_run_autodev_enterprise)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autodev",
            "--prd",
            str(prd_path),
            "--out",
            str(tmp_path / "runs"),
            "--config",
            str(cfg_path),
            "--profile",
            "minimal",
        ],
    )

    main.cli()

    assert isinstance(captured.get("run_id"), str)
    assert isinstance(captured.get("request_id"), str)
    assert captured.get("profile") == "minimal"
    assert len(captured["run_id"]) == 32
    assert len(captured["request_id"]) == 32


def test_cli_passes_resume_flag_to_runner(tmp_path, monkeypatch):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
"""
    cfg_path, prd_path = _build_fake_args(tmp_path)
    cfg_path.write_text(cfg, encoding="utf-8")
    captured: dict[str, object] = {}

    async def _fake_run_autodev_enterprise(*_args, **kwargs):
        captured["resume"] = kwargs.get("resume")
        return True, {"project": {}}, {"project": {}}, []

    monkeypatch.setattr(main, "run_autodev_enterprise", _fake_run_autodev_enterprise)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autodev",
            "--prd",
            str(prd_path),
            "--out",
            str(tmp_path / "runs"),
            "--config",
            str(cfg_path),
            "--profile",
            "minimal",
            "--resume",
        ],
    )

    main.cli()
    assert captured["resume"] is True


def test_cli_passes_interactive_flag_to_runner(tmp_path, monkeypatch):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
"""
    cfg_path, prd_path = _build_fake_args(tmp_path)
    cfg_path.write_text(cfg, encoding="utf-8")
    captured: dict[str, object] = {}

    async def _fake_run_autodev_enterprise(*_args, **kwargs):
        captured["interactive"] = kwargs.get("interactive")
        return True, {"project": {}}, {"project": {}}, []

    monkeypatch.setattr(main, "run_autodev_enterprise", _fake_run_autodev_enterprise)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autodev",
            "--prd",
            str(prd_path),
            "--out",
            str(tmp_path / "runs"),
            "--config",
            str(cfg_path),
            "--profile",
            "minimal",
            "--interactive",
        ],
    )

    main.cli()
    assert captured["interactive"] is True


def test_cli_passes_role_temperatures_to_runner(tmp_path, monkeypatch):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
  role_temperatures:
    planner: 0.4
    implementer: 0.1
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
"""
    cfg_path, prd_path = _build_fake_args(tmp_path)
    cfg_path.write_text(cfg, encoding="utf-8")
    captured: dict[str, object] = {}

    async def _fake_run_autodev_enterprise(*_args, **kwargs):
        captured["role_temperatures"] = kwargs.get("role_temperatures")
        return True, {"project": {}}, {"project": {}}, []

    monkeypatch.setattr(main, "run_autodev_enterprise", _fake_run_autodev_enterprise)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autodev",
            "--prd",
            str(prd_path),
            "--out",
            str(tmp_path / "runs"),
            "--config",
            str(cfg_path),
            "--profile",
            "minimal",
        ],
    )

    main.cli()
    assert captured["role_temperatures"] == {"planner": 0.4, "implementer": 0.1}
