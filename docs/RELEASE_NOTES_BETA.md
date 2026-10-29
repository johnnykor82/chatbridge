# ChatBridge — Beta release notes

**Language:** [English](#english) · [Русский](#русский)

## English

**Read in Russian:** [Русский](#русский)

ChatBridge is a standalone macOS HTTP gateway for a user-authorized ChatGPT web session. It creates and tracks conversations, schedules asynchronous requests, retains history and attachments, and streams responses to applications and agents. It also exposes ChatBridge-owned background browser operations through a dedicated Browser API.

- **Works without Mac Control MCP Server.** Mac Control can optionally use ChatBridge as a separately installed browser dependency.
- **HTTP API + SSE:** asynchronous job tracking, persisted request IDs and resumable events.
- **Files:** incoming images, audio and documents; access to supported response attachments.
- **Independent browser:** managed Chrome profile and recovery, no extra MCP dependency.
- **Installer:** isolated Python environment, managed user LaunchAgent, install/upgrade/status/doctor/uninstall.
- **License:** MIT.

**Requirements:** macOS, Chrome, Git, Python 3, uv, and a valid ChatGPT login. This is unofficial tooling, not an OpenAI API implementation; web UI and account limits may change behavior.

**Beta 3:** the installer copies an explicit runtime file list and browser-origin protections preserve keyless access for local clients. If `uv` is missing, first-run installation offers to install it with explicit approval.

**Beta limitations:** clean-Mac installation and login, cold-reboot acceptance, long-running tool flows and source-provenance review remain unverified. This release is experimental, not production-ready.

**Installation:** use this repository's README and `./install.sh` after reviewing source. No secrets or private browser profiles belong in GitHub.

## Русский

**Read in English:** [English](#english)

ChatBridge — самостоятельный HTTP-сервис для приложений и ИИ-агентов, использующий авторизованную браузерную сессию ChatGPT пользователя. Он создаёт и продолжает диалоги, обрабатывает асинхронные запросы, хранит историю и файлы, возвращает события SSE и позволяет управлять фоновыми браузерными сессиями через собственный Browser API.

- **Работает без Mac Control MCP Server.** Mac Control при необходимости подключает ChatBridge как отдельно установленную браузерную зависимость.
- **HTTP API и SSE:** асинхронные запросы, постоянные идентификаторы и возобновляемые события.
- **Файлы:** изображения, аудио, документы и доступные вложения ответов.
- **Собственный браузер:** отдельный управляемый профиль Chrome и восстановление после сбоев.
- **Установщик:** изолированный Python, LaunchAgent, установка, обновление, диагностика и удаление.
- **Лицензия:** MIT.

**Требования:** macOS, Chrome, Git, Python 3, uv и авторизация ChatGPT. Это неофициальный проект, не реализация OpenAI API; изменения сайта и ограничения аккаунта могут влиять на работу.

**Beta 3:** установщик копирует только перечисленные рабочие файлы, а защита браузерных запросов сохраняет локальный доступ без ключа. Если `uv` отсутствует, установщик предлагает поставить его с подтверждением пользователя.

**Ограничения Beta:** ещё нужны тест чистой установки и входа на другом Mac, проверка после перезагрузки, длительных сценариев и аудит публичных исходников. До их завершения нельзя представлять версию как промышленно готовую.

**Установка:** инструкция в README репозитория и `./install.sh` после проверки исходников. Секреты и пользовательские Chrome-профили не публикуются.
