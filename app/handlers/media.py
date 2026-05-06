"""Утилиты загрузки медиа из Telegram на диск.

Вынесено из ``app/handlers/shift.py`` в рамках поэтапного рефакторинга,
чтобы изолировать I/O-побочки чек-листов (фото холодильника, фото
корзины фритюра и т.п.) от FSM-логики смен.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from aiogram import Bot


logger = logging.getLogger(__name__)


# Поддерживаемые типы медиа для имени подкаталога.
MediaKind = Literal["open", "close"]


def _ext_for_mime(mime_type: str | None) -> str:
    """Возвращает расширение файла по MIME-типу.

    Args:
        mime_type: MIME-тип Telegram-вложения; может быть None.

    Returns:
        Расширение с точкой; по умолчанию ``.jpg``.
    """
    if not mime_type:
        return ".jpg"
    if "png" in mime_type:
        return ".png"
    if "gif" in mime_type:
        return ".gif"
    if "pdf" in mime_type:
        return ".pdf"
    return ".jpg"


async def download_media_to_disk(
    bot: Bot,
    *,
    file_id: str,
    shift_id: int,
    item_index: int,
    mime_type: str | None,
    media_type: MediaKind,
) -> str | None:
    """Скачивает файл из Telegram и сохраняет на диск.

    Каталог берётся из ``MEDIA_DIR`` (дефолт ``data/media``) с подпапкой
    ``open`` или ``close``. Имя файла однозначно адресует пункт чек-листа
    по конкретной смене (``{shift_id}_{item_index}{ext}``), что упрощает
    отладку и сопоставление с записями в БД.

    Args:
        bot: Экземпляр Telegram-бота.
        file_id: Telegram file_id.
        shift_id: ID смены.
        item_index: Индекс пункта чек-листа.
        mime_type: MIME-тип файла.
        media_type: Тип медиа ('open' или 'close').

    Returns:
        Путь к сохранённому файлу или None при ошибке.
    """
    media_root = Path(os.getenv("MEDIA_DIR", "data/media")) / media_type
    media_root.mkdir(parents=True, exist_ok=True)
    dest = media_root / f"{shift_id}_{item_index}{_ext_for_mime(mime_type)}"
    try:
        await bot.download(file_id, destination=str(dest))
        return str(dest)
    except Exception:
        logger.exception("Failed to save media to disk file_id=%s shift_id=%s", file_id, shift_id)
        return None
