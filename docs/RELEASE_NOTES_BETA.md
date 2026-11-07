# ChatBridge — Beta release notes

**Language:** [English](#english) · [Русский](#русский)

## English
## v0.2.0-beta.21 — Safe ChatGPT DOM diagnostics and marker search order (2026-11-07)

- Fixed indexed search querying six unfiltered "Recent chats" suggestions as if they were marker results. ChatBridge now enters the complete compact or legacy marker BEFORE inspecting candidates, waits for the built-in search UI to settle, and fails closed if the recent list remains unfiltered.
- Search dialog activation now retries a bounded set of normally visible Search buttons when the first matching control is covered. A single keyboard shortcut remains the final fallback; no force-click or external CDP takeover.
- If recent sidebar discovery or search opening fails, record only allowlisted counts and readiness from the actual resolver-owned Playwright page. No user prompt, marker, conversation title/ID/URL, HTML, bearer token, cookie or conversation message is logged.
- Existing assistant-message evidence and unique provider conversation ID checks remain mandatory; a UI result is only a candidate. No new chat creation or messages. Initial RED regressions and 246 offline tests passed on the isolated development branch; real Mac Server login/search acceptance and independent installation are separate release gates.

### v0.2.0-beta.20 — Fix ChatGPT search dialog activation (2026-11-06)

- Real jkmac beta.19 short-marker lookup could not open the indexed-search dialog because the visible Search Chats button timed out on click. Beta.20 first accepts an existing normal search dialog, otherwise tries one standard button click followed by a single browser Meta+K shortcut if needed.
- No force-click, direct CDP, authorization bypass, provider message sends or rate-limit workaround. The full short/legacy marker, assistant role and provider conversation identity must still verify exactly. Real live acceptance remains a separate gate before any Watcher marker change.

## v0.2.0-beta.19 — Short search markers, legacy compatible (2026-11-06)

- ChatBridge accepts a compact 18-character marker (WC plus 16 lowercase hexadecimal characters) alongside legacy WATCHER-CHAT plus 32 hex. Exact message boundary, real provider conversation identity, sender role, and unique match remain mandatory.
- New parser is tested with 5,800-event-safe source mocks and live lookup is a separate acceptance gate. This release changes ONLY ChatBridge format validation, not Watcher, existing pending tasks, history, or browser behavior. Do not claim Watcher format migration until a real provider assistant-message lookup succeeds.


### v0.2.0-beta.18 — Preserve verified Watcher chat destination (2026-11-06)

- On independent jkmac beta.17, authenticated provider chat inventory returned 200 and the most recent conversation verified the exact marker in its assistant and user messages, but a later unrelated history timeout invalidated the positive result. Beta.18 checks a bounded recency group, rejects multiple verified matches within the inspected group, then stops once an exact assistant marker and real provider conversation ID have been verified.
- Unavailable history cannot erase a verified assistant destination. Missing/ambiguous evidence, provider rate limits and authorization denials remain fail-closed. No user URL, no new chat messages during discovery. Ruff and 236 offline tests PASS; independent live acceptance required.


### v0.2.0-beta.17 — Authenticated recent-chat inventory fallback (2026-11-06)

- Beta.16 live jkmac marker lookup still returned HTTP 502 and indexed-search click timeouts. This beta adds one bounded read-only same-origin recent-conversation list fetch inside ChatBridge-owned authenticated Chrome when ordinary sidebar candidates cannot verify the marker. The full marker, actual provider conversation ID and author role are required for each candidate; neither the agent nor owner provides a URL.
- Provider 429 and authorization failures stop instead of bypassing cooldowns. Candidate/history checks remain bounded, no chat message is sent during lookup, and diagnostic logs contain only counts. 235 offline tests and lint pass; independent live acceptance is still required.


### v0.2.0-beta.16 — Faster verified chat discovery (2026-11-06)

- Live beta.15 testing found the first recent-chat fallback could spend 100 seconds opening candidate conversations one by one, returning HTTP 502 instead of resolving. Beta.16 reuses ONE managed browser page and verifies recent candidates via the existing provider conversation history reader, requiring the actual provider conversation ID, full marker and verified author role. No manual URL or extra chat sends.
- Candidate history checks are bounded individually; full lookup stays below Watcher's hard timeout. Assistant evidence takes precedence when users quote an assistant marker. Missing, unavailable or ambiguous identity fails closed; indexed search remains a fallback. Source lint and 231 offline tests pass; CI and independent live acceptance required.


### v0.2.0-beta.15 — Automatic recent-chat discovery (2026-11-06)

- ChatBridge resolves a unique watcher marker without asking the user or agent for a URL. It first scans a bounded set of recent ChatGPT conversations in its managed browser and verifies the full marker, sender role and conversation identity. The existing indexed search is retained as a fallback under one bounded timeout.
- No new executor message is sent during discovery. Unverified, redirected or ambiguous candidates fail closed; provider authentication and cooldown policies remain unchanged.
- 229 offline tests pass on macOS; CI is required before release. ChatBridge beta.14 and its Chrome profile remain available for rollback.


### v0.2.0-beta.14 — Watcher notification receipt (2026-11-05)

- An exact Watcher delivery or batch notification is terminal once the browser confirms the real provider user-message ID and canonical chat, not after an unnecessary assistant model reply. No fabricated assistant turn; the global ChatBridge slot is released immediately. Ordinary ChatGPT requests still require the full assistant answer.
- Strict Watcher stable client keys and matching MC-WATCH markers gate this mode. SQLite idempotency and no blind retransmission are unchanged.
- New TDD tests cover immediate receipt, regular requests, malformed keys and batch notifications. Full local ChatBridge suite 223 PASS. Keep prior beta.13 and Chrome profile as rollback, no other service upgrades.

### v0.2.0-beta.13 — Native reasoning picker timing fix (2026-11-04)

- Fix first-message failure when the model picker submenu is not yet mounted: if the native picker explicitly reports the requested `data-selected-reasoning-effort`, reuse that verified setting without opening a menu. When different or unknown, still open the native slider and verify the requested level; allow 18 seconds for its delayed mount instead of 6.
- No change to ChatBridge message queue/idempotency, security, Chrome profile, SQLite or API semantics. Failed/unknown requests are **not** blindly retried.
- Three focused new tests passed (RED→GREEN), 218 total regression tests passed; lint passed. Real new-chat and Watcher marker→URL acceptance are checked separately after deployment, not claimed by tests alone.

**Read in Russian:** [Русский](#русский)

ChatBridge is a standalone macOS HTTP gateway for a user-authorized ChatGPT web session. It creates and tracks conversations, schedules asynchronous requests, retains history and attachments, and streams responses to applications and agents. It also exposes ChatBridge-owned background browser operations through a dedicated Browser API.

- **Works without Mac Control MCP Server.** Mac Control can optionally use ChatBridge as a separately installed browser dependency.
- **HTTP API + SSE:** asynchronous job tracking, persisted request IDs and resumable events.
- **Files:** incoming images, audio and documents; access to supported response attachments.
- **Independent browser:** managed Chrome profile and recovery, no extra MCP dependency.
- **Installer:** isolated Python environment, managed user LaunchAgent, install/upgrade/status/doctor/uninstall.
- **License:** MIT.

**Requirements:** macOS, Chrome, Git, Python 3, uv, and a valid ChatGPT login. This is unofficial tooling, not an OpenAI API implementation; web UI and account limits may change behavior.

**Beta 6:** when uv is missing, the installer offers Astral's official standalone installer instead of building Homebrew dependencies. First-run instructions explain how to open the managed Chrome profile for ChatGPT login. Keyless local access remains the default.

**uv bootstrap update:** when uv is missing, installation offers the official standalone installer with explicit consent, even if Homebrew is present. Prebuilt uv binaries go to `~/.local/bin`; shell configuration is preserved. Declining, noninteractive input and download failures provide manual installation guidance. Existing uv installations are reused.

**Beta limitations:** clean-Mac installation and login, cold-reboot acceptance, long-running tool flows and source-provenance review remain unverified. This release is experimental, not production-ready.

**Installation:** use this repository's README and `./install.sh` after reviewing source. No secrets or private browser profiles belong in GitHub.

## Русский
### v0.2.0-beta.20 — Восстановление открытия поиска чатов (2026-11-06)

- При поиске короткой метки ChatBridge beta.19 на jkmac штатная кнопка поиска ChatGPT зависала на click timeout. Теперь используется уже открытое поле либо обычный клик, при необходимости однократно Cmd+K.
- Без принудительного клика, прямого CDP, отправки сообщений или обхода ограничений. Строгая проверка роли, ID чата и полной метки сохраняется. Watcher не меняется до успешной live-проверки.

### v0.2.0-beta.19 — Короткие метки поиска (2026-11-06)

- Поддержка метки длиной 18 символов (WC и 16 строчных шестнадцатеричных символов) с сохранением прежнего формата WATCHER-CHAT плюс 32 символа. Совпадение принимается только после полной проверки роли автора, ID чата провайдера и уникальности.
- Обновлён только ChatBridge. Watcher и ожидающие задания не меняются до успешного реального поиска короткой метки, проведённого независимо на jkmac.


### v0.2.0-beta.18 — Не терять подтверждённый чат Watcher (06.11.2026)

- Живой тест beta.17 показал, что первая запись списка разговоров уже содержала точную метку в сообщении ассистента, но тайм-аут более старого чата отменял результат. Исправлен приоритет подтверждённого совпадения и ограничен перебор несвязанных разговоров.
- Проверка полной метки, фактического ID разговора и роли сообщения сохранена; 429 и ошибки авторизации не обходятся. Ruff и 236 тестов PASS. Требуется проверка на jkmac.


### v0.2.0-beta.17 — Резервный список последних чатов (06.11.2026)

- После ошибок поиска в живом тесте beta.16 ChatBridge добавляет один ограниченный запрос списка последних разговоров в собственной авторизованной сессии, если обычная боковая панель не дала подтверждения. Для каждого кандидата строго проверяются исходный идентификатор, полная метка и роль сообщения. Передавать URL вручную не требуется.
- 429 и ошибки авторизации не обходятся; отправок сообщений во время поиска нет. 235 тестов прошли, сквозной живой тест нужен отдельно.


### v0.2.0-beta.16 — Быстрый проверенный поиск чатов (06.11.2026)

- Живой тест beta.15 выявил тайм-аут при последовательном открытии найденных разговоров. Beta.16 использует одну страницу ChatBridge и читает подтверждённую историю каждого кандидата с проверкой настоящего идентификатора, полной метки и роли сообщения. URL пользователя не требуется.
- Приоритет доказательства из сообщения ассистента, даже если пользователь цитирует метку. Ограничение времени для каждого кандидата, резервный поисковый индекс, отказ без отправки сообщений при неоднозначности. 231 локальный тест прошёл; необходимы CI и независимая проверка на jkmac.


### v0.2.0-beta.15 — Автоматическое обнаружение чатов (06.11.2026)

- Пользователь и агент не предоставляют URL: ChatBridge проверяет последние чаты из боковой панели ChatGPT, полную метку, роль сообщения и идентификатор разговора. Поисковый индекс используется как резервный способ, все проверки ограничены таймаутом.
- При неоднозначности и отсутствии доказательства отправка не выполняется. Существующие ограничения доступа, дедупликация и профиль браузера сохранены.
- Локально 229 тестов PASS; релиз допускается только после успешного CI. Версия beta.14 сохраняется для отката.


### v0.2.0-beta.14 — Завершение уведомлений Watcher (05.11.2026)

- Watcher-уведомления завершаются после подтверждения настоящего пользовательского сообщения в нужном чате, без ожидания ответа модели до 30 минут. Обычные запросы работают по-старому; ответ ассистента не подделывается.
- Строгая проверка ключей/меток, дедупликация и отсутствие слепых повторов сохранены. 223 теста ChatBridge прошли.

### v0.2.0-beta.13 — Исправление выбора режима рассуждения (04.11.2026)

- При отправке первого сообщения ChatBridge сначала проверяет фактическое значение `data-selected-reasoning-effort` и не открывает меню повторно, если нужный режим уже выставлен. При переключении сохраняется проверка слайдера, время ожидания увеличено с 6 до 18 секунд.
- Очереди, идентификаторы запросов, авторизация, профиль Chrome и SQLite не меняются. Неоднозначные или неудавшиеся отправки автоматически не повторяются.
- Три новых теста прошли RED→GREEN; общий набор — 218 тестов, проверка Ruff без ошибок. Сквозной поиск метки проверяется отдельно после обновления.

**Read in English:** [English](#english)

ChatBridge — самостоятельный HTTP-сервис для приложений и ИИ-агентов, использующий авторизованную браузерную сессию ChatGPT пользователя. Он создаёт и продолжает диалоги, обрабатывает асинхронные запросы, хранит историю и файлы, возвращает события SSE и позволяет управлять фоновыми браузерными сессиями через собственный Browser API.

- **Работает без Mac Control MCP Server.** Mac Control при необходимости подключает ChatBridge как отдельно установленную браузерную зависимость.
- **HTTP API и SSE:** асинхронные запросы, постоянные идентификаторы и возобновляемые события.
- **Файлы:** изображения, аудио, документы и доступные вложения ответов.
- **Собственный браузер:** отдельный управляемый профиль Chrome и восстановление после сбоев.
- **Установщик:** изолированный Python, LaunchAgent, установка, обновление, диагностика и удаление.
- **Лицензия:** MIT.

**Требования:** macOS, Chrome, Git, Python 3, uv и авторизация ChatGPT. Это неофициальный проект, не реализация OpenAI API; изменения сайта и ограничения аккаунта могут влиять на работу.

**Beta 6:** если uv отсутствует, установщик предлагает официальный standalone-установщик Astral без сборки зависимостей Homebrew. Инструкция объясняет, как открыть управляемый Chrome-профиль и войти в ChatGPT. Локальный доступ по умолчанию работает без ключа.

**Обновление установки uv:** если uv отсутствует, предлагается официальный standalone-установщик с явным согласием, даже при наличии Homebrew. Готовые бинарные файлы uv помещаются в `~/.local/bin`, настройки shell сохраняются. При отказе, неинтерактивном запуске или ошибке загрузки выводится инструкция ручной установки. Уже установленный uv используется повторно.

**Ограничения Beta:** ещё нужны тест чистой установки и входа на другом Mac, проверка после перезагрузки, длительных сценариев и аудит публичных исходников. До их завершения нельзя представлять версию как промышленно готовую.

**Установка:** инструкция в README репозитория и `./install.sh` после проверки исходников. Секреты и пользовательские Chrome-профили не публикуются.
