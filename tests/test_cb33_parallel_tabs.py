"""CB33 deterministic CDP interleavings: no Chrome or provider traffic."""

import asyncio
import unittest

from chatbridge.browser_transport import BrowserTransport


class FakePage:
    def __init__(self, target_id):
        self.target_id = target_id
        self.closed = False

    async def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed


class ExpectedPage:
    def __init__(self, context):
        self.context = context
        self.future = asyncio.get_running_loop().create_future()

    async def __aenter__(self):
        self.context.waiters.append(self.future)
        return self

    async def __aexit__(self, *_args):
        if self.future in self.context.waiters:
            self.context.waiters.remove(self.future)

    @property
    def value(self):
        return self.future


class FakeContext:
    def __init__(self):
        self.waiters = []
        self.pages = []
        self.closed_targets = []
        self.created_targets = []
        self.announced = asyncio.Event()
        self.publish_page = True
        self.misidentify = False
        self.create_response_delay = 0.0

    def expect_page(self, timeout):
        assert timeout > 0
        return ExpectedPage(self)

    async def new_cdp_session(self, page):
        return FakePageSession(page, self)


class FakePageSession:
    def __init__(self, page, context):
        self.page, self.context = page, context
        self.detached = False

    async def send(self, name, params=None):
        assert name == "Target.getTargetInfo"
        target = self.page.target_id
        if self.context.misidentify:
            target = "some-other-user-tab"
        return {"targetInfo": {"targetId": target}}

    async def detach(self):
        self.detached = True


class FakeBrowserSession:
    def __init__(self, browser):
        self.browser = browser
        self.detached = False

    async def send(self, name, params=None):
        ctx = self.browser.contexts[0]
        if name == "Target.createTarget":
            assert params == {"url": "about:blank", "background": True, "focus": False}
            # Yield to allow simultaneous event listeners before any page event.
            target_id = "cb33-page-" + str(len(ctx.created_targets) + 1)
            ctx.created_targets.append(target_id)
            ctx.announced.set()
            await asyncio.sleep(0)
            page = FakePage(target_id)
            ctx.pages.append(page)
            if ctx.publish_page:
                # Chromium broadcasts page to EVERY active expect_page listener.
                for waiter in list(ctx.waiters):
                    if not waiter.done():
                        waiter.set_result(page)
            if ctx.create_response_delay:
                await asyncio.sleep(ctx.create_response_delay)
            return {"targetId": target_id}
        if name == "Target.closeTarget":
            ctx.closed_targets.append(params["targetId"])
            return {"success": True}
        raise AssertionError(name)

    async def detach(self):
        self.detached = True


class FakeBrowser:
    def __init__(self):
        self.contexts = [FakeContext()]
        self.sessions = []

    async def new_browser_cdp_session(self):
        result = FakeBrowserSession(self)
        self.sessions.append(result)
        return result


class ParallelTabTests(unittest.IsolatedAsyncioTestCase):
    def new_transport(self):
        return BrowserTransport("http://127.0.0.1:39999", visible_background=True)

    async def test_parallel_creation_keeps_exact_target_identity_2_3_5_10(self):
        for n in (2, 3, 5, 10):
            with self.subTest(n=n):
                browser = FakeBrowser()
                transport = self.new_transport()
                pages = await asyncio.wait_for(asyncio.gather(
                    *(transport._new_page(browser) for _ in range(n))), timeout=5)
                assigned = [p.target_id for p in pages]
                self.assertEqual(len(set(assigned)), n, assigned)
                self.assertEqual(set(assigned), set(browser.contexts[0].created_targets))
                self.assertTrue(all(s.detached for s in browser.sessions))
                self.assertEqual(browser.contexts[0].closed_targets, [])

    async def test_wrong_chrome_target_id_fails_closed(self):
        browser = FakeBrowser()
        browser.contexts[0].misidentify = True
        with self.assertRaisesRegex(RuntimeError, "target|Target|page"):
            await self.new_transport()._new_page(browser)
        self.assertEqual(browser.contexts[0].closed_targets, ["cb33-page-1"])

    async def test_cancelled_tab_creation_cleans_created_target(self):
        browser = FakeBrowser()
        browser.contexts[0].publish_page = False
        task = asyncio.create_task(self.new_transport()._new_page(browser))
        await asyncio.wait_for(browser.contexts[0].announced.wait(), 2)
        # Let createTarget return its ID before cancellation at event.value.
        await asyncio.sleep(0.03)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        self.assertEqual(browser.contexts[0].closed_targets, ["cb33-page-1"])

    async def test_created_pages_can_navigate_in_parallel_after_allocation(self):
        browser = FakeBrowser()
        transport = self.new_transport()
        pages = await asyncio.gather(
            *(transport._new_page(browser) for _ in range(5)))
        arrived = 0
        gate = asyncio.Event()
        async def navigation(_page):
            nonlocal arrived
            arrived += 1
            if arrived == 5:
                gate.set()
            await asyncio.wait_for(gate.wait(), 1.0)
        await asyncio.gather(*(navigation(page) for page in pages))
        self.assertEqual(arrived, 5)


    async def test_cancel_during_cdp_create_target_closes_exact_target(self):
        browser = FakeBrowser()
        ctx = browser.contexts[0]
        ctx.create_response_delay = 0.05
        task = asyncio.create_task(self.new_transport()._new_page(browser))
        await asyncio.wait_for(ctx.announced.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        self.assertEqual(ctx.closed_targets, ["cb33-page-1"])
        self.assertTrue(all(s.detached for s in browser.sessions))

    async def test_page_event_timeout_closes_allocated_target(self):
        browser = FakeBrowser()
        ctx = browser.contexts[0]
        ctx.publish_page = False
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.new_transport()._new_page(browser), 0.05)
        self.assertEqual(ctx.closed_targets, ["cb33-page-1"])

    async def test_identity_mismatch_does_not_close_foreign_page(self):
        browser = FakeBrowser()
        ctx = browser.contexts[0]
        ctx.misidentify = True
        with self.assertRaisesRegex(RuntimeError, "targetId"):
            await self.new_transport()._new_page(browser)
        self.assertEqual(ctx.closed_targets, ["cb33-page-1"])
        # Only our known target may be closed, no page.close on mismatched event.
        self.assertFalse(ctx.pages[0].closed)
