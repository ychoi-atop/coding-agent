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


def test_load_config_reports_unresolved_auth_placeholders(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTODEV_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AUTODEV_CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    cfg = """\
llm:
  base_url: http://127.0.0.1:1234/v1
  api_key: ${AUTODEV_LLM_API_KEY}
  oauth_token: ${AUTODEV_CLAUDE_CODE_OAUTH_TOKEN}
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

    assert "llm authentication is required" in str(exc.value)


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


def test_load_config_accepts_oauth_token_without_api_key(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  oauth_token: oauth-token-for-test
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
    assert config["llm"]["oauth_token"] == "oauth-token-for-test"


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


def test_load_config_accepts_run_budget_max_tokens(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
run:
  budget:
    max_tokens: 500000
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
    assert config["run"]["budget"]["max_tokens"] == 500000


def test_load_config_rejects_non_positive_run_budget_max_tokens(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
run:
  budget:
    max_tokens: 0
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

    assert "run.budget.max_tokens must be a positive integer" in str(exc.value)


def test_load_config_accepts_autonomous_quality_gate_policy_thresholds(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
run:
  autonomous:
    quality_gate_policy:
      tests:
        min_pass_rate: 0.95
      security:
        max_high_findings: 0
      performance:
        max_regression_pct: 5
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
    gate_cfg = config["run"]["autonomous"]["quality_gate_policy"]
    assert gate_cfg["tests"]["min_pass_rate"] == 0.95
    assert gate_cfg["security"]["max_high_findings"] == 0
    assert gate_cfg["performance"]["max_regression_pct"] == 5.0


def test_load_config_rejects_invalid_autonomous_quality_gate_policy_thresholds(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
run:
  autonomous:
    quality_gate_policy:
      tests:
        min_pass_rate: 1.1
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

    assert "run.autonomous.quality_gate_policy.tests.min_pass_rate must be between 0 and 1" in str(exc.value)


def test_load_config_accepts_llm_role_temperatures(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
  role_temperatures:
    planner: 0.4
    implementer: 0.1
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
    assert config["llm"]["role_temperatures"]["planner"] == 0.4
    assert config["llm"]["role_temperatures"]["implementer"] == 0.1


def test_load_config_rejects_out_of_range_llm_role_temperature(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
  role_temperatures:
    planner: 2.5
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

    assert "llm.role_temperatures.planner must be between 0 and 2" in str(exc.value)


def test_load_config_defaults_run_max_parallel_tasks_to_2(tmp_path):
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
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")

    config = load_config(str(cfg_path))
    assert config["run"]["max_parallel_tasks"] == 2


def test_load_config_rejects_invalid_run_max_parallel_tasks(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
run:
  max_parallel_tasks: 0
profiles:
  enterprise:
    validators:
      - ruff
    template_candidates:
      - python_fastapi
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_config(str(cfg_path))

    assert "run.max_parallel_tasks must be >= 1" in str(exc.value)


def test_load_config_reads_oauth_token_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTODEV_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AUTODEV_CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: ${AUTODEV_LLM_API_KEY}
  oauth_token: ${AUTODEV_CLAUDE_CODE_OAUTH_TOKEN}
  model: fake-model
profiles:
  enterprise:
    validators:
      - ruff
    template_candidates:
      - python_fastapi
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")
    (tmp_path / ".env").write_text(
        'AUTODEV_CLAUDE_CODE_OAUTH_TOKEN="oauth-token-from-dotenv"\n',
        encoding="utf-8",
    )

    config = load_config(str(cfg_path))
    assert config["llm"]["api_key"] is None
    assert config["llm"]["oauth_token"] == "oauth-token-from-dotenv"


def test_load_config_rejects_openrouter_with_oauth_only(tmp_path):
    cfg = """\
llm:
  base_url: "https://openrouter.ai/api/v1"
  oauth_token: oauth-token-for-test
  model: fake-model
profiles:
  enterprise:
    validators:
      - ruff
    template_candidates:
      - python_fastapi
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_config(str(cfg_path))

    assert "OpenRouter requires an API key" in str(exc.value)


def test_load_config_rejects_openrouter_model_endpoint_with_oauth_only(tmp_path):
    cfg = """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: local-test
  model: fake-model
  models:
    - base_url: "https://openrouter.ai/api/v1"
      model: "anthropic/claude-opus-4-6"
      oauth_token: oauth-token-for-test
profiles:
  enterprise:
    validators:
      - ruff
    template_candidates:
      - python_fastapi
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_config(str(cfg_path))

    assert "OpenRouter requires an API key" in str(exc.value)
