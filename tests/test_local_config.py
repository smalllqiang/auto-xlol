"""Tests for the shared-session plumbing in ``scripts/local_config.py``."""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _module():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "local_config_under_test", SCRIPTS / "local_config.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _args(**overrides):
    values = {
        "config": None,
        "base_url": "https://example.test/",
        "username": "user",
        "password": "secret",
        "universe": "UNI1",
        "no_verify_ssl": True,
        "interval": 0.1,
        "session_file": None,
        "relogin": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for key in (
        "NEWSTAR_BASE_URL",
        "NEWSTAR_USERNAME",
        "NEWSTAR_PASSWORD",
        "NEWSTAR_UNIVERSE",
        "NEWSTAR_VERIFY_SSL",
        "NEWSTAR_SESSION_FILE",
        "NEWSTAR_TIMEOUT",
        "NEWSTAR_AGENT_BASE_URL",
        "NEWSTAR_AGENT_MODEL",
        "NEWSTAR_AGENT_API_KEY",
        "NEWSTAR_AGENT_TEMPERATURE",
        "NEWSTAR_AGENT_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


def test_load_config_uses_existing_session_file_without_relogin():
    module = _module()
    session = Path("shared.json")
    session.write_text("{}", encoding="utf-8")

    config = module.load_config(_args(session_file=str(session)))

    assert config.session_file == str(session)
    assert config.relogin is False


def test_load_config_relogin_flag_forces_password_login():
    module = _module()
    session = Path("shared.json")
    session.write_text("{}", encoding="utf-8")

    config = module.load_config(_args(session_file=str(session), relogin=True))

    assert config.session_file == str(session)
    assert config.relogin is True


def test_load_config_auto_uses_default_session_file():
    module = _module()
    Path("state").mkdir()
    (Path("state") / "session.json").write_text("{}", encoding="utf-8")

    config = module.load_config(_args())

    assert config.session_file == "state/session.json"
    assert config.relogin is False


def test_load_config_env_session_file_and_missing_base_url():
    module = _module()
    session = Path("env.json")
    session.write_text("{}", encoding="utf-8")
    config = module.load_config(_args(session_file=None))
    assert config.session_file is None

    os.environ["NEWSTAR_SESSION_FILE"] = str(session)
    try:
        config = module.load_config(_args())
    finally:
        del os.environ["NEWSTAR_SESSION_FILE"]
    assert config.session_file == str(session)

    with pytest.raises(SystemExit):
        module.load_config(_args(base_url=None))


def test_add_session_args_parses_flags():
    module = _module()
    parser = argparse.ArgumentParser()
    module.add_session_args(parser)

    args = parser.parse_args(["--session-file", "x.json", "--relogin"])

    assert args.session_file == "x.json"
    assert args.relogin is True


def test_make_client_reuses_valid_session(monkeypatch):
    module = _module()
    session = Path("shared.json")
    session.write_text("{}", encoding="utf-8")
    config = module.Config(
        base_url="https://example.test/",
        username="user",
        password="secret",
        session_file=str(session),
        relogin=False,
    )
    calls = {"load": 0, "login": 0}
    monkeypatch.setattr(
        module.NewStarClient,
        "load_session",
        lambda self, path, validate=True: calls.__setitem__("load", calls["load"] + 1) or True,
    )
    monkeypatch.setattr(
        module.NewStarClient, "login", lambda self, force=False: calls.__setitem__(
            "login", calls["login"] + 1
        )
    )

    client = module.make_client(config)
    client.close()

    assert calls == {"load": 1, "login": 0}


def test_make_client_refuses_to_relogin_over_a_shared_session(monkeypatch):
    module = _module()
    session = Path("shared.json")
    session.write_text("{}", encoding="utf-8")
    config = module.Config(
        base_url="https://example.test/",
        username="user",
        password="secret",
        session_file=str(session),
        relogin=False,
    )
    monkeypatch.setattr(module.NewStarClient, "load_session", lambda *a, **k: False)
    monkeypatch.setattr(
        module.NewStarClient,
        "login",
        lambda *a, **k: pytest.fail("must not log in while sharing a session"),
    )

    with pytest.raises(SystemExit) as info:
        module.make_client(config)
    assert "session.py import" in str(info.value)


def test_make_client_relogins_when_explicitly_allowed(monkeypatch):
    module = _module()
    session = Path("shared.json")
    session.write_text("{}", encoding="utf-8")
    config = module.Config(
        base_url="https://example.test/",
        username="user",
        password="secret",
        session_file=str(session),
        relogin=True,
    )
    calls = {"login": 0}
    monkeypatch.setattr(module.NewStarClient, "load_session", lambda *a, **k: False)
    monkeypatch.setattr(
        module.NewStarClient,
        "login",
        lambda self, force=False: calls.__setitem__("login", calls["login"] + 1),
    )

    client = module.make_client(config)
    client.close()

    assert calls["login"] == 1


def _env_file(tmp_path, body: str) -> str:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_agent_settings_are_loaded_from_the_same_file(tmp_path):
    module = _module()
    path = _env_file(
        tmp_path,
        "NEWSTAR_BASE_URL=https://game.test/\n"
        "NEWSTAR_AGENT_BASE_URL=https://model.test/v1\n"
        "NEWSTAR_AGENT_MODEL=model-a\n"
        "NEWSTAR_AGENT_API_KEY=sk-1\n",
    )

    config = module.load_config(
        _args(config=path, base_url=None, username=None, password=None, universe=None)
    )

    assert config.agent.configured is True
    assert config.agent.base_url == "https://model.test/v1"
    assert config.agent.model == "model-a"
    assert config.agent.api_key == "sk-1"


def test_agent_settings_follow_precedence(tmp_path, monkeypatch):
    module = _module()
    path = _env_file(tmp_path, "NEWSTAR_BASE_URL=https://game.test/\nNEWSTAR_AGENT_MODEL=file-model\n")

    monkeypatch.setenv("NEWSTAR_AGENT_MODEL", "env-model")
    config = module.load_config(_args(config=path, base_url=None, username=None, password=None))
    assert config.agent.model == "env-model"

    config = module.load_config(
        _args(
            config=path,
            base_url=None,
            username=None,
            password=None,
            agent_model="cli-model",
        )
    )
    assert config.agent.model == "cli-model"


def test_unconfigured_agent_is_visible(tmp_path):
    module = _module()
    path = _env_file(tmp_path, "NEWSTAR_BASE_URL=https://game.test/\n")

    config = module.load_config(_args(config=path, base_url=None, username=None, password=None))

    assert config.agent.configured is False
    assert config.agent.as_dict()["api_key"] == ""


def test_bad_agent_temperature_fails_loudly(tmp_path):
    module = _module()
    path = _env_file(
        tmp_path,
        "NEWSTAR_BASE_URL=https://game.test/\nNEWSTAR_AGENT_TEMPERATURE=hot\n",
    )

    with pytest.raises(SystemExit):
        module.load_config(_args(config=path, base_url=None, username=None, password=None))


def test_load_values_does_not_require_a_base_url():
    module = _module()

    assert module.load_values(argparse.Namespace(config=None)) == {}


def _config_script():
    module = _module()
    sys.modules["local_config"] = module
    spec = importlib.util.spec_from_file_location("config_script", SCRIPTS / "config.py")
    script = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(script)
    return script


def test_config_script_masks_secrets_and_reports_problems(tmp_path, capsys):
    (tmp_path / ".env").write_text(
        "NEWSTAR_BASE_URL=https://game.test/\nNEWSTAR_USERNAME=user\nNEWSTAR_PASSWORD=pw\n",
        encoding="utf-8",
    )
    script = _config_script()

    assert script.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["game"]["password"] == "***"
    assert payload["errors"] == []
    assert any("模型" in warning for warning in payload["warnings"])
    # A missing model is a warning: the other scripts still work.
    assert script.main(["--validate"]) == 0


def test_config_script_validate_fails_on_missing_required(tmp_path, capsys):
    (tmp_path / ".env").write_text("NEWSTAR_USERNAME=user\nNEWSTAR_PASSWORD=pw\n", encoding="utf-8")
    script = _config_script()

    assert script.main(["--validate"]) == 1
    assert "BASE_URL" in capsys.readouterr().out


def test_config_script_can_reveal_secrets(tmp_path, capsys):
    (tmp_path / ".env").write_text(
        "NEWSTAR_BASE_URL=https://game.test/\nNEWSTAR_USERNAME=user\nNEWSTAR_PASSWORD=pw\n",
        encoding="utf-8",
    )
    script = _config_script()

    script.main(["--json", "--reveal"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["game"]["password"] == "pw"
