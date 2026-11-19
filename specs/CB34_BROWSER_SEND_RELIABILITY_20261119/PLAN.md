# PLAN v1 — утверждён владельцем 2026-11-19

1. Инвентаризация источников/релизов, прочитать Spec Kit и проверить Git; бэкап двух Mac с тестом целостности. **DONE, VERIFIED**.
2. Assess: путь label-only Send → слепой Enter подтверждён кодом и синтетическим сценарием; SPEC/PLAN/ANALYZE выполнены. **DONE, VERIFIED**.
3. Fix: native icon/label Send preflight, единый click, stage diagnostics, конфигурируемые 60s receipt deadline и CDP DOM-error guard; 6 новых + P09 профильные tests прошли. **DONE, VERIFIED**.
4. Мониторинг/диагностика: безопасные cb34_stage/receipt_deadline логи; медицинский бот уже выдаёт однократное уведомление о задержке после 12 мин, 15 regression tests прошли. **DONE, VERIFIED**.
5. Полный regression, Analyze повторно, source Converge: 334/334 pytest + 4 subtests, Ruff, 15 medical tests. **DONE, VERIFIED for source**; live provider smoke remains stage 7.
6. Git commit и push на GitHub, проверить удалённый SHA. **DONE VERIFIED**: 1dbd2883861fdfba0fb4513e18082c126407e93f совпадает с origin/main.
7. Установить beta25 на Mac Server и jkmac. **DONE VERIFIED**: одинаковый релиз/исходный SHA, /health и /ready HTTP 200 с подтверждённой авторизацией, изолированный nonmedical POST + user/assistant ACK на каждой машине.
8. Финальный Converge, документация и GitHub commit. **DONE VERIFIED** после входа пользователя в jkmac и успешного сквозного теста на второй машине.

**Проверенные бэкапы / rollback:**
- Mac Server: `/Users/openclaw/AgentWorkspace/artifacts/backups/chatbridge-submit-fix-macserver-20261119-103441`; Git bundle VERIFIED, SQLite integrity_check=ok, plist/release SHA verified.
- jkmac: `/Users/jk/AgentWorkspace/artifacts/backups/chatbridge-submit-fix-jkmac-20261119-103517`; SQLite integrity_check=ok, plist/release SHA verified.
- Возврат: остановить ТОЛЬКО указанный gateway, восстановить прежний `ProgramArguments` plist или release pin, запустить и проверить /health; текущую SQLite НЕ перезаписывать старой копией без проверки новых запросов.

**Источник:** GitHub `johnnykor82/chatbridge` main synchronized to `7e57b573314350b5265edb570a79c7f8d3973ce2`; installed baseline on both `0.2.0b24-cf5b8bc2202b`.
**Область:** только ChatBridge + согласованный клиентский мониторинг; медицинские ответы, Telegram credentials, other services unchanged.
**Согласие:** пользователем 2026-11-19 утвержден весь план и запрошены GitHub и обновление обеих машин.
## Production verification, 2026-11-19
- Published GitHub `johnnykor82/chatbridge` SHA `1dbd2883861fdfba0fb4513e18082c126407e93f`, independently verified origin/main.
- Mac Server `0.2.0b25-d8277f597c56`, launchd `net.chatbridge.gateway.maccontrol` PID 14092, port59496 /health=ok, /ready authenticated=200, browser_transport.py SHA `e1510e9ff9350dee504f09f74337be802f88830c8abecd3db1f5067fcd329a26`. One isolated nonmedical standard request `5b7f533d-c31d-4757-8f10-2981b9eaa509` verified submitting→streaming→completed, provider user and assistant IDs ACK, exact `CB34-OK-20261119` returned. No medical prompt replay.
- jkmac `0.2.0b25-d8277f597c56`, launchd `net.chatbridge.gateway`, порт 8787, /health=ok, SQLite integrity=ok, SHA исходников совпадает с Mac Server. После ручной авторизации в правильном Chrome-профиле подтверждены аккаунт и редактор ChatGPT. Один изолированный немедицинский запрос `87927a94-41ce-4693-9ccc-a9dd34a75b92` перешёл submitting→thinking→completed, оба идентификатора user/assistant подтверждены, ответ точно `CB34-JKMAC-OK-20261119`; повторный /ready вернул HTTP 200 и `chatgpt_authenticated=true`. Предыдущая ошибка 503 после входа больше не воспроизводится.
- Immutable original releases preserved; tested backups Mac Server 10:34, jkmac 10:35 as recorded above; no other gateway or bot restarted.
