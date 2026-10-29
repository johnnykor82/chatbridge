"""Capture all downloadable files in one completed ChatGPT browser turn."""

from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import unquote

from .media import ImagePayload, verify_file

log = logging.getLogger(__name__)
SANDBOX_FILE = re.compile(r"sandbox:/mnt/data/([A-Za-z0-9_.%+-]+)")


async def download_visible_artifacts(
    page, message_id: str | None, content: str = ""
) -> AsyncIterator[tuple[ImagePayload | None, dict | None]]:
    """Return every resource-card file as bytes, never executing downloaded code.

    The Card UI is scoped to the final assistant message. Missing files are
    explicit errors rather than being silently omitted. Each occurrence of a
    file card is retained, even if another card has identical bytes.
    """
    if not message_id:
        return
    root = page.locator(f'[data-chatgpt-selection-message-id="{message_id}"]').first
    expected = [unquote(path) for path in SANDBOX_FILE.findall(content)]
    if expected:
        try:
            await root.wait_for(state="visible", timeout=9000)
            await root.locator("span[title]").first.wait_for(state="attached", timeout=9000)
        except Exception:
            log.warning("File card not rendered before capture timeout")

    titles = root.locator("span[title]")
    found: list[str] = []
    count = await titles.count()
    for index in range(count):
        title = titles.nth(index)
        filename = await title.get_attribute("title") or ""
        if not filename or "/" in filename or "\\" in filename:
            continue
        row = title.locator("xpath=../../..")
        buttons = row.get_by_role("button", name=re.compile(r"Скачать файл|Download file", re.I))
        if not await buttons.count():
            continue
        found.append(filename)
        try:
            async with page.expect_download(timeout=30000) as pending:
                await buttons.first.dispatch_event("click")
            download = await pending.value
            failure = await download.failure()
            if failure:
                raise RuntimeError("Browser download reported failure")
            filepath = await download.path()
            if not filepath:
                raise RuntimeError("Browser download has no local file")
            data = Path(filepath).read_bytes()
            filename = download.suggested_filename or filename
            declared = mimetypes.guess_type(filename)[0] or ""
            yield verify_file(data, filename, declared), None
        except Exception as exc:
            log.warning("Unable to retrieve one ChatGPT file: %s", type(exc).__name__)
            yield None, {"filename": filename, "reason": type(exc).__name__}

    for filename in expected:
        if filename not in found:
            yield None, {"filename": filename, "reason": "file_card_not_found"}
