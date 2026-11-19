# PLAN v1 — утверждён владельцем 2026-11-19

1. Инвентаризация источников/релизов, прочитать Spec Kit и проверить Git; бэкап двух Mac с тестом целостности. **DONE, VERIFIED**.
2. Assess: путь label-only Send → слепой Enter подтверждён кодом и синтетическим сценарием; SPEC/PLAN/ANALYZE выполнены. **DONE, VERIFIED**.
3. Fix: native icon/label Send preflight, единый click, stage diagnostics, конфигурируемые 60s receipt deadline и CDP DOM-error guard; 6 новых + P09 профильные tests прошли. **DONE, VERIFIED**.
4. Мониторинг/диагностика: безопасные cb34_stage/receipt_deadline логи; медицинский бот уже выдаёт однократное уведомление о задержке после 12 мин, 15 regression tests прошли. **DONE, VERIFIED**.
5. Полный regression, Analyze повторно, source Converge: 334/334 pytest + 4 subtests, Ruff, 15 medical tests. **DONE, VERIFIED for source**; live provider smoke remains stage 7.
6. Git commit и push на GitHub, проверить удалённый SHA. **PENDING**.
7. Установить и проверить новую версию ChatBridge на Mac Server (только `net.chatbridge.gateway.maccontrol`), затем на jkmac (только `net.chatbridge.gateway`); проверка SHA, здоровья, тестового запроса, без повторного медицинского. **PENDING**.
8. Завершить финальный Converge, обновить CHANGELOG/docs/задачи и финальный Git commit/push при необходимости. **PENDING**.

**Проверенные бэкапы / rollback:**
- Mac Server: `/Users/openclaw/AgentWorkspace/artifacts/backups/chatbridge-submit-fix-macserver-20261119-103441`; Git bundle VERIFIED, SQLite integrity_check=ok, plist/release SHA verified.
- jkmac: `/Users/jk/AgentWorkspace/artifacts/backups/chatbridge-submit-fix-jkmac-20261119-103517`; SQLite integrity_check=ok, plist/release SHA verified.
- Возврат: остановить ТОЛЬКО указанный gateway, восстановить прежний `ProgramArguments` plist или release pin, запустить и проверить /health; текущую SQLite НЕ перезаписывать старой копией без проверки новых запросов.

**Источник:** GitHub `johnnykor82/chatbridge` main synchronized to `7e57b573314350b5265edb570a79c7f8d3973ce2`; installed baseline on both `0.2.0b24-cf5b8bc2202b`.
**Область:** только ChatBridge + согласованный клиентский мониторинг; медицинские ответы, Telegram credentials, other services unchanged.
**Согласие:** пользователем 2026-11-19 утвержден весь план и запрошены GitHub и обновление обеих машин.