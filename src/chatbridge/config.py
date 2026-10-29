"""Configuration (no session credentials persisted here)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8787
    database: Path = Path.home() / ".chatbridge" / "state.db"
    cdp_url: str = "http://127.0.0.1:9437"
    max_active_sessions: int = 10
    hard_timeout_seconds: float = 1800.0
    first_content_timeout_seconds: float = 300.0
    stream_idle_timeout_seconds: float = 240.0
    verify_interval_seconds: float = 2.0
    api_key: str = ""
    transport: str = "browser"
    reasoning_effort: str = "high"
    browser_mode: str = "managed"
    chrome_headless: bool = False
    chrome_profile: Path = Path.home() / ".chatbridge" / "chrome-profile"
    chrome_binary: Path | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            host=os.getenv("CHATBRIDGE_HOST", "127.0.0.1"),
            port=int(os.getenv("CHATBRIDGE_PORT", "8787")),
            database=Path(
                os.getenv("CHATBRIDGE_DB", str(Path.home() / ".chatbridge" / "state.db"))
            ).expanduser(),
            cdp_url=os.getenv("CHATBRIDGE_CDP", "http://127.0.0.1:9437"),
            max_active_sessions=int(os.getenv("CHATBRIDGE_MAX_ACTIVE", "10")),
            hard_timeout_seconds=float(os.getenv("CHATBRIDGE_HARD_TIMEOUT", "1800")),
            first_content_timeout_seconds=float(os.getenv("CHATBRIDGE_FIRST_TIMEOUT", "300")),
            stream_idle_timeout_seconds=float(os.getenv("CHATBRIDGE_IDLE_TIMEOUT", "240")),
            verify_interval_seconds=float(os.getenv("CHATBRIDGE_VERIFY_INTERVAL", "2")),
            api_key=os.getenv("CHATBRIDGE_API_KEY", ""),
            transport=os.getenv("CHATBRIDGE_TRANSPORT", "browser"),
            reasoning_effort=os.getenv("CHATBRIDGE_REASONING_EFFORT", "high").lower(),
            # Existing installations supplying CDP explicitly are left external.
            # A clean install with no CDP setting owns its own isolated Chrome.
            browser_mode=os.getenv(
                "CHATBRIDGE_BROWSER_MODE",
                "external" if "CHATBRIDGE_CDP" in os.environ else "managed",
            ).lower(),
            chrome_headless=os.getenv("CHATBRIDGE_CHROME_HEADLESS", "0").strip().lower()
            not in ("0", "false", "no"),
            chrome_profile=Path(
                os.getenv(
                    "CHATBRIDGE_CHROME_PROFILE",
                    str(Path.home() / ".chatbridge" / "chrome-profile"),
                )
            ).expanduser(),
            chrome_binary=(
                Path(os.environ["CHATBRIDGE_CHROME_PATH"]).expanduser()
                if os.getenv("CHATBRIDGE_CHROME_PATH")
                else None
            ),
        )

    def validate(self) -> None:
        if self.browser_mode not in ("external", "managed"):
            raise ValueError("CHATBRIDGE_BROWSER_MODE must be managed or external")
        if not (1 <= self.max_active_sessions <= 100):
            raise ValueError("max_active_sessions must be 1..100")
        if self.port < 1 or self.port > 65535:
            raise ValueError("Invalid port")
        if self.reasoning_effort not in ("low", "medium", "high"):
            raise ValueError("reasoning_effort must be low, medium, or high")
        if self.host != "127.0.0.1" and not self.api_key:
            raise ValueError("Non-loopback binding requires CHATBRIDGE_API_KEY")
