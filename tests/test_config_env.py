"""Tests for Config environment variable override."""
import pytest
from core.config import Config


@pytest.fixture(autouse=True)
def reset_config():
    Config.reset()
    yield
    Config.reset()


@pytest.fixture
def yaml_config(tmp_path):
    config_file = tmp_path / "settings.yaml"
    config_file.write_text("""
llm:
  api_key: "yaml_key"
  base_url: "https://yaml.example.com"
  model: "yaml-model"
translation:
  provider: "volcengine_ark"
  volcengine_ark:
    api_key: "yaml_ark_key"
""")
    return str(config_file)


def test_env_overrides_yaml(yaml_config, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "env_key")
    cfg = Config(config_path=yaml_config)
    assert cfg.get("llm", "api_key") == "env_key"


def test_yaml_used_when_no_env(yaml_config):
    cfg = Config(config_path=yaml_config)
    assert cfg.get("llm", "api_key") == "yaml_key"


def test_missing_config_file_does_not_crash():
    cfg = Config(config_path="/nonexistent/path.yaml")
    assert cfg.get("llm", "api_key") is None


def test_nested_env_override(yaml_config, monkeypatch):
    monkeypatch.setenv("VOLCENGINE_ARK_API_KEY", "env_ark_key")
    cfg = Config(config_path=yaml_config)
    assert cfg.get("translation", "volcengine_ark", "api_key") == "env_ark_key"
