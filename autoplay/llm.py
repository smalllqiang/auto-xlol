"""Model providers behind one tiny interface.

The planning loop only needs "send messages + tools, get a reply with optional
tool calls".  Keeping that behind a protocol means the loop is testable with
:class:`FakeProvider`, and a provider outage degrades to the deterministic
planner instead of stopping the daemon.

:class:`OpenAICompatibleProvider` speaks the ``/v1/chat/completions`` shape,
which most hosted models expose (including function calling).  It uses
``requests`` so the project does not grow a second HTTP dependency.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol

import requests

DEFAULT_TIMEOUT = 60.0


class ProviderError(RuntimeError):
    """The model endpoint failed or returned something unusable."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""

    def parsed_args(self) -> Dict[str, Any]:
        try:
            value = json.loads(self.arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderError(f"工具参数不是合法 JSON：{exc}") from exc
        if not isinstance(value, Mapping):
            raise ProviderError("工具参数必须是 JSON 对象")
        return dict(value)


@dataclass
class ChatReply:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw_message: Dict[str, Any] = field(default_factory=dict)
    usage: Dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""


class Provider(Protocol):
    name: str

    def complete(
        self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None
    ) -> ChatReply:
        ...


class FakeProvider:
    """A scripted provider for tests: returns the queued replies in order."""

    name = "fake"

    def __init__(self, replies: List[ChatReply]) -> None:
        self._replies = list(replies)
        self.calls: List[Dict[str, Any]] = []

    def complete(
        self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None
    ) -> ChatReply:
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": tools})
        if not self._replies:
            raise ProviderError("FakeProvider 没有更多回复了")
        return self._replies.pop(0)


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


class OpenAICompatibleProvider:
    """POST ``/v1/chat/completions`` and normalise the reply."""

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        temperature: float = 0.2,
        extra_headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        if not base_url:
            raise ProviderError("缺少 base_url")
        if not model:
            raise ProviderError("缺少 model")
        self.endpoint = _endpoint(base_url)
        self.model = model
        self.api_key = api_key
        self.timeout = float(timeout)
        self.temperature = float(temperature)
        self.extra_headers = dict(extra_headers or {})

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)
        return headers

    def complete(
        self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None
    ) -> ChatReply:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            response = requests.post(
                self.endpoint,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"模型请求失败：{exc}") from exc

        if response.status_code >= 400:
            raise ProviderError(
                f"模型返回 HTTP {response.status_code}：{response.text[:500]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"模型返回的不是 JSON：{exc}") from exc

        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"模型返回结构异常：{data!r}"[:500]) from exc

        calls: List[ToolCall] = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            calls.append(
                ToolCall(
                    id=str(item.get("id") or f"call_{len(calls)}"),
                    name=str(function.get("name") or ""),
                    arguments=str(function.get("arguments") or ""),
                )
            )

        return ChatReply(
            content=str(message.get("content") or ""),
            tool_calls=calls,
            raw_message=message,
            usage={str(key): int(value) for key, value in (data.get("usage") or {}).items() if isinstance(value, int)},
            finish_reason=str(choice.get("finish_reason") or ""),
        )


def provider_from_env() -> Optional[Provider]:
    """Build a provider from ``NEWSTAR_AGENT_*`` variables, or ``None``.

    Keeping this explicit means a misconfigured agent fails loudly in the
    script instead of silently planning with an empty model name.
    """

    base_url = os.getenv("NEWSTAR_AGENT_BASE_URL", "").strip()
    model = os.getenv("NEWSTAR_AGENT_MODEL", "").strip()
    if not base_url or not model:
        return None
    return OpenAICompatibleProvider(
        base_url,
        model,
        api_key=os.getenv("NEWSTAR_AGENT_API_KEY", "").strip(),
        timeout=float(os.getenv("NEWSTAR_AGENT_TIMEOUT", DEFAULT_TIMEOUT)),
        temperature=float(os.getenv("NEWSTAR_AGENT_TEMPERATURE", "0.2")),
    )


__all__ = [
    "ChatReply",
    "FakeProvider",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderError",
    "ToolCall",
    "provider_from_env",
]
