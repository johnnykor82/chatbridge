# CB35 Test — 2026-11-20
Live no-send reproduction with original frozen #10318: four ready attachments, Send disabled at 0/2 seconds, enabled by 7 seconds; zero model requests. New test_cb35_delayed_send.py 4 cases and existing 11 native/receipt tests pass. Full suite, postrelease nonmedical smoke and actual medical/Telegram delivery pending.

Pre-release full regression: 338/338 ChatBridge tests, 4 subtests, Ruff all src/tests/scripts, compileall and 38 medical batch/catch-up/incident/delay tests passed. No provider or Telegram live replay before release.
