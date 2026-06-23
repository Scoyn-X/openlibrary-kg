"""Name splitting for identifiers.

Handles snake_case, CamelCase, and mixed-case identifiers,
splitting them into individual word tokens. Filtering is delegated
to `noun_filter.filter_tokens`, which applies the hard blocklist
(stop words + stdlib + builtins + framework symbols).
"""

from __future__ import annotations

import re

from openlibrary_kg.extraction.noun_filter import HARD_BLOCKLIST, filter_tokens

# Matches: ABCDef, abcDef, ABC (acronym), abc123, ABC123
_CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|$)")


def split_identifier(name: str) -> list[str]:
    """Split an identifier into its constituent word tokens.

    >>> split_identifier("get_user_name")
    ['get', 'user', 'name']
    >>> split_identifier("getUserBookData")
    ['get', 'user', 'book', 'data']
    >>> split_identifier("ISBN_13_check")
    ['isbn', '13', 'check']
    >>> split_identifier("")
    []
    """
    if not name:
        return []

    segments = name.split("_")

    tokens: list[str] = []
    for seg in segments:
        if not seg:
            continue
        if "_" in seg:
            tokens.append(seg.lower())
        else:
            parts = _CAMEL_RE.findall(seg)
            if parts:
                tokens.extend(p.lower() for p in parts if p)
            else:
                tokens.append(seg.lower())

    return tokens


def split_name_filter_nouns(
    raw_name: str,
    stop_words: set[str] | None = None,
    keep_abbreviations: set[str] | None = None,
    min_length: int = 2,
) -> str:
    """Split a raw identifier and return only noun-like tokens as a joined string.

    The previous version only checked a small stop-words list; this version
    delegates to `noun_filter.filter_tokens`, which uses HARD_BLOCKLIST by
    default (stop words + Python builtins/methods + stdlib modules +
    web.py/infogami framework symbols).

    Returns the filtered tokens joined by underscore (e.g., "user_name").
    Returns empty string if all tokens are filtered out.
    """
    tokens = split_identifier(raw_name)
    if not tokens:
        return ""

    # Caller can pass an extended set, but by default we use HARD_BLOCKLIST.
    block = stop_words if stop_words is not None else HARD_BLOCKLIST
    keep_abbreviations = keep_abbreviations or set()

    filtered = filter_tokens(
        tokens,
        stop_words=block,
        keep_abbreviations=keep_abbreviations,
        min_length=min_length,
    )
    return "_".join(filtered)
