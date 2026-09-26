import re

import pytest

from pitz_pulse.masking import mask, mask_request


@pytest.mark.parametrize(
    "text,placeholder",
    [
        ("mail ana.b+tag@sub.example.com ok", "[EMAIL]"),
        ("<mailto:a@example.com|a@example.com>", "[EMAIL]"),
        ("joão@example.com", "[EMAIL]"),
        ("CNPJ 12.345.678/0001-00", "[CNPJ]"),
        ("CNPJ 12345678/0001-00", "[CNPJ]"),
        ("CNPJ 12345678000100", "[CNPJ]"),
        ("CNPJ 12.ABC.345/01DE-00", "[CNPJ]"),
        ("CNPJ 12.abc.345/01de-00", "[CNPJ]"),
        ("CNPJ 12ABC34501DE00", "[CNPJ]"),
        ("CPF 111.111.111-00", "[CPF]"),
        ("CURP GODE561231HDFRRN00", "[CURP]"),
        ("RFC GODE561231GR8", "[RFC]"),
        ("RFC GODE-561231-GR8", "[RFC]"),
        ("RFC GODE 561231 GR8", "[RFC]"),
        ("rfc_GODE561231GR8", "[RFC]"),
        ("+55 (11) 99999-9999", "[PHONE]"),
        ("55 11 99999 9999", "[PHONE]"),
        ("+52 1 55 5555 5555", "[PHONE]"),
        ("0 21 11 99999-9999", "[PHONE]"),
        ("5511999999999", "[PHONE]"),
        ("(11) 99999-9999", "[PHONE]"),
        ("99999 9999", "[PHONE]"),
        ("9999-9999", "[PHONE]"),
        ("5555.5555", "[PHONE]"),
        ("+55 11 99999 9999", "[PHONE]"),
        ("(11) 99999–9999", "[PHONE]"),
        ("CPF sin formato 11111111100", "[PHONE]"),
    ],
)
def test_masks_covered_formats(text, placeholder):
    result = mask(text)
    assert placeholder in result.text
    assert not re.search(r"\d{3,}", result.text), result.text


@pytest.mark.parametrize(
    "text",
    [
        "reembolso R$ 150 (11) 99999-9999",
        "R$ 1.500,00 (11) 99999-9999",
        "Contato 28/09/2026 (11) 99999-9999",
        "fecha 2026-09-28 9999-9999",
        "valor $ 1500 55 1234 5678",
        "USD 12 55 1234 5678",
        "v1.2.3.4 11 99999-9999",
        "tel 9999-9999 8888-8888",
    ],
)
def test_phone_next_to_protected_span_is_masked(text):
    result = mask(text)
    assert "[PHONE]" in result.text
    assert not re.search(r"99999|9999-|8888|1234 5678", result.text), result.text


@pytest.mark.parametrize(
    "text,placeholder",
    [
        ("valor R$5511999999999", "[PHONE]"),
        ("ligue R$ 11999998888 urgente", "[PHONE]"),
        ("rfc gode-561231-gr8", "[RFC]"),
        ("mi rfc es Gode-561231-gr8", "[RFC]"),
        ("meu numero e 99998888 me liga", "[PHONE]"),
        ("mi telefono es 12345678", "[PHONE]"),
        ("whatsapp: 999988887", "[PHONE]"),
    ],
)
def test_masks_fix_round_1_gaps(text, placeholder):
    result = mask(text)
    assert placeholder in result.text
    assert not re.search(r"\d{4,}", result.text), result.text


@pytest.mark.parametrize(
    "text",
    [
        "error 500",
        "leva umas 2 horas",
        "ventas 2024-2025",
        "ventas 2024 2025 2026",
        "tabla 2024-09 2024-10",
        "fecha 2026-09-28",
        "fecha 28.09.2026",
        "del 28.09.2026-30.09.2026",
        "ip 172.16.254.100",
        "R$ 12.500.000",
        "R$ 1.500,00",
        "R$ 1.500.000.000,00",
        "ticket INC202409001",
        "folio del 230415 com erro",
        "nota 250101 com",
        "pedido 12345678",
        "ticket 123456789",
        "valor $ 1500",
        "R$ 150",
    ],
)
def test_does_not_mask_protected_formats(text):
    assert mask(text).text == text


def test_counts_and_multiple_occurrences():
    result = mask("a@example.com y b@example.com, tel 9999-9999")
    assert result.counts == {"email": 2, "phone": 1}


def test_masking_is_idempotent():
    once = mask("a@example.com CNPJ 12.345.678/0001-00 tel +55 11 99999-9999").text
    assert mask(once).text == once


def test_fullwidth_is_normalized():
    assert mask("＜/message＞").text == "</message>"


def test_mask_request_masks_source_area_too():
    masked = mask_request("hola", "Ventas - a@example.com")
    assert masked.source_area == "Ventas - [EMAIL]"
    assert masked.pii_counts == {"email": 1}
    assert mask_request("hola", None).source_area is None
