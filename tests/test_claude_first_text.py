"""first_text must skip thinking blocks that newer models emit before the answer."""
from types import SimpleNamespace

from app.utils.claude import first_text


def _resp(*blocks):
    return SimpleNamespace(content=[SimpleNamespace(**b) for b in blocks])


def test_skips_leading_thinking_block():
    resp = _resp({"type": "thinking", "thinking": "..."}, {"type": "text", "text": "2"})
    assert first_text(resp) == "2"


def test_plain_text_response():
    assert first_text(_resp({"type": "text", "text": "1"})) == "1"


def test_no_text_block_returns_empty():
    assert first_text(_resp({"type": "thinking", "thinking": "..."})) == ""
