"""Early media bounds must fail before expensive Base64 decoding."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from chatbridge.media import (
    MAX_ATTACHMENT_BYTES,
    MAX_CONTENT_PARTS,
    MAX_IMAGE_BYTES,
    MediaError,
    encoded_limit,
    parse_data_url,
    parse_message,
    read_file_objects,
    read_image_objects,
)


class OversizedString(str):
    """Short allocation emulating a huge client string for predecode tests."""

    def __len__(self):
        return 100 * 1024 * 1024


def test_predecode_image_bytes_rejected_without_base64_decode():
    with patch("chatbridge.media.base64.b64decode") as decode:
        with pytest.raises(MediaError, match="size"):
            read_image_objects([{"data_base64": OversizedString("QQ==")}])
    decode.assert_not_called()


def test_data_url_oversized_before_regex_and_decoder():
    with patch("chatbridge.media.base64.b64decode") as decode:
        with pytest.raises(MediaError, match="size"):
            parse_data_url(OversizedString("data:image/png;base64,QQ=="))
    decode.assert_not_called()


def test_predecode_attachment_bytes_rejected():
    with patch("chatbridge.media.base64.b64decode") as decode:
        with pytest.raises(MediaError, match="size"):
            read_file_objects([{"filename": "data.bin", "data_base64": OversizedString("QQ==")}])
    decode.assert_not_called()


def test_predecode_attachment_data_url_rejected():
    with patch("chatbridge.media.base64.b64decode") as decode:
        with pytest.raises(MediaError, match="size"):
            read_file_objects([{"filename": "data.pdf",
                                "data_url": OversizedString("data:application/pdf;base64,QQ==")}])
    decode.assert_not_called()


def test_part_count_rejected_before_contents():
    with pytest.raises(MediaError, match="content parts"):
        parse_message({"content": [{"type": "text", "text": "x"}] * (MAX_CONTENT_PARTS + 1)})


def test_normal_invalid_base64_still_errors():
    with pytest.raises(MediaError, match="Invalid base64"):
        read_file_objects([{"filename": "data.txt", "data_base64": "!!!"}])


def test_limit_formula_is_exact():
    assert encoded_limit(MAX_ATTACHMENT_BYTES) == 4 * ((MAX_ATTACHMENT_BYTES + 2) // 3)
    assert encoded_limit(MAX_IMAGE_BYTES) == 4 * ((MAX_IMAGE_BYTES + 2) // 3)
