# Durum Shift Bot

Telegram-бот для управления сменой дюрюмной: чек-листы, закрытие смены, заказы, остатки, журнал проблем, отчёты и напоминания.

## Стек

- Python 3.13
- aiogram 3.x
- SQLite
- APScheduler
- Poetry
- Docker / Docker Compose

## Структура

```text
app/
  bot.py
  config.py
  db.py
  ai_client.py
  checklist_data.py
  checklists.py
  handlers/
    __init__.py
    ai.py
    constants.py
    misc.py
    orders.py
    shift.py
    states.py
    stock.py
    utils.py
  logging_setup.py
  orders.py
  reports.py
  reminders.py
pyproject.toml
Dockerfile
docker-compose.yml
.env.example
README.md
```

## Архитектура обработчиков

- `app/handlers/shift.py` — сценарии смены и чек-листы (`/open`, `/mid`, `/close`).
- `app/handlers/orders.py` — интерфейс и отправка заказов (`/order_products`, `/order_supplies`).
- `app/handlers/stock.py` — ввод остатков (`/stock`) и уведомления о низком уровне.
- `app/handlers/misc.py` — старт, отмена, проблемы и отчёты (`/start`, `/cancel`, `/problem`, `/report`).
- `app/handlers/ai.py` — AI-команды и AI-диалог (`/fact`, `/ai`, `/stop`).
- `app/handlers/__init__.py` — сборка общего `Router` из всех подроутеров.

## 1. Установка Poetry

Официальный способ:

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

Проверьте:

```bash
poetry --version
```

## 2. Создание `.env`

Скопируйте пример:

```bash
cp .env.example .env
```

Заполните значения:

```env
BOT_TOKEN=ваш_токен_бота
OWNER_ID=telegram_id_владельца
WORK_CHAT_ID=telegram_id_рабочего_чата
OPENROUTER_API_KEY=ваш_openrouter_api_key
AI_MODEL=openrouter/free
AI_MAX_INPUT_CHARS=1000
AI_REQUEST_TIMEOUT_SEC=45
DB_PATH=shifts.db
LOG_DIR=logs
BOT_TIMEZONE=Europe/Moscow
SHIFT_OPEN_TIME=11:00
SHIFT_CLOSE_TIME=22:00
```

## 3. Запуск через Docker Compose

```bash
docker compose up --build
```

База данных будет храниться в `./data/shifts.db`.
Логи приложения будут сохраняться в `./logs/app_YYYY-MM-DD.log`.

Остановка:

```bash
docker compose down
```

## Локальный запуск без Docker

```bash
poetry install
poetry run python -m app.bot
```

## Логи и данные

- Файлы логов создаются автоматически в `LOG_DIR` (по умолчанию `./logs`).
- SQLite-файлы базы (`*.db`) не должны храниться в репозитории и добавлены в `.gitignore`.

## Команды бота

- `/open` — открыть смену
- `/mid` — чек-лист в течение смены
- `/close` — закрыть смену
- `/order_products` — заказ продукции
- `/order_supplies` — заказ хозтоваров
- `/stock` — ввод остатков
- `/problem` — сообщение о проблеме
- `/report YYYY-MM-DD` — отчёт за дату
- `/fact` — последний факт о еде
- `/ai` — включить AI режим
- `/stop` — выключить AI режим
