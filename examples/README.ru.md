# CB-022: ChatBridge HTTP examples

Независимые клиенты без Chromium, Mac Control или официального OpenAI API:

- Python (стандартная библиотека): python/chatbridge_client.py
- Node.js 18+ (native fetch): javascript/chatbridge_client.mjs
- OpenAPI preview: GET http://127.0.0.1:8788/v1/openapi.json

## Python

    from chatbridge_client import ChatBridgeClient
    client = ChatBridgeClient()
    session = client.create_session(mode="temporary", title="Standalone")
    sent = client.send(session["session_id"], "Короткий отчёт",
                       client_request_id=client.request_key())
    outcome = client.wait(sent["request_id"])
    print(outcome["status"], outcome.get("answer", ""))

## Node.js

    import {randomUUID} from "node:crypto";
    import {ChatBridgeClient} from "./chatbridge_client.mjs";
    const api = new ChatBridgeClient({apiKey: process.env.CHATBRIDGE_API_KEY || ""});
    const chat = await api.createSession({mode: "temporary"});
    const submitted = await api.send(chat.session_id, "Short summary",
      {clientRequestId: "example-" + randomUUID()});
    const result = await api.wait(submitted.request_id);
    console.log(result.status);

## Правила работы

Используйте temporary только для независимых задач без проекта; persistent/continued — для долговременных диалогов, проект передавайте явно. Для POST отправки требуется постоянный client_request_id. При сетевом обрыве результат может быть unknown: нельзя автоматически повторять его с новым ключом. Проверяйте существующий request_id и локальную историю, при необходимости — POST /v1/requests/{id}/reconcile (не повторяет генерацию).

Статус streaming не означает completed. Ожидание по тайм-ауту возвращает фактический статус, а не фиктивный успех. Неподдержанные операции Projects API возвращают HTTP 501; в примерах их нет.

Это минимальный JSON SDK: multipart, SSE, полный набор MIME и типизированные ошибки каждого маршрута остаются незавершёнными частями CB-022.

## Проверка

Для Python: python3 -m unittest discover -s examples/python -p test_chatbridge_client.py -v.

Тестовый файл создан. Пока отсутствует подтверждённый прогон unittest в Mac, а штатная проверка pytest столкнулась с отсутствием пакета. Это необходимо закрыть перед DONE.
