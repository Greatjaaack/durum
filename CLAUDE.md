# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Project

**Docker (production):**
```bash
docker compose up --build
docker compose up -d --force-recreate  # after .env changes
```

**Local development (Poetry):**
```bash
poetry install --with dev
poetry run python -m app.bot                                                    # bot
poetry run uvicorn app.dashboard:app --host 0.0.0.0 --port 8000 --reload       # dashboard
poetry run pytest                                                                # smoke tests
```

A small smoke-test suite lives in `tests/`. It validates: YAML checklist
config loads, close-wizard items have unique residual keys, DB migrations
run cleanly on a temp SQLite, settings are parsed from `.env`. Run it
before any non-trivial change to `app/checklist/`, `app/handlers/shift.py`,
`app/db.py`, `app/db_schema.py`, or `app/config.py`.

## Environment Variables

Copy `.env.example` to `.env`. Required variables:
```
BOT_TOKEN          # Telegram bot API token
OWNER_ID           # Owner's Telegram user ID
WORK_CHAT_ID       # Work chat for reports (defaults to OWNER_ID)
DB_PATH            # SQLite file path (data/shifts.db)
BOT_TIMEZONE       # Timezone for cron (e.g. Europe/Moscow)
SHIFT_OPEN_TIME    # Expected open time (11:00)
SHIFT_CLOSE_TIME   # Expected close time (22:00)
```

Optional:
```
WORK_CHAT_THREAD_ID                # Forum topic id for reports
BOT_PROXY_URL                      # http(s)/socks proxy for Telegram API
DASHBOARD_USERNAME / _PASSWORD     # Basic auth on /dashboard
DASHBOARD_SECRET                   # Secret used by dashboard
FORCE_CLOSE_OPEN_SHIFTS_ON_START   # See "Release migration helpers" below
```

## Architecture

The bot manages a döner shop's shift lifecycle (open → mid → close) via Telegram. It is built on **aiogram 3.x** with FSM for multi-step flows, **SQLite** for storage, **APScheduler** for reminders, and a **FastAPI + Jinja2** analytics dashboard.

```
Telegram Updates
    └→ aiogram Handlers (app/handlers/)
           └→ FSM States (handlers/states.py)
                  └→ DB Layer (app/db.py, thread-safe SQLite)
                         └→ Background Scheduler (app/reminders.py)
                         └→ FastAPI Dashboard (app/dashboard/)
```

### Core Flow

Every action centers on a **Shift** record:

1. `/open` — creates shift (status=OPEN), runs open checklist
2. `/mid` — optional intra-shift checklist (not persisted)
3. `/close` — FSM wizard: close checklist → residual inputs → photo uploads → shift closed; notifies work chat

### Key Modules

| Path | Role |
|---|---|
| `app/bot.py` | Entry point; wires dispatcher, DB, scheduler. Optionally force-closes OPEN shifts on startup. |
| `app/config.py` | Dataclass settings loaded from env (`load_settings`). |
| `app/db.py` | All DB access; `threading.Lock()` for safety. |
| `app/db_schema.py` | Schema creation and migrations (incl. `force_close_all_open_shifts`). |
| `app/handlers/shift.py` | Core business logic for open/mid/close (~3600 lines — see "Refactor roadmap"). |
| `app/handlers/shift_checklist.py` | Callback handlers for open/mid checklist toggles. |
| `app/handlers/states.py` | FSM state classes (`CloseShiftStates`, `StockStates`, etc.). |
| `app/handlers/utils.py` | Shared helpers: notification formatting, keyboard builders. |
| `app/handlers/media.py` | File-system helpers for downloading Telegram media. |
| `app/checklist/config.yaml` | YAML-driven checklist definitions, residual inputs, item options. |
| `app/checklist/data.py` | Loads and validates `config.yaml`. |
| `app/checklist/ui.py` | Renders checklist text and inline keyboards. |
| `app/reminders.py` | APScheduler jobs (opening/closing deadline checks, reminders). |
| `app/report_builder.py` | Formats shift summary text for Telegram messages. |
| `app/dashboard/web.py` | FastAPI routes; also runs DB migrations on startup. |
| `app/dashboard/service.py` | KPI aggregation and anomaly detection logic. |
| `scripts/shift_open_index_plus_one.py` | One-off migration: shift saved checklist indexes by +1. |

### Database Tables

`shifts`, `checklist_state`, `close_residuals`, `close_checklist_media`,
`open_checklist_media`, `stock`, `shift_periodic_residuals`,
`mid_checklist_data`, `employee_profiles`, `employee_schedule_entries`,
`orders` (см. TODO рядом с `Database.save_order`).

**Удалённые фичи (миграции выполняются автоматически при старте):**
`camera_devices`/`camera_videos` — синхронизатор Xiaomi-камер. Таблицы
дропаются миграцией `drop_camera_tables` в `db_schema.py`.

### Checklist System

Checklists (open/mid/close sections and items) are fully defined in
`app/checklist/config.yaml` — no code changes needed to modify them. Each
item is **either a string or a mapping** with options:

```yaml
items:
  - "Plain item"
  - text: "Item with photo requirement"
    requires_photo: true
```

The close checklist also drives which residual inputs are collected
(`close_residual_inputs` block). Each residual is bound to a specific
section via `section_title` to avoid duplication when the same item text
appears in multiple sections (e.g. expiry-check vs put-away).

### Photo-required items

A close-wizard item with `requires_photo: true` cannot be checked off
until the user uploads a photo. The same flag works in the open
checklist (handled by `app/handlers/shift_checklist.py`). Legacy
hard-coded heuristics for "корзина"/"масло" remain as a fallback only;
prefer the YAML flag for new items.

### Release migration helpers

When a release changes the order or count of checklist items (which
shifts saved indexes in `checklist_state.completed` and
`open_checklist_media.item_index`), there are two safety nets:

1. **`scripts/shift_open_index_plus_one.py`** — one-off Python migration
   with `--commit`/dry-run modes; safe to inspect before applying.
2. **`FORCE_CLOSE_OPEN_SHIFTS_ON_START=true`** — at next bot startup,
   every shift with `status='OPEN'` is closed. Intended for off-hours
   redeploys; turn off again after the deploy completes.

### Dashboard

Accessible at `http://localhost:8000/dashboard`. Shows KPI trends,
anomaly detection (residuals outside 0.5×–1.5× historical average), and
per-shift drill-down with photo previews.

### Refactor roadmap

`app/handlers/shift.py` is being incrementally split into focused
modules. New helpers should land in dedicated files (e.g.
`app/handlers/media.py`) rather than growing `shift.py`. Aim:

- `close_wizard.py` — wizard FSM, item assembly, photo gating.
- `open_flow.py` — `/open` handler + open-checklist photo step.
- `mid_flow.py` — `/mid` handler + numeric inputs.
- `residuals.py` — close + periodic residual storage and parsing.

Each move must be accompanied by `poetry run pytest` passing.
