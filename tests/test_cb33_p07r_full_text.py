import pytest
from chatbridge.media import parse_message, MediaError


@pytest.mark.parametrize("size", [130000, 1000000])
def test_single_text_message_is_accepted_verbatim_at_scale(size):
    ending = "CB33_P07R_LAST_MARKER"
    content = "Ж"*(size-len(ending)) + ending
    text, images = parse_message({"content": content})
    assert text == content
    assert len(text)==size
    assert text.endswith(ending)
    assert images == []


def test_non_text_message_still_rejected():
    with pytest.raises(MediaError):
        parse_message({"content":42})
