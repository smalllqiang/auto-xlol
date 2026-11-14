import json

from autoplay.records import collect_records, load_records, write_records
from newstar_sdk.exceptions import TransportError
from newstar_sdk.models import RecordEntry, RecordSection, UniverseRecords


class FakeRecordsClient:
    base_url = "https://example.test/"

    def get_universe_records(self):
        return UniverseRecords(
            updated_at="2026-11-14T19:00:05",
            updated_text="2026年11月14日, 19:00:05",
            sections=[
                RecordSection(
                    label="建筑",
                    entries=[
                        RecordEntry(
                            section="建筑",
                            item_id=1,
                            name="合金冶炼厂",
                            server=48,
                            own=21,
                            holders=["TestUser"],
                        )
                    ],
                )
            ],
        )


def test_collect_records_keeps_payload():
    payload = collect_records(FakeRecordsClient())

    assert payload["base_url"] == "https://example.test/"
    assert payload["updated_at"] == "2026-11-14T19:00:05"
    assert payload["errors"] == []
    section = payload["sections"][0]
    assert section["label"] == "建筑"
    entry = section["entries"][0]
    assert entry["name"] == "合金冶炼厂"
    assert entry["server"] == 48
    assert entry["own"] == 21
    assert entry["percent"] == 44
    assert entry["holders"] == ["TestUser"]


def test_collect_records_reports_transport_error():
    class FailingClient:
        base_url = "https://example.test/"

        def get_universe_records(self):
            raise TransportError("records page timed out")

    payload = collect_records(FailingClient())

    assert payload["sections"] == []
    assert payload["errors"][0]["section"] == "all"
    assert "timed out" in payload["errors"][0]["error"]


def test_collect_records_flags_empty_page():
    class EmptyClient:
        def get_universe_records(self):
            return UniverseRecords()

    payload = collect_records(EmptyClient())

    assert payload["sections"] == []
    assert payload["errors"][0]["error"].startswith("空页")


def test_write_and_load_records_roundtrip(tmp_path):
    path = tmp_path / "records.json"
    payload = collect_records(FakeRecordsClient())

    write_records(path, payload)

    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1
    assert load_records(path)["sections"][0]["entries"][0]["name"] == "合金冶炼厂"
    assert load_records(tmp_path / "missing.json") == {}

    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert load_records(tmp_path / "broken.json") == {}
