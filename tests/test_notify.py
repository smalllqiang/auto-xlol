"""Tests for the Feishu reporting channel in ``utils/notify.py``."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
import requests

from utils.env import parse_dotenv
from utils.notify import (
    ENV_SECRET,
    ENV_TIMEOUT,
    ENV_WEBHOOK,
    FeishuNotifier,
    feishu_sign,
    is_feishu_webhook,
    notifier_from_env,
    notify,
)

WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response

    def close(self):
        self.closed = True

    def payload(self, index: int = 0):
        body = self.calls[index][1]["data"]
        return json.loads(body.decode("utf-8"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for key in (ENV_WEBHOOK, ENV_SECRET, ENV_TIMEOUT):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


def _ok_session():
    return FakeSession(
        FakeResponse(payload={"StatusCode": 0, "StatusMessage": "success", "code": 0, "msg": "success"})
    )


def test_send_text_payload_and_headers():
    session = _ok_session()
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_text("hello")

    assert result.ok is True
    assert result.code == 0
    assert result.http_status == 200
    assert result.msg == "success"
    url, kwargs = session.calls[0]
    assert url == WEBHOOK
    assert kwargs["headers"]["Content-Type"].startswith("application/json")
    assert kwargs["timeout"] == 10.0
    assert session.payload() == {
        "msg_type": "text",
        "content": {"text": "hello"},
    }


def test_send_post_payload():
    session = _ok_session()
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_post("标题", ["第一行", "第二行"])

    assert result.ok is True
    assert session.payload() == {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": "标题",
                    "content": [
                        [{"tag": "text", "text": "第一行"}],
                        [{"tag": "text", "text": "第二行"}],
                    ],
                }
            }
        },
    }


def test_send_post_accepts_newline_string():
    session = _ok_session()
    notifier = FeishuNotifier(WEBHOOK, session=session)

    notifier.send_post("", "a\nb")

    assert session.payload()["content"]["post"]["zh_cn"]["content"] == [
        [{"tag": "text", "text": "a"}],
        [{"tag": "text", "text": "b"}],
    ]


def test_signature_payload_uses_official_algorithm():
    session = _ok_session()
    notifier = FeishuNotifier(
        WEBHOOK,
        "demo-secret",
        session=session,
        clock=lambda: 1599360473,
    )

    result = notifier.send_text("signed")

    assert result.ok is True
    payload = session.payload()
    expected = base64.b64encode(
        hmac.new(
            b"1599360473\ndemo-secret", digestmod=hashlib.sha256
        ).digest()
    ).decode("ascii")
    assert payload["timestamp"] == "1599360473"
    assert payload["sign"] == expected
    assert payload["sign"] == feishu_sign(1599360473, "demo-secret")


def test_feishu_sign_is_stable():
    assert feishu_sign(100, "demo") == base64.b64encode(
        hmac.new(b"100\ndemo", digestmod=hashlib.sha256).digest()
    ).decode("ascii")


def test_business_error_http_200_is_failure():
    session = FakeSession(
        FakeResponse(payload={"code": 19021, "msg": "sign match fail", "data": {}})
    )
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_text("hello")

    assert result.ok is False
    assert result.code == 19021
    assert "19021" in result.error
    assert "sign match fail" in result.error
    assert "限流" not in result.error


def test_rate_limit_code_gets_hint():
    session = FakeSession(FakeResponse(payload={"code": 11232, "msg": "rate limited"}))
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_text("hello")

    assert result.ok is False
    assert result.code == 11232
    assert "限流" in result.error


def test_http_error_is_failure():
    session = FakeSession(FakeResponse(status_code=502, text="bad gateway"))
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_text("hello")

    assert result.ok is False
    assert result.http_status == 502
    assert "HTTP 502" in result.error


def test_unparseable_200_is_failure():
    session = FakeSession(FakeResponse(text="<html>not feishu</html>"))
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_text("hello")

    assert result.ok is False
    assert "无法解析" in result.error


def test_missing_code_is_failure():
    session = FakeSession(FakeResponse(payload={"msg": "success"}))
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_text("hello")

    assert result.ok is False
    assert "缺少 code" in result.error


def test_network_error_is_failure_and_hides_webhook():
    session = FakeSession(error=requests.ConnectionError(f"boom {WEBHOOK}"))
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_text("hello")

    assert result.ok is False
    assert WEBHOOK not in result.error
    assert "<feishu-webhook>" in result.error


def test_network_error_hides_webhook_path_and_token():
    path = "/open-apis/bot/v2/hook/test-token"
    session = FakeSession(error=requests.ConnectionError(f"failed on {path}"))
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_text("hello")

    assert result.ok is False
    assert path not in result.error
    assert "test-token" not in result.error
    assert "<feishu-webhook-path>" in result.error


def test_empty_text_is_failure_without_request():
    session = _ok_session()
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_text("  \n")

    assert result.ok is False
    assert "为空" in result.error
    assert session.calls == []


def test_oversized_body_is_rejected_before_request():
    session = _ok_session()
    notifier = FeishuNotifier(WEBHOOK, session=session)

    result = notifier.send_text("x" * (21 * 1024))

    assert result.ok is False
    assert "20 KB" in result.error
    assert session.calls == []


def test_unconfigured_notifier_is_skipped():
    session = _ok_session()
    notifier = FeishuNotifier("", session=session)

    result = notifier.send_text("hello")

    assert result.ok is False
    assert result.skipped is True
    assert session.calls == []


def test_notifier_from_env_reads_dotenv(tmp_path):
    (tmp_path / ".env").write_text(
        "# reporting\n"
        f"{ENV_WEBHOOK}='{WEBHOOK}'\n"
        f'{ENV_SECRET}="demo-secret"\n'
        f"{ENV_TIMEOUT}=3.5\n",
        encoding="utf-8",
    )

    notifier = notifier_from_env()

    assert notifier is not None
    assert notifier.webhook == WEBHOOK
    assert notifier.secret == "demo-secret"
    assert notifier.timeout == 3.5
    notifier.close()


def test_process_environment_wins_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(f"{ENV_WEBHOOK}=https://file.example/hook\n")
    monkeypatch.setenv(ENV_WEBHOOK, "https://env.example/hook")

    notifier = notifier_from_env()

    assert notifier is not None
    assert notifier.webhook == "https://env.example/hook"
    notifier.close()


def test_explicit_values_win_over_environment(monkeypatch):
    monkeypatch.setenv(ENV_WEBHOOK, "https://env.example/hook")

    notifier = notifier_from_env(webhook="https://arg.example/hook", secret="arg")

    assert notifier is not None
    assert notifier.webhook == "https://arg.example/hook"
    assert notifier.secret == "arg"
    notifier.close()


def test_notify_without_webhook_is_skipped():
    result = notify("hello")

    assert result.ok is False
    assert result.skipped is True
    assert ENV_WEBHOOK in result.error


def test_send_result_as_dict():
    result = notify("hello")

    data = result.as_dict()
    assert data["ok"] is False
    assert data["skipped"] is True
    assert data["channel"] == "feishu"
    assert data["error"]


def test_is_feishu_webhook():
    assert is_feishu_webhook(WEBHOOK) is True
    assert is_feishu_webhook("http://127.0.0.1:8080/hook") is True
    assert is_feishu_webhook("open.feishu.cn/open-apis/bot/v2/hook/x") is False
    assert is_feishu_webhook("") is False


def test_parse_dotenv_keeps_hash_inside_value(tmp_path):
    path = tmp_path / "custom.env"
    path.write_text(
        "# comment\nA=1\nB='x'\nC=\"y\"\nD=a=b\nE=a#b\nBAD\n",
        encoding="utf-8",
    )

    assert parse_dotenv(path) == {
        "A": "1",
        "B": "x",
        "C": "y",
        "D": "a=b",
        "E": "a#b",
    }
