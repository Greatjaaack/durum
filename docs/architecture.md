# Архитектура Durum Bot

## 1) Общая схема системы

```text
Telegram User
    │
    ▼
Telegram Bot (aiogram handlers)
    │
    ├─► SQLite (shifts, checklist_state, close_residuals, orders, stock, ...)
    │
    ├─► APScheduler (напоминания, проверка закрытия)
    │
    └─► FastAPI Dashboard (/dashboard)
```

## 2) Бизнес-поток смены

```text
/open
  ▼
Создание смены (status=OPEN, opened_at, opened_by)
  ▼
Чек-лист открытия
  ▼
(/mid в течение дня)
  ▼
/close
  ▼
Чек-лист закрытия + обязательные остатки
  ▼
Ввод выручки + фото
  ▼
Закрытие смены (status=CLOSED, closed_at)
  ▼
Отчёт о закрытии в рабочий чат
```

## 3) Логика чек-листов

```text
Выбор типа чек-листа (open/mid/close)
  ▼
Загрузка состояния из БД (completed, active_section)
  ▼
Рендер:
  - текст с секциями и чекбоксами
  - inline-кнопки для переключения секций/пунктов
  ▼
Нажатие на пункт
  ▼
toggle ⬜/☑ + сохранение состояния в БД
  ▼
Обновление того же сообщения (editMessageText)
```

## 4) Отчёты

### Команда `/report YYYY-MM-DD`

```text
Дата -> сбор агрегированных данных (shifts + stock + orders)
     -> текстовый отчёт одним сообщением
```

### Команда `/reports`

```text
/reports
  ▼
Выбор типа (смены / остатки / чек-листы)
  ▼
Выбор даты
  ▼
Просмотр:
  - список смен (для типа "смены")
  - агрегаты остатков
  - прогресс чек-листов по сменам
```

## 5) Планировщик

```text
Периодические задачи:
  - напоминание про проверку всех хозов и чистоту
  - напоминание о заказе продукции
  - контроль незакрытой смены
```

## 6) Дашборд `/dashboard`

```text
HTTP GET /dashboard?date=YYYY-MM-DD
  ▼
Чтение из SQLite:
  - shifts
  - close_residuals
  - checklist_state
  ▼
HTML-страница:
  - фильтр по дате
  - таблица смен
  - таблица остатков
  - таблица выполнения чек-листов
```

## 7) Основные модули

```text
app/handlers/*   -> транспортный слой (команды, callback, FSM)
app/db.py        -> data access + миграции
app/checklist/*  -> YAML-конфиг + UI-рендер чек-листов
app/report_builder.py -> текстовый отчёт по дате
app/reminders.py -> фоновые задачи
app/dashboard/*  -> веб-представление отчётов/смен
```
