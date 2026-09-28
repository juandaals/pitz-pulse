from pitz_pulse.duplicates import DuplicateDetector, normalize

LONG_A = "Necesito acceso urgente a mi cuenta de correo corporativo para continuar"
LONG_B = "necesito acceso urgente a mi cuenta de correo corporativo para continuar."
DIFFERENT = "El sistema de facturacion no genera el reporte mensual de ventas"


def detector(**overrides):
    options = {"window": 200, "threshold": 0.85, "min_len": 30}
    options.update(overrides)
    return DuplicateDetector(**options)


def test_normalize_lowercases_strips_accents_punctuation_and_collapses_spaces():
    assert normalize("¡Cómo, ES esto!!  Múltiples   espacios.") == "como es esto multiples espacios"


def test_normalize_truncates_to_300_chars():
    assert normalize("a" * 2000) == "a" * 300


def test_identical_message_is_flagged():
    assert detector().find(LONG_A, [("r1", LONG_A)]) == "r1"


def test_small_edit_is_flagged():
    assert detector().find(LONG_A, [("r1", LONG_B)]) == "r1"


def test_different_wording_is_not_flagged():
    assert detector().find(LONG_A, [("r1", DIFFERENT)]) is None


def test_short_message_guard_skips_detection_entirely():
    short = "hola buenas"  # well under min_len
    assert detector().find(short, [("r1", short)]) is None


def test_window_bound_ignores_candidates_past_the_window():
    small_window = detector(window=1)
    candidates = [
        ("far", "unrelated text that will not match anything at all here"),
        ("r1", LONG_A),
    ]
    assert small_window.find(LONG_A, candidates) is None
    assert detector(window=2).find(LONG_A, candidates) == "r1"


def test_never_flags_itself_when_there_are_no_other_candidates():
    # The current row is still `pending` (not yet classified) when the check runs, so it can
    # never appear among candidates; with nothing else to compare against, nothing is flagged.
    assert detector().find(LONG_A, []) is None


def test_returns_the_best_match_among_several_candidates():
    close_edit = LONG_A.replace("urgente", "urgentee")  # still >= threshold, but not a perfect 1.0
    exact = LONG_A
    result = detector().find(LONG_A, [("close", close_edit), ("exact", exact)])
    assert result == "exact"


def test_quick_ratio_prefilter_does_not_change_results():
    """The real_quick_ratio/quick_ratio prefilters are upper bounds on ratio(); using them to
    skip candidates early must never change which id (if any) is returned, compared to scoring
    every candidate with the full ratio() alone."""
    from difflib import SequenceMatcher

    from pitz_pulse.duplicates import normalize

    candidates = [
        ("a", LONG_A),
        ("b", LONG_B),
        ("c", DIFFERENT),
        ("d", "Necesito acceso urgente a mi cuenta de correo personal para continuar"),
        ("e", "otra solicitud completamente distinta sobre reportes de ventas mensuales"),
    ]

    def naive_find(message, candidates, threshold):
        normalized = normalize(message)
        best_id, best_ratio = None, 0.0
        for candidate_id, candidate_text in candidates:
            ratio = SequenceMatcher(
                None, normalized, normalize(candidate_text), autojunk=False
            ).ratio()
            if ratio >= threshold and ratio > best_ratio:
                best_id, best_ratio = candidate_id, ratio
        return best_id

    assert detector().find(LONG_A, candidates) == naive_find(LONG_A, candidates, 0.85)


def test_full_window_of_long_same_language_texts_stays_under_budget():
    """200 candidates, each ~1000 raw chars of similar Spanish vocabulary (the worst case for the
    real_quick_ratio length-based prefilter, since every candidate is close in length): the
    Jaccard word-set prefilter and the 300-char truncation must keep `find` fast."""
    import random
    import time as _time

    vocabulary = (
        "reporte factura cliente sistema acceso cuenta urgente soporte problema equipo "
        "solicitud proceso datos usuario correo registro pago servicio pedido incidente "
        "formulario contrato ticket revision entrega envio devolucion saldo inventario "
        "almacen producto vendedor comprador orden confirmacion notificacion alerta"
    ).split()

    def make_text(seed: int) -> str:
        words = random.Random(seed).choices(vocabulary, k=160)  # ~1000 chars
        return " ".join(words)

    candidates = [(f"r{i}", make_text(i)) for i in range(200)]
    message = make_text(999)

    start = _time.perf_counter()
    detector().find(message, candidates)
    elapsed = _time.perf_counter() - start

    assert elapsed < 0.5
