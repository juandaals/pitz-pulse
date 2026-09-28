"""Near-duplicate resubmission detection (Spec 06c, X3): stdlib only, read-only, best-effort.

Topic-level similarity ("third case like this this month") would need embeddings; out of scope
here (documented upgrade path). This module only compares one message's text against a bounded
set of recently classified candidates and returns the closest match above a threshold, if any.
"""

import re
import unicodedata
from difflib import SequenceMatcher

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
_TRUNCATE_CHARS = 1000


def normalize(text: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace, truncate to 1000 chars."""
    text = text.lower()
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    without_punctuation = _PUNCTUATION.sub("", without_accents)
    collapsed = _WHITESPACE.sub(" ", without_punctuation).strip()
    return collapsed[:_TRUNCATE_CHARS]


class DuplicateDetector:
    """State: classified -> checked -> flagged | unique (Spec 06c)."""

    def __init__(self, window: int = 200, threshold: float = 0.85, min_len: int = 30):
        self.window = window
        self.threshold = threshold
        self.min_len = min_len

    def find(self, message: str, candidates: list[tuple[str, str]]) -> str | None:
        """Best candidate id whose text is a near-duplicate of `message`, or None.

        `candidates` is (id, text) pairs, most-recent first; only the first `window` are
        considered. Never returns an id outside `candidates` (in particular, never the current
        request's own id, since the caller never includes it there).
        """
        normalized = normalize(message)
        if len(normalized) < self.min_len:
            return None
        best_id, best_ratio = None, 0.0
        for candidate_id, candidate_text in candidates[: self.window]:
            candidate_normalized = normalize(candidate_text)
            matcher = SequenceMatcher(None, normalized, candidate_normalized, autojunk=False)
            if matcher.real_quick_ratio() < self.threshold:
                continue
            if matcher.quick_ratio() < self.threshold:
                continue
            ratio = matcher.ratio()
            if ratio >= self.threshold and ratio > best_ratio:
                best_id, best_ratio = candidate_id, ratio
        return best_id
