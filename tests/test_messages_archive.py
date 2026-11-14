from datetime import datetime

from autoplay.messages import collect_messages, load_messages, write_messages
from newstar_sdk.exceptions import TransportError
from newstar_sdk.models import Message, MessagePage


def _message(message_id, hour, body, category=4):
    return Message(
        id=message_id,
        sender="系统",
        subject="",
        body=body,
        time=datetime(2026, 11, 13, hour, 0, 0),
        category=category,
        category_name="系统消息",
    )


class FakeMessageClient:
    def __init__(self, pages=None, fail_pages=()):
        self.pages = pages or {}
        self.fail_pages = set(fail_pages)
        self.calls = []

    def get_messages(self, category, page=1):
        self.calls.append((category, page))
        if page in self.fail_pages:
            raise TransportError(f"page {page} timed out")
        messages = self.pages.get(page, [])
        return MessagePage(
            messages=list(messages),
            category=100,
            category_name="所有消息",
            page=page,
            pages=[1, 2],
        )


def test_collect_messages_paginates_and_dedupes():
    client = FakeMessageClient(
        {
            1: [_message(1, 12, "new"), _message(2, 11, "old")],
            2: [_message(2, 11, "old"), _message(3, 10, "older")],
        }
    )
    payload = collect_messages(client, max_pages=2)

    assert client.calls == [("all", 1), ("all", 2)]
    assert [item["id"] for item in payload["messages"]] == [1, 2, 3]
    assert payload["pages_fetched"] == 2
    assert payload["category_name"] == "所有消息"
    assert payload["errors"] == []


def test_collect_messages_keeps_partial_on_page_error():
    client = FakeMessageClient({1: [_message(1, 12, "new")]}, fail_pages=(2,))
    payload = collect_messages(client, max_pages=2)

    assert [item["id"] for item in payload["messages"]] == [1]
    assert payload["errors"][0]["page"] == 2


def test_collect_messages_limits_pages():
    client = FakeMessageClient({1: [_message(1, 12, "new")], 2: []})
    payload = collect_messages(client, max_pages=1)

    assert client.calls == [("all", 1)]
    assert payload["pages_fetched"] == 1


def test_message_round_trip(tmp_path):
    path = tmp_path / "messages.json"
    payload = {
        "schema": 1,
        "captured_at": "2026-11-13T09:00:00+00:00",
        "messages": [_message(1, 12, "body").as_dict()],
        "errors": [],
    }
    write_messages(path, payload)
    assert load_messages(path) == payload
    assert load_messages(tmp_path / "missing.json") == {}
