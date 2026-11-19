# PLAN v1 — 2026-11-20, владелец разрешил восстановить медагента
1. Git sync latest CB34, Assess текущих ошибок, проверить ChatBridge/medical states — DONE/VERIFIED.
2. Проверенные medical+ChatBridge DB/source/launchd backup и SDD Analyze — DONE/VERIFIED. Backup: /Users/openclaw/AgentWorkspace/artifacts/backups/medical-restore-20261120-0006 (integrity_check=ok); previous ChatBridge code + plist backups /Users/openclaw/AgentWorkspace/artifacts/backups/chatbridge-submit-fix-macserver-20261119-103441, jkmac backup /Users/jk/AgentWorkspace/artifacts/backups/chatbridge-submit-fix-jkmac-20261119-103517.
3. Исходный frozen 10318 в изолированной вкладке, без Send: четыре upload done; button disabled при 0/2с и enabled при ~7с — DONE/VERIFIED.
4. Fix 25s bound polling enabled native Send, конфиг/CLI и негативные тесты; 15 focused, 338 full ChatBridge + 4 subtests, Ruff и 38 medical tests — DONE/VERIFIED.
5. Source Converge PASS, Git commit+push + fresh jkmac backup and two-machine beta26 install/health/smoke — IN PROGRESS.
6. Dry-run recovery exact original batch #10318 on copy DB; stop only medical bot, stage safe recovery with audit and return service, monitor provider+Telegram and child completion — PENDING.
7. Final Converge, docs/backlog, regression, Git commit, verify stable services and queue — PENDING.
No alteration to other services, frozen source data or uncertain prior provider requests. For any unknown outcome, do not automatically retry.

Fresh ChatBridge rollback backup: /Users/openclaw/AgentWorkspace/artifacts/backups/chatbridge-cb35-20261120-0011 (SQLite integrity_check=ok, Git bundle verified), in addition to medical DB backup above.
