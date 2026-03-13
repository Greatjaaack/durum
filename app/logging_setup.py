from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TextIO


class DailyFileHandler(logging.Handler):
    """Обработчик логов с ежедневной ротацией в отдельный файл."""

    def __init__(
        self,
        log_dir: Path,
        *,
        encoding: str = "utf-8",
    ) -> None:
        """Инициализирует обработчик ежедневных логов.

        Args:
            log_dir: Путь до директории с логами.
            encoding: Кодировка файла лога.

        Returns:
            None.
        """
        super().__init__()
        self.log_dir = log_dir
        self.encoding = encoding
        self._current_date = ""
        self._stream: TextIO | None = None

    def _log_path_for_today(self) -> Path:
        """Возвращает путь к файлу логов за текущую дату.

        Args:
            Нет параметров.

        Returns:
            Путь к файлу лога за текущий день.
        """
        date_text = datetime.now().date().isoformat()
        return self.log_dir / f"app_{date_text}.log"

    def _ensure_stream(self) -> None:
        """Открывает или переключает поток лога при смене даты.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        today = datetime.now().date().isoformat()
        if self._stream is not None and self._current_date == today:
            return

        self._close_stream()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_path_for_today()
        self._stream = log_path.open("a", encoding=self.encoding)
        self._current_date = today

    def emit(
        self,
        record: logging.LogRecord,
    ) -> None:
        """Записывает запись лога в файл текущего дня.

        Args:
            record: Запись лога.

        Returns:
            None.
        """
        try:
            self._ensure_stream()
            if self._stream is None:
                return
            message = self.format(record)
            self._stream.write(message + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def _close_stream(self) -> None:
        """Закрывает текущий поток файла лога.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def close(self) -> None:
        """Закрывает обработчик и освобождает файловый дескриптор.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        self._close_stream()
        super().close()


def configure_logging(
    log_dir: str | Path = "logs",
) -> None:
    """Настраивает консольное и файловое логирование приложения.

    Args:
        log_dir: Директория хранения логов.

    Returns:
        None.
    """
    logs_path = Path(log_dir)
    logs_path.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = DailyFileHandler(logs_path)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
