"""Tests for the shared-session plumbing in ``scripts/local_config.py``."""

import argparse
import importlib.util
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
