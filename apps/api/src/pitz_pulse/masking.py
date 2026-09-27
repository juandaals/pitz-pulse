"""PII masking applied before any text leaves the process (spec 01 §8.6).

Covered: email, CNPJ (numeric and 2026 alphanumeric), CPF, CURP, RFC, BR/MX phones.
Not covered: names, addresses, CLABE/bank accounts, cards, NF-e keys, obfuscated emails,
bare 8-9 digit phones without a phone keyword, lowercase space-separated RFCs.
An IPv4 address of the exact CPF shape (3.3.3.2 digits) is masked as [CPF] by design.
Unicode format characters (category Cf: zero-width spaces, soft hyphens, BOM...) are removed
before NFKC so they cannot split PII. The "número"/"numero" phone keyword over-masks order or
ticket numbers of 8-9 digits by design (privacy first, D14); only the prompt copy is masked.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")

_EMAIL = re.compile(r"[\w.+%-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4 = rf"(?<!\d){_OCTET}(?:\.{_OCTET}){{3}}(?!\d)"
# Separators are tolerated anywhere (typos like "12.345.678.0001-00" are still CNPJs).
_CNPJ = re.compile(
    r"(?<![0-9A-Za-z])[0-9A-Z]{2}[./-]?[0-9A-Z]{3}[./-]?[0-9A-Z]{3}[./-]?[0-9A-Z]{4}[./-]?\d{2}"
    r"(?![0-9A-Za-z])",
    re.IGNORECASE,
)
# The last separator is mandatory so bare 11-digit numbers stay phones. Runs before the IPv4
# guard: an address of this exact shape is masked as [CPF] (privacy first).
_CPF = re.compile(r"(?<![0-9A-Za-z])\d{3}[.-]?\d{3}[.-]?\d{3}[.-]\d{2}(?![0-9A-Za-z])")
_CURP = re.compile(
    r"(?<![0-9A-Za-z])[A-Z]{4}\d{6}[HMX][A-Z]{5}[A-Z0-9]\d(?![0-9A-Za-z])", re.IGNORECASE
)
_RFC_COMPACT = re.compile(
    r"(?<![0-9A-Za-z])[A-ZÑ&]{3,4}(\d{6})[A-Z0-9]{3}(?![0-9A-Za-z])", re.IGNORECASE
)
# Hyphens are an unambiguous RFC marker, so this form is case-insensitive.
_RFC_SEPARATED_HYPHEN = re.compile(
    r"(?<![0-9A-Za-z])[A-ZÑ&]{3,4}-(\d{6})-[A-Z0-9]{3}(?![0-9A-Za-z])", re.IGNORECASE
)
# Uppercase only: a case-insensitive space-separated form would eat prose like "del 230415 com".
_RFC_SEPARATED_SPACE = re.compile(
    r"(?<![0-9A-Za-z])[A-ZÑ&]{3,4}\s(\d{6})\s[A-Z0-9]{3}(?![0-9A-Za-z])"
)
# Local form first so "9999-9999 8888-8888" is two phones, not one greedy match.
# Neighbours are ASCII alphanumerics, not \w: "_" (Markdown italics) must not shield a phone.
_PHONE = re.compile(
    r"(?<![0-9A-Za-z-])\d{4,5}[-. ]\d{4}(?![0-9A-Za-z-])"
    r"|(?<![0-9A-Za-z+])(?:\+|\()?\d(?:[\s().-]*\d){9,13}(?!\d)"
)
# Bare 8-9 digit local numbers have no distinguishing shape (they collide with ticket/order
# IDs), so they are only masked when a phone keyword precedes them. Up to 3 non-space
# connector characters (":", "-", or a short word like "es"/"e") may sit between the keyword
# and the digits; surrounding whitespace is unlimited. Separated local forms with an optional
# area code ("(11) 2045-2078") are accepted here because the year guards would claim them later.
_PHONE_KEYWORD = re.compile(
    r"(?<![0-9A-Za-z])(?:tel[eé]fono|telefone|tel|celular|cel|whatsapp|whats|"
    r"n[uú]mero|fone|ligue|llame|llamar)(?![A-Za-z])\s*[^\d\s]{0,3}\s*"
    r"((?:(?:\(\d{2}\)|\d{2})\s?)?\d{4,5}[-. ]\d{4}|\d{8,9})(?!\d)",
    re.IGNORECASE,
)
# Spans never masked as phones; phones are searched only in the text between them.
_GUARDS = (
    re.compile(r"(?<!\d)(?:19|20)\d{2}(?:(?:\s*[-,/]\s*|\s+)(?:19|20)\d{2})+(?!\d)"),  # years
    re.compile(r"(?<!\d)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])(?!\d)"),  # year-month
    re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)"),  # ISO dates
    re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}[./]\d{2,4}(?!\d)"),  # dotted / slashed dates
    re.compile(_IPV4),  # IPv4 / short version strings (octets <= 255 only)
    # Amounts: bounded to a plausible number so it can't swallow an adjacent phone number
    # that happens to sit right after a currency symbol with no separating space.
    re.compile(
        r"(?:R\$|US\$|MXN|BRL|USD|\$)\s?(?:\d{1,3}(?:[.,]\d{3})+|\d{1,6})(?:[.,]\d{1,2})?(?!\d)"
    ),
)
_SIMPLE_RULES = (("email", _EMAIL), ("cnpj", _CNPJ), ("cpf", _CPF), ("curp", _CURP))


@dataclass(frozen=True)
class MaskResult:
    text: str
    counts: dict[str, int]


@dataclass(frozen=True)
class MaskedRequest:
    message: str
    source_area: str | None
    pii_counts: dict[str, int]


def normalize(text: str) -> str:
    visible = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return unicodedata.normalize("NFKC", visible).translate(_DASHES)


def mask(text: str) -> MaskResult:
    current = normalize(text)
    counts: dict[str, int] = {}
    for kind, pattern in _SIMPLE_RULES:
        current, found = pattern.subn(f"[{kind.upper()}]", current)
        _add(counts, kind, found)
    for pattern in (_RFC_COMPACT, _RFC_SEPARATED_HYPHEN, _RFC_SEPARATED_SPACE):
        current, found = _mask_rfc(pattern, current)
        _add(counts, "rfc", found)
    current, found = _mask_keyword_phones(current)
    _add(counts, "phone", found)
    current, found = _mask_phones(current)
    _add(counts, "phone", found)
    return MaskResult(current, counts)


def mask_request(message: str, source_area: str | None) -> MaskedRequest:
    masked_message = mask(message)
    counts = dict(masked_message.counts)
    masked_area = None
    if source_area is not None:
        area = mask(source_area)
        masked_area = area.text
        for kind, found in area.counts.items():
            _add(counts, kind, found)
    return MaskedRequest(masked_message.text, masked_area, counts)


def _add(counts: dict[str, int], kind: str, found: int) -> None:
    if found:
        counts[kind] = counts.get(kind, 0) + found


def _is_date(yymmdd: str) -> bool:
    try:
        datetime.strptime(yymmdd, "%y%m%d")
    except ValueError:
        return False
    return True


def _mask_rfc(pattern: re.Pattern[str], text: str) -> tuple[str, int]:
    found = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal found
        if not _is_date(match.group(1)):
            return match.group(0)
        found += 1
        return "[RFC]"

    return pattern.sub(replace, text), found


def _mask_keyword_phones(text: str) -> tuple[str, int]:
    found = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal found
        found += 1
        kept = match.group(0)[: match.start(1) - match.start()]
        return kept + "[PHONE]"

    return _PHONE_KEYWORD.sub(replace, text), found


def _guard_spans(text: str) -> list[tuple[int, int]]:
    spans = sorted(m.span() for guard in _GUARDS for m in guard.finditer(text))
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _mask_phones(text: str) -> tuple[str, int]:
    parts, found, cursor = [], 0, 0
    for start, end in [*_guard_spans(text), (len(text), len(text))]:
        segment, count = _PHONE.subn("[PHONE]", text[cursor:start])
        parts += [segment, text[start:end]]
        found += count
        cursor = end
    return "".join(parts), found
