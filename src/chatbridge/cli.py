"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace

from aiohttp import web

from .browser_supervisor import ChromeSupervisor
from .browser_transport import BrowserTransport
from .config import Settings
from .engine import GatewayEngine
from .mock_transport import MockTransport
from .server import make_app
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="ChatBridge ChatGPT multi-session HTTP gateway")
    parser.add_argument("--mock", action="store_true", help="Offline test transport only")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    settings = Settings.from_env()
    from pathlib import Path

    if args.host:
        settings = replace(settings, host=args.host)
    if args.port:
        settings = replace(settings, port=args.port)
    if args.db:
        settings = replace(settings, database=Path(args.db))
    if args.mock:
        settings = replace(settings, transport="mock")
    settings.validate()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store = Store(settings.database)
    transport = (
        MockTransport()
        if settings.transport == "mock"
        else BrowserTransport(
            settings.cdp_url,
            settings.hard_timeout_seconds,
            settings.verify_interval_seconds,
            reasoning_effort=settings.reasoning_effort,
            visible_background=settings.browser_mode == "managed" and not settings.chrome_headless,
        )
    )
    supervisor = None
    if settings.transport == "browser" and settings.browser_mode == "managed":
        supervisor = ChromeSupervisor(
            profile=settings.chrome_profile,
            chrome=settings.chrome_binary,
            on_ready=lambda url: setattr(transport, "cdp_url", url),
            headless=settings.chrome_headless,
        )
    if supervisor:
        transport.browser_supervisor = supervisor
    engine = GatewayEngine(
        store,
        transport,
        settings.max_active_sessions,
        default_reasoning_effort=settings.reasoning_effort,
    )
    from .background_browser import BackgroundBrowsers

    background_browsers = BackgroundBrowsers(settings.database.parent / "background-browsers")
    app = make_app(
        store,
        engine,
        settings,
        browser_supervisor=supervisor,
        background_browsers=background_browsers,
    )

    async def startup(app):
        if supervisor:
            await supervisor.start()
        await engine.start()

    async def cleanup(app):
        await engine.stop()
        await background_browsers.shutdown()
        if supervisor:
            await supervisor.stop()
        store.close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    web.run_app(app, host=settings.host, port=settings.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
