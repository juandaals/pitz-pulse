"""`.github/workflows/ci.yml` pins every action to a real, unambiguous ref (Spec 06a).

A moving major-version tag like `@v10` never existed for `astral-sh/setup-uv` (only full
`vMAJOR.MINOR.PATCH` tags are published), so CI failed to even start. This test parses the
workflow text with a plain regex (no PyYAML dependency, no network) and asserts every `uses:` ref
is a full semantic-version tag or a 40-character commit SHA — either resolves unambiguously.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_USES_LINE = re.compile(r"^\s*-\s*uses:\s*(\S+)\s*$", re.MULTILINE)
_FULL_SEMVER_TAG = re.compile(r"^v\d+\.\d+\.\d+$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _uses_refs() -> list[tuple[str, str]]:
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    refs = []
    for match in _USES_LINE.finditer(text):
        action, _, ref = match.group(1).partition("@")
        refs.append((action, ref))
    return refs


def test_workflow_file_exists():
    assert CI_WORKFLOW.is_file()


def test_every_uses_ref_is_a_full_tag_or_a_sha():
    refs = _uses_refs()
    assert refs, "no `uses:` lines found; the parser regex or the workflow file changed"
    for action, ref in refs:
        assert _FULL_SEMVER_TAG.fullmatch(ref) or _FULL_SHA.fullmatch(ref), (
            f"{action}@{ref} is not a full vMAJOR.MINOR.PATCH tag or a 40-char SHA "
            "(a moving major tag like @v10 is not guaranteed to exist)"
        )


def test_setup_uv_and_checkout_are_present():
    actions = {action for action, _ in _uses_refs()}
    assert "actions/checkout" in actions
    assert "astral-sh/setup-uv" in actions
