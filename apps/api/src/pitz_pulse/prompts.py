"""Versioned prompt files: prompts/<version>.md with system, user and feedback sections."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

from pitz_pulse.masking import MaskedRequest, normalize

TOOL_NAME = "record_classification"
_SECTION = re.compile(r"^<!-- section: (\w+) -->$", re.MULTILINE)
_PLACEHOLDERS = {"system": set(), "user": {"source_area", "message"}, "feedback": {"errors"}}
_TAG = re.compile(r"<\s*/?\s*(?:message|source_area|feedback)\b", re.IGNORECASE)
_FILE_MENTION = re.compile(r"(?<!\S)@(?=[/~.\w])")


class PromptError(ValueError):
    pass


def neutralize(text: str) -> str:
    """Make delimiter-tag lookalikes and @file mentions inert."""
    text = _TAG.sub(lambda match: "&lt;" + match.group(0)[1:], normalize(text))
    return _FILE_MENTION.sub("(at)", text)


@dataclass(frozen=True)
class Prompt:
    version: str
    system: str
    user_template: Template
    feedback_template: Template
    sha256: str

    def render_user(self, masked: MaskedRequest, feedback: str | None = None) -> str:
        user = self.user_template.substitute(
            source_area=neutralize(masked.source_area or ""),
            message=neutralize(masked.message),
        )
        if feedback:
            user += "\n\n" + self.feedback_template.substitute(errors=neutralize(feedback))
        return user


def _sections(text: str) -> dict[str, str]:
    parts = _SECTION.split(text)
    if parts[0].strip():
        raise PromptError("prompt has content before the first section marker")
    names = parts[1::2]
    if len(names) != len(set(names)):
        raise PromptError("prompt has a duplicate section")
    sections = {name: body.strip() for name, body in zip(names, parts[2::2], strict=True)}
    if set(sections) != set(_PLACEHOLDERS):
        raise PromptError(f"prompt must have sections {sorted(_PLACEHOLDERS)}")
    for name, expected in _PLACEHOLDERS.items():
        template = Template(sections[name])
        if not template.is_valid() or set(template.get_identifiers()) != expected:
            raise PromptError(
                f"section {name} must use exactly the placeholders {sorted(expected)}"
            )
    return sections


def load_prompt(app_root: Path, version: str) -> Prompt:
    if not re.fullmatch(r"v\d+", version):
        raise PromptError("prompt version must look like v1, v2, ...")
    path = app_root / "prompts" / f"{version}.md"
    if not path.is_file():
        raise PromptError(f"missing prompt file prompts/{version}.md")
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    sections = _sections(text)
    return Prompt(
        version=version,
        system=sections["system"],
        user_template=Template(sections["user"]),
        feedback_template=Template(sections["feedback"]),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
