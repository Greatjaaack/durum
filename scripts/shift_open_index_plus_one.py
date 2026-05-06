#!/usr/bin/env python3
"""Одноразовая миграция: сдвиг индексов open-чек-листа на +1.

Контекст. В YAML открытия добавлена новая первая секция «Униформа и
внешний вид» с одним пунктом. Все индексы пунктов сдвинулись на +1.
Если в БД на момент выкатки есть открытая (status='OPEN') смена, её
сохранённое состояние чек-листа (`checklist_state.completed` и
`open_checklist_media.item_index`) будет указывать на старую нумерацию —
этот скрипт приводит индексы к новой.

Особенности.
* По умолчанию — dry-run (только показывает изменения).
* С флагом --commit делает бэкап БД и применяет изменения в одной
  транзакции.
* Идемпотентность защищена тем, что скрипт ОДНОРАЗОВЫЙ — повторный
  запуск с --commit сдвинет индексы ещё раз. Перед --commit убедитесь,
  что миграция ранее не выполнялась.

Запуск (на проде, рядом с data/shifts.db):
    python scripts/shift_open_index_plus_one.py            # dry-run
    python scripts/shift_open_index_plus_one.py --commit   # применить
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


SHIFT_INDEX_OFFSET = 1  # на сколько сдвигаем индексы пунктов open-чек-листа


def _load_completed(raw: object) -> list[int]:
    """Парсит JSON-строку completed в список индексов."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [int(x) for x in data if isinstance(x, (int, float))]


def _shift_indexes(indexes: list[int], offset: int) -> list[int]:
    """Сдвигает индексы на offset."""
    return sorted({idx + offset for idx in indexes})


def _backup_db(db_path: Path) -> Path:
    """Делает копию БД с timestamp в имени."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_suffix(db_path.suffix + f".bak_open_index_{stamp}")
    shutil.copy2(db_path, backup)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", "data/shifts.db"),
        help="Путь к SQLite-файлу (по умолчанию data/shifts.db или $DB_PATH).",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Применить изменения. Без флага — только dry-run.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=SHIFT_INDEX_OFFSET,
        help=f"Смещение индексов (по умолчанию {SHIFT_INDEX_OFFSET}).",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"ERROR: БД не найдена: {db_path}")
        return 1

    print(f"DB: {db_path}")
    print(f"Mode: {'COMMIT' if args.commit else 'DRY-RUN'}")
    print(f"Offset: +{args.offset}")
    print()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # 1. Открытые смены
        open_shifts = conn.execute(
            "SELECT id, date, opened_at, employee FROM shifts WHERE status = 'OPEN' ORDER BY id"
        ).fetchall()
        if not open_shifts:
            print("Открытых смен нет — миграция не нужна.")
            return 0

        print(f"Найдено открытых смен: {len(open_shifts)}")
        for row in open_shifts:
            print(f"  shift_id={row['id']}  date={row['date']}  employee={row['employee']}")
        print()

        shift_ids = [int(r["id"]) for r in open_shifts]
        placeholders = ",".join("?" * len(shift_ids))

        # 2. checklist_state.completed для open-чек-листа
        states = conn.execute(
            f"""
            SELECT id, shift_id, completed, active_section
            FROM checklist_state
            WHERE checklist_type = 'open' AND shift_id IN ({placeholders})
            """,
            shift_ids,
        ).fetchall()
        print(f"checklist_state записей для open: {len(states)}")
        state_updates: list[tuple[str, int]] = []
        for row in states:
            old = _load_completed(row["completed"])
            new = _shift_indexes(old, args.offset)
            print(
                f"  state_id={row['id']} shift_id={row['shift_id']}: "
                f"completed {old} -> {new}"
            )
            state_updates.append((json.dumps(new), int(row["id"])))

        # 3. open_checklist_media.item_index
        media = conn.execute(
            f"""
            SELECT id, shift_id, item_index, item_label
            FROM open_checklist_media
            WHERE shift_id IN ({placeholders})
            ORDER BY shift_id, item_index DESC
            """,
            shift_ids,
        ).fetchall()
        print(f"open_checklist_media записей: {len(media)}")
        media_updates: list[tuple[int, int]] = []
        for row in media:
            old_idx = int(row["item_index"])
            new_idx = old_idx + args.offset
            print(
                f"  media_id={row['id']} shift_id={row['shift_id']} "
                f"label={row['item_label']!r}: index {old_idx} -> {new_idx}"
            )
            media_updates.append((new_idx, int(row["id"])))

        if not args.commit:
            print()
            print("DRY-RUN: изменения не применены. Запустите с --commit, чтобы сохранить.")
            return 0

        # 4. Бэкап + транзакция
        backup_path = _backup_db(db_path)
        print()
        print(f"Бэкап БД: {backup_path}")

        try:
            conn.execute("BEGIN")
            for completed_json, state_id in state_updates:
                conn.execute(
                    "UPDATE checklist_state SET completed = ? WHERE id = ?",
                    (completed_json, state_id),
                )
            # ORDER BY ...DESC выше — обновляем item_index с конца, чтобы
            # не наткнуться на UNIQUE(shift_id, item_index) во время сдвига.
            for new_idx, media_id in media_updates:
                conn.execute(
                    "UPDATE open_checklist_media SET item_index = ? WHERE id = ?",
                    (new_idx, media_id),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        print("Готово. Изменения применены.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
