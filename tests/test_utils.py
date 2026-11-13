from newstar_sdk.constants import (
    CATEGORY_BUILDING,
    CATEGORY_DEFENSE,
    CATEGORY_SHIP,
    CATEGORY_TECHNOLOGY,
    Mission,
    resolve_element,
)
from newstar_sdk.models import Coordinates
from newstar_sdk.utils import extract_datatlist, parse_js_object, parse_number


def test_parse_number_variants():
    assert parse_number("10.460") == 10460
    assert parse_number("10,5 K") == 10500
    assert parse_number("2,4 K") == 2400
    assert parse_number("4,0 K") == 4000
    assert parse_number("202.5") == 202.5
    assert parse_number("1.234.567") == 1234567
    assert parse_number("1,234.56") == 1234.56
    assert parse_number("1.234,56") == 1234.56
    assert parse_number("1.2 Mio") == 1200000
    assert parse_number("0") == 0
    assert parse_number("") == 0


def test_coordinates():
    coords = Coordinates.parse("[3:505:8]")
    assert (coords.galaxy, coords.system, coords.planet, coords.type) == (3, 505, 8, 1)
    assert str(coords) == "[3:505:8]"
    assert Coordinates.parse("3:505:9", type=3).type == 3


def test_parse_js_object_trailing_commas():
    data = parse_js_object('{"a": 1, "b": {"c": 2,}, }')
    assert data["a"] == 1
    assert data["b"]["c"] == 2


def test_extract_datatlist():
    html = '<script>DatatList = { "1": {"level":"3",}, "2": {"level":"0",} };</script>'
    data = extract_datatlist(html)
    assert data["1"]["level"] == "3"
    assert data["2"]["level"] == "0"


def test_resolve_elements():
    assert resolve_element(1, CATEGORY_BUILDING) == 1
    assert resolve_element("合金冶炼厂", CATEGORY_BUILDING) == 1
    assert resolve_element("metal", CATEGORY_BUILDING) == 1
    assert resolve_element("计算机技术", CATEGORY_TECHNOLOGY) == 108
    assert resolve_element("small_cargo", CATEGORY_SHIP) == 202
    assert resolve_element(402, CATEGORY_DEFENSE) == 402


def test_mission_enum():
    assert int(Mission.TRANSPORT) == 3
    assert int(Mission.DEPLOY) == 4
