from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError


class DailyFileHandler(logging.Handler):
    """Обработчик логов с ежедневной ротацией в отдельный файл."""

    def __init__(
        self,
        log_dir: Path,
        timezone: str = "Europe/Moscow",
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
        self.timezone = timezone
        self._current_date = ""
        self._stream: TextIO | None = None
        try:
            self._tzinfo = ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            self._tzinfo = None

    def _today_text(self) -> str:
        """Возвращает текущую дату в целевой таймзоне.

        Args:
            Нет параметров.

        Returns:
            Строка даты YYYY-MM-DD.
        """
        if self._tzinfo is not None:
            return datetime.now(self._tzinfo).date().isoformat()
        return datetime.now().date().isoformat()

    def _log_path_for_today(self) -> Path:
        """Возвращает путь к файлу логов за текущую дату.

        Args:
            Нет параметров.

        Returns:
            Путь к файлу лога за текущий день.
        """
        date_text = self._today_text()
        return self.log_dir / f"app_{date_text}.log"

    def _ensure_stream(self) -> None:
        """Открывает или переключает поток лога при смене даты.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        today = self._today_text()
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


class TimezoneFormatter(logging.Formatter):
    """Форматтер логов с явной таймзоной."""

    def __init__(
        self,
        fmt: str,
        timezone: str = "Europe/Moscow",
    ) -> None:
        """Инициализирует форматтер с заданной таймзоной.

        Args:
            fmt: Формат строки лога.
            timezone: Имя таймзоны IANA.

        Returns:
            None.
        """
        super().__init__(fmt=fmt)
        self.timezone = timezone
        try:
            self._tzinfo = ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            self._tzinfo = None

    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        """Форматирует время записи лога в заданной таймзоне.

        Args:
            record: Запись лога.
            datefmt: Пользовательский формат времени.

        Returns:
            Строка времени.
        """
        if self._tzinfo is not None:
            dt = datetime.fromtimestamp(record.created, tz=self._tzinfo)
        else:
            dt = datetime.fromtimestamp(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(sep=" ", timespec="seconds")


_LOG_RETENTION_DAYS = 30


def _cleanup_old_logs(log_dir: Path, retention_days: int = _LOG_RETENTION_DAYS) -> None:
    """Удаляет файлы логов старше retention_days дней.

    Args:
        log_dir: Директория с логами.
        retention_days: Сколько дней хранить.

    Returns:
        None.
    """
    cutoff = datetime.now().date().toordinal() - retention_days
    for log_file in log_dir.glob("app_*.log"):
        try:
            date_part = log_file.stem.removeprefix("app_")
            file_date = datetime.strptime(date_part, "%Y-%m-%d").date()
            if file_date.toordinal() < cutoff:
                log_file.unlink()
        except (ValueError, OSError):
            continue


def configure_logging(
    log_dir: str | Path = "logs",
    timezone: str = "Europe/Moscow",
) -> None:
    """Настраивает консольное и файловое логирование приложения.

    Args:
        log_dir: Директория хранения логов.

    Returns:
        None.
    """
    logs_path = Path(log_dir)
    logs_path.mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs(logs_path)

    formatter = TimezoneFormatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        timezone=timezone,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = DailyFileHandler(logs_path, timezone=timezone)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
