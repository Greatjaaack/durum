# Durum Bot

Telegram-бот для управления сменами дюрюмной: чек-листы, закрытие смены, фиксация остатков, отчёты и AI-функции.  
В проекте есть аналитический веб-дашборд `/dashboard` (FastAPI + Jinja2 + Chart.js) для KPI, аномалий и контроля потерь.

## 0. Ветки

- `dev` — рабочая ветка для разработки.
- `master` — основная ветка.

## 1. Описание проекта

Что решает бот:
- ведёт сотрудника по чек-листам открытия, ведения и закрытия смены;
- фиксирует остатки при закрытии и сохраняет их в SQLite;
- формирует отчёты по дате и интерактивные отчёты через `/reports`;
- отправляет рабочие уведомления и напоминания;
- поддерживает AI-режим (`/ai`, `/stop`) и генерацию фактов (`/fact`).

## 2. Как запустить

### Вариант A: Docker Compose

```bash
docker compose up --build
```

Если изменили `.env`, пересоздайте контейнеры, чтобы переменные точно применились:

```bash
docker compose up -d --force-recreate
```

После запуска:
- бот работает в контейнере `bot`;
- веб-дашборд доступен на `http://localhost:8000/dashboard`.

Данные и логи:
- база: `./data/shifts.db`
- логи: `./logs/app_YYYY-MM-DD.log`

### Вариант B: локально через Poetry

```bash
poetry install
poetry run python -m app.bot
```

Дашборд локально:

```bash
poetry run python -m uvicorn app.dashboard:app --host 0.0.0.0 --port 8000
```

### Настройка `.env`

```bash
cp .env.example .env
```

Минимально заполните:

```env
BOT_TOKEN=
OWNER_ID=
WORK_CHAT_ID=
OPENROUTER_API_KEY=
AI_MODEL=openrouter/free
AI_MAX_INPUT_CHARS=1000
AI_REQUEST_TIMEOUT_SEC=45
DB_PATH=data/shifts.db
LOG_DIR=logs
BOT_TIMEZONE=Europe/Moscow
SHIFT_OPEN_TIME=11:00
SHIFT_CLOSE_TIME=22:00
```

## 3. Структура проекта

```text
app/
  bot.py                 # Точка входа Telegram-бота
  config.py              # Загрузка и валидация настроек
  db.py                  # SQLite-слой и миграции
  db_schema.py           # Отдельные миграции/синхронизация схемы
  report_builder.py      # Текстовый отчёт /report YYYY-MM-DD
  reminders.py           # Планировщик напоминаний/фактов
  ai_client.py           # OpenRouter API клиент
  logging_setup.py       # Логирование в daily-файлы
  units_config.py        # Базовые единицы измерения и нормализация
  checklist/
    callbacks.py         # Формирование и парсинг callback_data чек-листов
    config.yaml          # YAML-конфиг чек-листов и остатков
    data.py              # Загрузка/валидация checklist-конфига
    ui.py                # Рендер текста/клавиатур чек-листов
  dashboard/
    __init__.py          # Экспорт FastAPI app для uvicorn app.dashboard:app
    web.py               # FastAPI роуты и миграции для dashboard
    service.py           # Агрегация KPI/аномалий/аналитики
    templates/           # Jinja2-шаблоны (base/dashboard)
    static/              # CSS и JS дашборда
  handlers/
    __init__.py          # Сборка всех роутеров
    shift.py             # /open, /mid, /close и чек-листы
    shift_checklist.py   # Callback-обработчики open/mid чек-листов
    stock.py             # /stock
    misc.py              # /start, /cancel, /problem, /report
    reports.py           # /reports (интерактивные отчёты)
    ai.py                # /fact, /ai, /stop
    states.py            # FSM-состояния
    constants.py         # Константы сценариев
    utils.py             # Вспомогательные функции
docs/
  architecture.md        # Схема логики и архитектуры
Dockerfile
docker-compose.yml
pyproject.toml
```

## 4. Используемые технологии

- Python 3.13
- aiogram 3.x
- SQLite
- APScheduler
- FastAPI + Uvicorn
- Jinja2 Templates
- PyYAML
- Chart.js
- Poetry
- Docker / Docker Compose

## 5. Основные команды бота

- `/start` — показать главное меню
- `/open` — открыть смену и пройти чек-лист открытия
- `/mid` — чек-лист ведения смены
- `/close` — чек-лист закрытия + фиксация остатков
- `/stock` — ручной ввод остатков
- `/problem` — отправка проблемы владельцу
- `/report YYYY-MM-DD` — текстовый отчёт за дату
- `/reports` — интерактивные отчёты (смены/остатки/чек-листы)
- `/fact` — последний факт о еде
- `/ai` — включить AI-режим
- `/stop` — выключить AI-режим
- `/cancel` — сброс текущего FSM-сценария

Текущее reply-меню первого уровня:
- при закрытой смене: `▶ Открыть смену`
- при открытой смене: `📝 Ведение смены`, `🔒 Закрыть смену`

## 6. Пример работы

### Смена (типовой поток)

1. Сотрудник отправляет `/open`.
2. Бот показывает чек-лист открытия с прогрессом `X / Y`.
3. После завершения смена фиксируется как `OPEN` (кто открыл, когда открыл).
4. В течение дня сотрудник может проходить `/mid`.
5. В конце дня сотрудник запускает `/close`, вводит остатки и фото.
6. Бот закрывает смену (`CLOSED`), сохраняет остатки и отправляет отчёт в рабочий чат.

### Интерактивные отчёты

1. `/reports`
2. Выбор типа отчёта:
   - отчёт по сменам
   - отчёт по остаткам
   - отчёт по чек-листам
3. Выбор даты
4. Для отчёта по сменам: выбор конкретной смены и просмотр деталей.

## Логирование

Логирование настроено в файл на каждый день:

```text
logs/app_YYYY-MM-DD.log
```

Логируются ключевые события:
- открытие смены;
- закрытие смены;
- AI-запросы;
- генерация фактов;
- ошибки.
