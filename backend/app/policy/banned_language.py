"""Compliance check on drafted reminder copy. Doc §5.

This runs on the text a language model produced, after generation and before anything
is sent. The model being well-behaved is not the safety mechanism — this is. A model
that has been polite for a thousand drafts can still produce a threat on the next one,
and "no threatening or legal-action language, ever" is a compliance rule, not a
preference.

Matching is done on a normalized copy of the text so the obvious evasions do not work:
spaced-out letters, punctuation between characters, repeated whitespace, and unicode
lookalikes all collapse to the same form before the patterns run.
"""

import re
import unicodedata

#: Phrases a debt-collection reminder may never contain. Grouped by what they threaten.
BANNED_PATTERNS: dict[str, str] = {
    # Legal threats
    r"\blegal\s*action\b": "legal action",
    r"\blegal\s*proceedings?\b": "legal proceedings",
    r"\blawyer\b": "lawyer",
    r"\battorney\b": "attorney",
    r"\bcourt\b": "court",
    r"\bsue\b": "sue",
    r"\bsuing\b": "suing",
    r"\blitigation\b": "litigation",
    r"\bprosecut": "prosecute",
    r"\bsummons\b": "summons",
    # Criminal / enforcement threats
    r"\bpolice\b": "police",
    r"\bcriminal\b": "criminal",
    r"\bfraud\b": "fraud",
    r"\bseize\b": "seize",
    r"\bconfiscat": "confiscate",
    r"\brecovery\s*agent\b": "recovery agent",
    r"\bdebt\s*collector\b": "debt collector",
    # Reputational threats
    r"\bblacklist": "blacklist",
    r"\bcredit\s*bureau\b": "credit bureau",
    r"\bcredit\s*rating\b": "credit rating",
    r"\bcibil\b": "CIBIL",
    r"\bdefaulter\b": "defaulter",
    r"\breport\s*you\b": "report you",
    # Coercive tone
    r"\bfinal\s*warning\b": "final warning",
    r"\bwarn(?:ing)?\s*you\b": "warning you",
    r"\bor\s*else\b": "or else",
    r"\bconsequences\b": "consequences",
    r"\bimmediately\s*or\b": "immediately or",
    r"\bfailure\s*to\s*comply\b": "failure to comply",
}

_COMPILED = [(re.compile(p, re.IGNORECASE), label) for p, label in BANNED_PATTERNS.items()]

#: Characters used to break up words while staying readable. Stripped before matching
#: so "l-e-g-a-l action" and "l e g a l action" both resolve to "legal action".
_SEPARATORS = re.compile(r"[\.\-_*|/\\+~`'\"]+")
_WHITESPACE = re.compile(r"\s+")
_SPACED_LETTERS = re.compile(r"\b(?:\w\s){2,}\w\b")


def normalize(text: str) -> str:
    """Collapse a message to the form the patterns are matched against.

    NFKD folding turns unicode lookalikes into their ASCII equivalents, so a homoglyph
    substitution does not slip a banned phrase past the filter.
    """
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = _SEPARATORS.sub("", folded)
    # Re-join single letters separated by spaces: "l e g a l" -> "legal".
    folded = _SPACED_LETTERS.sub(lambda m: m.group(0).replace(" ", ""), folded)
    return _WHITESPACE.sub(" ", folded).strip().lower()


def find_banned_phrases(text: str) -> list[str]:
    """Every banned phrase present, by readable label.

    Returns all matches rather than the first: the regeneration prompt needs to name
    each one, and a draft fixed for a single phrase that still contains two others
    would just fail again.
    """
    haystack = normalize(text)
    return sorted({label for pattern, label in _COMPILED if pattern.search(haystack)})
