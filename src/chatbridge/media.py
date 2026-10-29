"""Multimodal image validation, transfer and persistence contracts.

All image data comes from client-provided bytes, never arbitrary server-side URLs.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import re
from dataclasses import dataclass

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES_PER_MESSAGE = 8
MAX_ATTACHMENTS_PER_MESSAGE = 12
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 45 * 1024 * 1024
MAX_CONTENT_PARTS = 128


def encoded_limit(raw_bytes: int) -> int:
    """Maximum canonical Base64 characters before decoding into memory."""
    return ((raw_bytes + 2) // 3) * 4


def check_encoded_size(encoded: str, raw_limit: int, *, label: str) -> None:
    # Includes any optional whitespace. Very large input must be rejected
    # before normalization or base64.b64decode allocates a large buffer.
    if len(encoded) > encoded_limit(raw_limit) + 1024:
        raise MediaError(f"{label} exceeds allowed payload size")
MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class MediaError(ValueError):
    """Invalid or unsupported image payload."""


@dataclass(frozen=True)
class ImagePayload:
    name: str
    mime_type: str
    data: bytes

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise MediaError("Image content must be PNG, JPEG, WebP or GIF")


def verify_image(data: bytes, name: str = "image", declared_type: str = "") -> ImagePayload:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise MediaError(f"Image must be between 1 and {MAX_IMAGE_BYTES} bytes")
    mime = image_type(data)
    if declared_type and declared_type.lower().split(";", 1)[0] != mime:
        raise MediaError("Declared MIME type differs from image content")
    name = re.sub(r"[^\w.\-]", "_", str(name or "image"))[:128].strip(".") or "image"
    if not name.lower().endswith(MIME_EXT[mime]):
        name += MIME_EXT[mime]
    return ImagePayload(name=name, mime_type=mime, data=bytes(data))


def parse_data_url(value: str, name: str = "image") -> ImagePayload:
    if not isinstance(value, str):
        raise MediaError("Image data URL must be a string")
    if len(value) > encoded_limit(MAX_IMAGE_BYTES) + 1152:
        raise MediaError("Image base64 payload exceeds allowed size")
    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=\s]+)", value)
    if not match:
        raise MediaError("Only inline base64 image data URLs are accepted")
    try:
        raw = base64.b64decode("".join(match.group(2).split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MediaError("Invalid base64 image") from exc
    return verify_image(raw, name=name, declared_type=match.group(1))


def read_image_objects(items: list[dict]) -> list[ImagePayload]:
    if not isinstance(items, list) or len(items) > MAX_IMAGES_PER_MESSAGE:
        raise MediaError(f"Maximum {MAX_IMAGES_PER_MESSAGE} images per message")
    result = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise MediaError("Image must be an object")
        name = item.get("filename") or item.get("name") or f"image-{i + 1}"
        value = item.get("data_url") or item.get("url")
        if value is not None:
            if not isinstance(value, str):
                raise MediaError("Image URL must be a string")
            result.append(parse_data_url(value, name))
            continue
        data = item.get("data_base64")
        if not isinstance(data, str):
            raise MediaError("Image requires data_url or data_base64")
        check_encoded_size(data, MAX_IMAGE_BYTES, label="Image base64")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MediaError("Invalid base64 image") from exc
        result.append(verify_image(raw, name, item.get("mime_type", "")))
    return result


def verify_file(data: bytes, name: str = "file", declared_type: str = "") -> ImagePayload:
    """Accept arbitrary user documents/audio; defer support decisions to ChatGPT UI.

    Do not fetch arbitrary client-supplied network URLs. This is intentionally
    not an assertion that the current account can ingest every file format.
    """
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise MediaError(f"File must be between 1 and {MAX_ATTACHMENT_BYTES} bytes")
    clean = re.sub(r"[^\w.\-]", "_", str(name or "file"))[:128].strip(".") or "file"
    if not clean.count("."):
        clean += ".bin"
    guessed = mimetypes.guess_type(clean)[0] or "application/octet-stream"
    mime = declared_type.split(";", 1)[0].strip().lower() or guessed
    if mime.startswith("image/"):
        return verify_image(data, clean, mime)
    # Do not claim a file is an ordinary document if its filename hides a binary.
    if mime == "application/pdf" and not data.startswith(b"%PDF-"):
        raise MediaError("Invalid PDF header")
    if mime in {"audio/wav", "audio/x-wav", "audio/wave"} and not (
        data.startswith(b"RIFF") and data[8:12] == b"WAVE"
    ):
        raise MediaError("Invalid WAV header")
    return ImagePayload(name=clean, mime_type=mime, data=bytes(data))


def read_file_objects(items: list[dict]) -> list[ImagePayload]:
    if not isinstance(items, list) or len(items) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise MediaError("Too many attachments")
    result = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise MediaError("Attachment must be an object")
        name = item.get("filename") or item.get("name") or f"attachment-{i + 1}.bin"
        encoded = item.get("data_base64")
        if isinstance(item.get("data_url"), str):
            if len(item["data_url"]) > encoded_limit(MAX_ATTACHMENT_BYTES) + 1152:
                raise MediaError("Attachment base64 payload exceeds allowed size")
            match = re.fullmatch(r"data:([^;,]+);base64,([A-Za-z0-9+/=\s]+)", item["data_url"])
            if not match:
                raise MediaError("Only base64 data URLs are supported")
            declared = match.group(1)
            encoded = "".join(match.group(2).split())
        else:
            declared = item.get("mime_type", "")
        if not isinstance(encoded, str):
            raise MediaError("Attachment requires base64 data")
        check_encoded_size(encoded, MAX_ATTACHMENT_BYTES, label="Attachment base64")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MediaError("Invalid base64 attachment") from exc
        result.append(verify_file(raw, name, declared))
    return result


def parse_message(data: dict) -> tuple[str, list[ImagePayload]]:
    """Native JSON and text/image_url OpenAI-compatible message structures."""
    if not isinstance(data, dict):
        raise MediaError("Message must be a JSON object")
    images = read_image_objects(data.get("images", [])) + read_file_objects(
        data.get("attachments", [])
    )
    content = data.get("content", "")
    if isinstance(content, list):
        if len(content) > MAX_CONTENT_PARTS:
            raise MediaError(f"Maximum {MAX_CONTENT_PARTS} content parts")
        texts = []
        extra = []
        for part in content:
            if not isinstance(part, dict):
                raise MediaError("Content part must be an object")
            if part.get("type") == "text":
                if not isinstance(part.get("text"), str):
                    raise MediaError("Text part must be text")
                texts.append(part["text"])
            elif part.get("type") == "image_url":
                url = part.get("image_url")
                if isinstance(url, dict):
                    url = url.get("url")
                if not isinstance(url, str):
                    raise MediaError("image_url must contain a data URL")
                extra.append(parse_data_url(url, f"image-{len(images) + len(extra) + 1}"))
            elif part.get("type") == "file":
                extra.extend(read_file_objects([part.get("file") or part]))
            else:
                raise MediaError("Unsupported content part")
        images += extra
        content = "\n".join(texts)
    if not isinstance(content, str) or len(content) > 100000:
        raise MediaError("content must be text <= 100000 characters")
    if not content.strip() and not images:
        raise MediaError("Message must contain text or images")
    if len(images) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise MediaError(f"Maximum {MAX_ATTACHMENTS_PER_MESSAGE} files")
    if sum(len(item.data) for item in images) > MAX_TOTAL_BYTES:
        raise MediaError("Total attachment payload too large")
    return content, images
