import pytest

from pitz_pulse.review import needs_review


@pytest.mark.parametrize(
    ("confianza", "expected"), [(0.69, True), (0.7, False), (0.71, False), (0.0, True)]
)
def test_below_threshold_needs_review_equal_or_above_does_not(confianza, expected):
    assert needs_review(confianza, 0.7) is expected
