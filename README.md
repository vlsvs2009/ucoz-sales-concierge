# AI Sales Concierge для uCoz

Виджет-консультант, который ставится на uCoz-сайт одной строкой, отвечает только по контенту сайта, уточняет потребность посетителя и собирает лид с резюме диалога в кабинет владельца.

## Запуск локально

```bash
pip install -r requirements.txt
cp .env.example .env   # вписать ANTHROPIC_API_KEY и ADMIN_TOKEN
export $(grep -v '^#' .env | xargs)
uvicorn main:app --reload --port 8000
```

Открыть `http://localhost:8000/admin?token=<ADMIN_TOKEN>` → вставить URL сайта → «Проиндексировать» → скопировать сниппет.

Без ключа (проверка пайплайна): `LLM_MOCK=1 uvicorn main:app`.

## Деплой на Railway

Dockerfile в корне, `railway.json` с healthcheck. Переменные: `ANTHROPIC_API_KEY`, `ADMIN_TOKEN`, опционально `MODEL` (по умолчанию `claude-sonnet-4-5`), `DATA_DIR=/data` + volume на `/data` для SQLite.

## Установка виджета на uCoz

```html
<script src="https://<host>/widget.js" data-site="<SITE_ID>" defer></script>
```

Панель uCoz → «Управление дизайном» → «Глобальные блоки» (или шаблон «Общее для всех страниц», перед `</body>`) → вставить → сохранить. Через uCoz MCP: попросить агента «добавь в глобальный блок перед `</body>` скрипт …».

## Структура

`main.py` — API и страницы · `crawler.py` — обход сайта и чистка текста · `retrieval.py` — BM25 · `agent.py` — Claude + tool `capture_lead` · `db.py` — SQLite · `widget.js`, `admin.html`, `site.html`, `demo.html`, `login.html` — виджет, кабинет, демо-страница · `PRD.md`, `DESIGN.md`, `DEMO_PITCH.md` — документы.

## API

`POST /api/chat` `{site_id, message, conversation_id?}` → `{conversation_id, reply, sources[], lead?}` · `GET /api/widget-config/{site_id}` · админ (заголовок `X-Admin-Token`): `GET/POST /api/sites`, `GET /api/sites/{id}`, `POST /api/sites/{id}/reindex`, `PUT /api/sites/{id}/config`, `GET /api/sites/{id}/leads|conversations|search?q=`.
