"""Small helpers for reading Claude API responses."""
from __future__ import annotations


def first_text(resp) -> str:
    """
    Return the text of the first text block in a Messages API response.

    Newer models can put a thinking block before the answer, so content[0]
    is not guaranteed to be text. Returns "" if the response has no text block.
    """
    for block in resp.content:
        if getattr(block, "type", "") == "text":
            return block.text
    return ""
