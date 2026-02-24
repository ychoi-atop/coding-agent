from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # noqa: E402

from autodev.config import load_config  # noqa: E402


def test_load_config_rejects_unknown_validator_names_in_profile(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  enterprise:
    validators:
      - ruff
      - bad_validator
    template_candidates:
      - python_fastapi
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_config(str(cfg_path))

    assert "unknown validator 'bad_validator'" in str(exc.value)


def test_load_config_reports_unresolved_api_key_placeholder(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTODEV_LLM_API_KEY", raising=False)

    cfg = """\
llm:
  base_url: http://127.0.0.1:1234/v1
  api_key: ${AUTODEV_LLM_API_KEY}
  model: fake-model
profiles:
  enterprise:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_config(str(cfg_path))

    assert "llm.api_key is required" in str(exc.value)


def test_load_config_rejects_profile_ambiguity_between_validator_policy_and_quality_policy(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  enterprise:
    validators:
      - ruff
    template_candidates:
      - python_fastapi
    validator_policy:
      per_task:
        soft_fail: ["ruff"]
    quality_profile:
      validator_policy:
        per_task:
          soft_fail: ["pytest"]
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_config(str(cfg_path))

    assert "has ambiguous policy configuration" in str(exc.value)


def test_load_config_adds_default_profile_security_and_quality_profile(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  enterprise:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")

    config = load_config(str(cfg_path))
    profile = config["profiles"]["enterprise"]

    assert profile["security"] == {"audit_required": False}
    assert profile["quality_profile"] == {"validator_policy": {"per_task": {}, "final": {}}}
    assert profile["disable_docker_build"] is False


def test_load_config_rejects_ambiguous_profile_list_missing_required_fields(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles: {}
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_config(str(cfg_path))

    assert "profiles must contain at least one profile" in str(exc.value)


def test_load_config_rejects_non_boolean_disable_docker_build(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  enterprise:
    validators:
      - ruff
    template_candidates:
      - python_fastapi
    disable_docker_build: maybe
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_config(str(cfg_path))

    assert "profiles.enterprise.disable_docker_build must be a boolean" in str(exc.value)
