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
  checklists.py
  handlers.py
  orders.py
  reports.py
  reminders.py
pyproject.toml
Dockerfile
docker-compose.yml
.env.example
README.md
```

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
```

## 3. Запуск через Docker Compose

```bash
docker compose up --build
```

База данных будет храниться в `./data/shifts.db`.

## Локальный запуск без Docker

```bash
poetry install
poetry run python app/bot.py
```

## Команды бота

- `/open` — открыть смену
- `/mid` — чек-лист в течение смены
- `/close` — закрыть смену
- `/order_products` — заказ продукции
- `/order_supplies` — заказ хозтоваров
- `/stock` — ввод остатков
- `/problem` — сообщение о проблеме
- `/report YYYY-MM-DD` — отчёт за дату
