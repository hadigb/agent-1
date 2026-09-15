"""Token estimation without external dependencies.

Uses ``tiktoken`` when it happens to be installed (good approximation for
OpenAI models, reasonable for others); otherwise a character-class heuristic
that is deliberately slightly pessimistic for Persian text and source code.
"""
from __future__ import annotations

import re
from typing import Optional

_enc = None
_tried = False


def _get_encoder():
    global _enc, _tried
    if _tried:
        return _enc
    _tried = True
    try:  # pragma: no cover - optional dependency
        import tiktoken  # type: ignore

        _enc = tiktoken.get_encoding("o200k_base")
    except Exception:
        _enc = None
    return _enc


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]")
_PERSIAN = re.compile(r"[؀-ۿ]")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:  # pragma: no cover
            pass
    # heuristic: ~1 token per 3.6 chars for code/English, ~1 per 2.2 chars for Persian
    persian_chars = len(_PERSIAN.findall(text))
    other_chars = len(text) - persian_chars
    return int(other_chars / 3.6 + persian_chars / 2.2) + 1


def truncate_to_tokens(text: str, max_tokens: int, marker: str = "\n// ... [truncated] ...\n") -> str:
    if estimate_tokens(text) <= max_tokens:
        return text
    # binary search on character length
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    cut = text[:lo]
    nl = cut.rfind("\n")
    if nl > lo * 0.6:
        cut = cut[:nl]
    return cut + marker
