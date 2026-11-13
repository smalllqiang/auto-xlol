"""Small parsing helpers shared by the HTML parsers."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup

_NUMBER_RE = re.compile(
    r"(?P<num>[-+]?\d[\d.,]*)\s*(?P<suffix>Mrd|Mio|[KkMmBb万 亿])?",
    re.UNICODE,
)

_SUFFIXES = {
    "k": 1_000.0,
    "m": 1_000_000.0,
    "b": 1_000_000_000.0,
    "mrd": 1_000_000_000.0,
    "mio": 1_000_000.0,
    "万": 10_000.0,
    "亿": 100_000_000.0,
}


def normalize_base_url(url: str) -> str:
    """Normalise a game base URL to ``scheme://host/path/``."""

    if not url or not str(url).strip():
        raise ValueError("base_url must not be empty")
    text = str(url).strip()
    if not re.match(r"^https?://", text, re.IGNORECASE):
        text = "https://" + text
    if not text.endswith("/"):
        text += "/"
    return text


def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def strip_html(value: Optional[str]) -> str:
    if not value:
        return ""
    return clean_text(BeautifulSoup(str(value), "html.parser").get_text(" "))


def parse_number(value: Any) -> float:
    """Parse a number formatted by the game (``1.234``, ``10,5 K``, ...)."""

    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", " ").strip()
    if not text:
        return 0.0

    match = _NUMBER_RE.search(text)
    if not match:
        return 0.0
    number = match.group("num")
    suffix = (match.group("suffix") or "").strip().casefold()

    # Determine decimal separator.
    if "." in number and "," in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif "," in number:
        # 10,5 -> decimal, 10,500 -> thousands
        if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", number):
            number = number.replace(",", "")
        else:
            number = number.replace(",", ".")
    elif "." in number:
        if re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})+", number):
            number = number.replace(".", "")
    try:
        result = float(number)
    except ValueError:
        return 0.0
    if suffix:
        result *= _SUFFIXES.get(suffix, 1.0)
    return result


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(parse_number(value)))
    except (TypeError, ValueError):
        return default


def first_int(value: Optional[str], default: Optional[int] = None) -> Optional[int]:
    if not value:
        return default
    match = re.search(r"-?\d+", value)
    return int(match.group(0)) if match else default


def parse_coordinates(value: Optional[str], type: int = 1):
    from .models import Coordinates

    if not value:
        return None
    try:
        return Coordinates.parse(value, type=type)
    except ValueError:
        return None


def extract_braced(text: str, start: int) -> Optional[str]:
    """Return the balanced ``{...}`` block starting at/after ``start``."""

    begin = text.find("{", start)
    if begin < 0:
        return None
    depth = 0
    quote: Optional[str] = None
    escaped = False
    for pos in range(begin, len(text)):
        char = text[pos]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[begin : pos + 1]
    return None


def _single_quotes_to_double(text: str) -> str:
    out = []
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char != "'":
            out.append(char)
            i += 1
            continue
        i += 1
        buf = []
        while i < length:
            char = text[i]
            if char == "\\" and i + 1 < length:
                buf.append(char)
                buf.append(text[i + 1])
                i += 2
                continue
            if char == "'":
                i += 1
                break
            buf.append(char)
            i += 1
        payload = "".join(buf).replace('"', '\\"')
        out.append('"' + payload + '"')
    return "".join(out)


def parse_js_object(text: str) -> Dict[str, Any]:
    """Parse a JavaScript object literal used by the game templates.

    The templates emit JSON-ish data with trailing commas (and occasionally
    single quotes or unquoted keys), so a small amount of normalisation is
    needed before calling :func:`json.loads`.
    """

    if not text:
        return {}
    candidate = text.strip()
    candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
    candidate = candidate.replace("undefined", "null")
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass

    candidate = _single_quotes_to_double(candidate)
    candidate = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', candidate)
    candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_datatlist(html: str) -> Dict[str, Dict[str, Any]]:
    """Extract the ``DatatList = {...};`` object embedded in game pages."""

    match = re.search(r"DatatList\s*=\s*", html)
    if not match:
        return {}
    block = extract_braced(html, match.end())
    if not block:
        return {}
    data = parse_js_object(block)
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def int_keys(data: Optional[Dict[Any, Any]]) -> Dict[int, float]:
    result: Dict[int, float] = {}
    for key, value in (data or {}).items():
        try:
            result[int(key)] = parse_number(value)
        except (TypeError, ValueError):
            continue
    return result
