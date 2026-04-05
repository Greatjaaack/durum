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
poetry install
poetry run python -m app.bot                                                    # bot
poetry run uvicorn app.dashboard:app --host 0.0.0.0 --port 8000 --reload       # dashboard
```

There are no automated tests in this project.

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
| `app/bot.py` | Entry point; wires dispatcher, DB, scheduler |
| `app/config.py` | Pydantic settings from env |
| `app/db.py` | All DB access; `threading.Lock()` for safety |
| `app/db_schema.py` | Schema creation and migrations |
| `app/handlers/shift.py` | Core business logic for open/mid/close (~2900 lines) |
| `app/handlers/shift_checklist.py` | Callback handlers for checklist interactions |
| `app/handlers/states.py` | FSM state classes (`CloseShiftStates`, `StockStates`, etc.) |
| `app/handlers/utils.py` | Shared helpers: notification formatting, keyboard builders |
| `app/checklist/config.yaml` | YAML-driven checklist definitions and residual inputs |
| `app/checklist/data.py` | Loads and validates `config.yaml` |
| `app/checklist/ui.py` | Renders checklist text and inline keyboards |
| `app/reminders.py` | APScheduler jobs (opening/closing deadline checks, reminders) |
| `app/report_builder.py` | Formats shift summary text for Telegram messages |
| `app/dashboard/web.py` | FastAPI routes; also runs DB migrations on startup |
| `app/dashboard/service.py` | KPI aggregation and anomaly detection logic |

### Database Tables

`shifts`, `checklist_state`, `close_residuals`, `close_checklist_media`, `stock`

### Checklist System

Checklists (open/mid/close sections and items) are fully defined in `app/checklist/config.yaml` — no code changes needed to modify them. The close checklist also drives which residual inputs are collected and their unit types.

### Dashboard

Accessible at `http://localhost:8000/dashboard`. Shows KPI trends, anomaly detection (residuals outside 0.5×–1.5× historical average), and per-shift drill-down with photo previews.
