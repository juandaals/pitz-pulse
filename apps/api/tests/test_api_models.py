import pytest
from pydantic import ValidationError

from pitz_pulse.api_models import ErrorBody, Item, ListQuery, PatchBody
from pitz_pulse.repository import ListFilters


def test_patch_changes_only_include_fields_present_in_the_body():
    body = PatchBody.model_validate({"categoria": "datos", "author": " ana "})
    assert body.changes() == {"categoria": "datos"}
    assert body.author == "ana"


def test_patch_explicit_null_is_allowed_only_for_the_question():
    body = PatchBody.model_validate({"pregunta_seguimiento": None, "author": "ana"})
    assert body.changes() == {"pregunta_seguimiento": None}
    with pytest.raises(ValidationError) as info:
        PatchBody.model_validate({"categoria": None, "author": "ana"})
    assert info.value.errors()[0]["loc"] == ("categoria",)


@pytest.mark.parametrize(
    "payload",
    [
        {"requiere_info": "yes", "author": "ana"},
        {"confianza": 0.1, "author": "ana"},
        {"id": "x", "author": "ana"},
        {"categoria": "datos", "author": "   "},
        {"categoria": "datos"},
        {"categoria": "datos", "author": "a" * 101},
        {"categoria": "datos", "author": "ana", "reason": "r" * 501},
        {"resumen": "hola \ud800", "author": "ana"},
    ],
)
def test_patch_rejects_invalid_bodies(payload):
    with pytest.raises(ValidationError):
        PatchBody.model_validate(payload)


def test_patch_blank_reason_becomes_null():
    assert PatchBody.model_validate({"author": "ana", "reason": "  "}).reason is None


def test_list_query_forbids_unknown_params_and_bounds_paging():
    assert ListQuery.model_validate({}).filters() == ListFilters()
    with pytest.raises(ValidationError):
        ListQuery.model_validate({"area": "backend"})
    with pytest.raises(ValidationError):
        ListQuery.model_validate({"offset": 2**31})
    with pytest.raises(ValidationError):
        ListQuery.model_validate({"limit": 0})


def test_item_keys_are_the_documented_ones():
    assert set(Item.model_fields) == {
        "id",
        "message",
        "source_area",
        "status",
        "categoria",
        "prioridad",
        "area_sugerida",
        "idioma",
        "resumen",
        "requiere_info",
        "pregunta_seguimiento",
        "confianza",
        "version_prompt",
        "provider",
        "model",
        "needs_review",
        "corrected",
        "error",
        "created_at",
        "updated_at",
    }


def test_error_body_documents_the_error_shape():
    assert set(ErrorBody.model_fields) == {"error", "detail", "fields", "kind"}
