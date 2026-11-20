"""Message reporting through a Feishu (Lark) custom-bot webhook.

A custom bot is bound to one group and exposes a webhook URL.  Posting a JSON
body such as ``{"msg_type": "text", "content": {"text": "..."}}`` delivers a
message to that group.  If the bot has signature verification enabled, the
request must also carry ``timestamp`` and ``sign`` where ``sign`` is
``Base64(HmacSHA256(key=timestamp + "\\n" + secret, message=""))``.

Configuration lives in ``.env`` (or the process environment):

* ``NEWSTAR_FEISHU_WEBHOOK`` -- full webhook URL; absent means "disabled".
* ``NEWSTAR_FEISHU_SECRET`` -- optional signing secret.
* ``NEWSTAR_FEISHU_TIMEOUT`` -- HTTP timeout in seconds (default 10).

Reporting is best-effort: missing configuration, network failures, non-2xx
responses and non-zero Feishu business codes are all returned as
:class:`SendResult` values with ``ok=False``; :func:`notify` and the
:class:`FeishuNotifier` methods never raise for those cases.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Union
from urllib.parse import urlsplit

import requests

from .env import PathLike, resolve_env

#: Environment keys understood by this module.
ENV_WEBHOOK = "NEWSTAR_FEISHU_WEBHOOK"
ENV_SECRET = "NEWSTAR_FEISHU_SECRET"
ENV_TIMEOUT = "NEWSTAR_FEISHU_TIMEOUT"

#: Feishu rejects request bodies larger than 20 KB.
MAX_BODY_BYTES = 20 * 1024

#: Default HTTP timeout.  A report must not stall a game cycle for long.
DEFAULT_TIMEOUT = 10.0

#: Feishu custom bots allow 100 messages/minute and 5/second per bot.
RATE_LIMIT_HINT = "飞书自定义机器人限流 100 次/分钟、5 次/秒"

_MAX_ERROR_BODY = 200


def feishu_sign(timestamp: int, secret: str) -> str:
    """Return the signature Feishu expects for ``timestamp`` and ``secret``.

    The signed string is ``timestamp + "\\n" + secret``; it is used as the HMAC
    key over an empty message, then Base64-encoded.  This mirrors the official
    Python sample and the Go/Java implementations.
    """

    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _timeout_value(raw: Any) -> float:
    if raw in (None, ""):
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


@dataclass(frozen=True)
class SendResult:
    """Outcome of one reporting attempt.

    ``skipped`` means "no webhook configured", which callers usually want to
    ignore rather than treat as an error.  ``code`` is Feishu's business code
    (present even on HTTP 200); ``http_status`` is the transport status.
    """

    ok: bool
    channel: str = "feishu"
    skipped: bool = False
    http_status: Optional[int] = None
    code: Optional[int] = None
    msg: str = ""
    error: Optional[str] = None
    response: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "ok": self.ok,
            "channel": self.channel,
            "skipped": self.skipped,
            "http_status": self.http_status,
            "code": self.code,
            "msg": self.msg,
            "error": self.error,
        }
        if self.response is not None:
            data["response"] = self.response
        return data


class FeishuNotifier:
    """Send text or rich-text messages to one Feishu custom bot.

    The webhook URL is a credential: do not log it.  Error strings produced by
    this class are sanitised so the URL and signing secret are replaced before
    they reach a caller's logs.
    """

    channel = "feishu"

    def __init__(
        self,
        webhook: str,
        secret: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        *,
        session: Optional[requests.Session] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.webhook = (webhook or "").strip()
        self.secret = (secret or "").strip()
        self.timeout = _timeout_value(timeout)
        self._clock = clock or time.time
        self._session = session if session is not None else requests.Session()
        self._owns_session = session is None

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> "FeishuNotifier":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    @property
    def configured(self) -> bool:
        return bool(self.webhook)

    # -- payloads ----------------------------------------------------------

    def text_payload(self, text: str) -> Dict[str, Any]:
        """Build a signed ``text`` payload without sending it."""

        payload: Dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": "" if text is None else str(text)},
        }
        return self._sign(payload)

    def post_payload(
        self,
        title: str,
        lines: Union[str, Sequence[str]],
    ) -> Dict[str, Any]:
        """Build a signed ``post`` (rich text) payload without sending it."""

        if isinstance(lines, str):
            lines = lines.splitlines() or [lines]
        elif lines is None:
            lines = []
        paragraphs = [
            [{"tag": "text", "text": "" if line is None else str(line)}]
            for line in lines
        ]
        content: Dict[str, Any] = {"content": paragraphs}
        if title:
            content["title"] = str(title)
        payload: Dict[str, Any] = {
            "msg_type": "post",
            "content": {"post": {"zh_cn": content}},
        }
        return self._sign(payload)

    def _sign(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.secret:
            return payload
        timestamp = int(self._clock())
        signed = dict(payload)
        signed["timestamp"] = str(timestamp)
        signed["sign"] = feishu_sign(timestamp, self.secret)
        return signed

    # -- sending -----------------------------------------------------------

    def send_text(self, text: str) -> SendResult:
        """Send a plain-text message."""

        text = "" if text is None else str(text)
        if not text.strip():
            return SendResult(ok=False, error="消息内容为空")
        return self._post(self.text_payload(text), label="text")

    def send_post(
        self,
        title: str,
        lines: Union[str, Sequence[str]],
    ) -> SendResult:
        """Send a rich-text message with an optional title.

        ``lines`` may be a newline-separated string or a sequence; each item
        becomes one paragraph.  This is useful for multi-line reports where a
        title makes the group notification easier to scan.
        """

        if isinstance(lines, str):
            lines = lines.splitlines() or [lines]
        elif lines is None:
            lines = []
        if not lines and not title:
            return SendResult(ok=False, error="消息内容为空")
        return self._post(self.post_payload(title, lines), label="post")

    def _post(self, payload: Dict[str, Any], *, label: str) -> SendResult:
        if not self.webhook:
            return SendResult(
                ok=False,
                skipped=True,
                error=f"未配置飞书 webhook（{ENV_WEBHOOK}）",
            )

        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(body) > MAX_BODY_BYTES:
            return SendResult(
                ok=False,
                error=(
                    f"{label} 消息体 {len(body)} 字节，超过飞书 "
                    f"{MAX_BODY_BYTES // 1024} KB 限制"
                ),
            )

        try:
            response = self._session.post(
                self.webhook,
                data=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=self.timeout,
            )
        except (requests.RequestException, ValueError) as exc:
            return SendResult(
                ok=False,
                error=self._sanitize(f"{type(exc).__name__}: {exc}"),
            )

        return self._parse_response(response)

    def _parse_response(self, response: Any) -> SendResult:
        raw_status = getattr(response, "status_code", None)
        try:
            status = int(raw_status) if raw_status is not None else None
        except (TypeError, ValueError):
            status = None
        try:
            data = response.json()
        except (ValueError, TypeError):
            data = None

        if not isinstance(data, dict):
            return SendResult(
                ok=False,
                http_status=status,
                error=self._sanitize(
                    f"飞书返回了无法解析的响应（HTTP {status}）"
                    f"{self._body_snippet(response)}"
                ),
            )

        # Current webhooks return ``code``/``msg``; ``StatusCode`` is a legacy
        # compatibility field that is documented as "do not rely on it".
        code = data.get("code")
        if not _is_int(code):
            code = data.get("StatusCode")
        if _is_int(code):
            code_value: Optional[int] = int(code)
        else:
            code_value = None
        msg = str(data.get("msg") or data.get("StatusMessage") or "")

        if status is not None and not 200 <= status < 300:
            return SendResult(
                ok=False,
                http_status=status,
                code=code_value,
                msg=msg,
                error=self._sanitize(
                    f"HTTP {status}"
                    + (f": {msg}" if msg else "")
                    + self._body_snippet(response)
                ),
                response=data,
            )

        if code_value is None:
            return SendResult(
                ok=False,
                http_status=status,
                msg=msg,
                error=self._sanitize(
                    "飞书响应缺少 code 字段"
                    + self._body_snippet(response)
                ),
                response=data,
            )

        if code_value == 0:
            return SendResult(
                ok=True,
                http_status=status,
                code=0,
                msg=msg or "success",
                response=data,
            )

        hint = f"；{RATE_LIMIT_HINT}" if code_value == 11232 else ""
        return SendResult(
            ok=False,
            http_status=status,
            code=code_value,
            msg=msg,
            error=self._sanitize(
                f"飞书拒绝消息：code {code_value}"
                + (f"（{msg}）" if msg else "")
                + hint
            ),
            response=data,
        )

    # -- helpers -----------------------------------------------------------

    def _body_snippet(self, response: Any) -> str:
        text = getattr(response, "text", "") or ""
        text = " ".join(str(text).split())
        if len(text) > _MAX_ERROR_BODY:
            text = text[:_MAX_ERROR_BODY] + "…"
        return f"：{text}" if text else ""

    def _sanitize(self, text: str) -> str:
        if self.webhook:
            text = text.replace(self.webhook, "<feishu-webhook>")
            # requests sometimes mentions only the path, which contains the
            # bot token; redact it as well.
            try:
                path = urlsplit(self.webhook).path
            except ValueError:
                path = ""
            if path:
                text = text.replace(path, "<feishu-webhook-path>")
        # Avoid mangling ordinary text when a deliberately short secret is in
        # use; realistic Feishu secrets are much longer than this.
        if len(self.secret) >= 6:
            text = text.replace(self.secret, "***")
        return text


def notifier_from_env(
    *,
    webhook: Optional[str] = None,
    secret: Optional[str] = None,
    timeout: Optional[float] = None,
    env_file: Optional[PathLike] = None,
) -> Optional[FeishuNotifier]:
    """Build a notifier from explicit values, the environment, or ``.env``.

    Explicit arguments win; then the process environment; then ``.env``.
    Returns ``None`` when no webhook is configured, which means the reporting
    channel is disabled rather than misconfigured.
    """

    values = resolve_env((ENV_WEBHOOK, ENV_SECRET, ENV_TIMEOUT), env_file)
    resolved_webhook = (
        webhook if webhook is not None else values.get(ENV_WEBHOOK, "")
    ).strip()
    if not resolved_webhook:
        return None
    resolved_secret = (
        secret if secret is not None else values.get(ENV_SECRET, "")
    ).strip()
    raw_timeout: Any = timeout if timeout is not None else values.get(ENV_TIMEOUT)
    return FeishuNotifier(
        resolved_webhook,
        resolved_secret,
        _timeout_value(raw_timeout),
    )


def notify(
    text: str,
    *,
    webhook: Optional[str] = None,
    secret: Optional[str] = None,
    timeout: Optional[float] = None,
    env_file: Optional[PathLike] = None,
) -> SendResult:
    """Send one text report using the configured Feishu bot.

    Intended for a one-off report from a script::

        from utils.notify import notify

        notify(f"cycle failed: {error}")

    Long-running code should create :class:`FeishuNotifier` once and reuse it.
    A missing webhook returns ``SendResult(skipped=True)`` instead of raising.
    """

    notifier = notifier_from_env(
        webhook=webhook,
        secret=secret,
        timeout=timeout,
        env_file=env_file,
    )
    if notifier is None:
        return SendResult(
            ok=False,
            skipped=True,
            error=f"未配置飞书 webhook（{ENV_WEBHOOK}）",
        )
    try:
        return notifier.send_text(text)
    finally:
        notifier.close()


def is_feishu_webhook(url: str) -> bool:
    """Best-effort sanity check for a webhook URL.

    Feishu-hosted hooks use ``https://open.feishu.cn/open-apis/bot/v2/hook/...``
    (or the Lark domain), but private deployments and proxies exist, so only
    the scheme and host are required here.
    """

    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)
