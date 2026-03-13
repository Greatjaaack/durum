from __future__ import annotations

import logging
from typing import Any

import aiohttp


logger = logging.getLogger(__name__)


class OpenRouterClient:
    """Клиент для взаимодействия с OpenRouter API."""

    # URL endpoint для Chat Completions API OpenRouter.
    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    # Базовый промпт для генерации факта о еде.
    FOOD_FACT_PROMPT = "Напиши короткий интересный факт о еде или кухне. 1–2 предложения."

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_sec: int = 45,
        app_title: str = "Durum Shift Bot",
    ) -> None:
        """Инициализирует клиент OpenRouter.

        Args:
            api_key: API-ключ OpenRouter.
            model: Идентификатор модели.
            timeout_sec: Таймаут HTTP-запроса в секундах.
            app_title: Название приложения для заголовка X-Title.

        Returns:
            None.
        """
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout_sec = timeout_sec
        self.app_title = app_title.strip()

    @property
    def enabled(self) -> bool:
        """Проверяет, что клиент настроен для работы.

        Args:
            Нет параметров.

        Returns:
            True, если заданы ключ и модель.
        """
        return bool(self.api_key and self.model)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 350,
    ) -> str:
        """Отправляет chat completion запрос в OpenRouter.

        Args:
            messages: Список сообщений в формате OpenRouter.
            temperature: Параметр вариативности генерации.
            max_tokens: Максимальное число токенов в ответе.

        Returns:
            Текстовый ответ модели.
        """
        if not self.enabled:
            raise RuntimeError("OpenRouter is not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.app_title:
            headers["X-Title"] = self.app_title

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.API_URL, json=payload, headers=headers) as response:
                try:
                    data = await response.json(content_type=None)
                except Exception as exc:
                    text = await response.text()
                    raise RuntimeError(
                        f"OpenRouter returned non-JSON response ({response.status}): {text[:200]}"
                    ) from exc

        if response.status >= 400:
            error_text = ""
            if isinstance(data, dict):
                error_payload = data.get("error")
                if isinstance(error_payload, dict):
                    error_text = str(error_payload.get("message", "")).strip()
                elif error_payload is not None:
                    error_text = str(error_payload).strip()
            raise RuntimeError(f"OpenRouter error {response.status}: {error_text or 'unknown error'}")

        if not isinstance(data, dict):
            raise RuntimeError("OpenRouter response has invalid format")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("OpenRouter response does not contain choices")

        message = choices[0].get("message", {})
        content = message.get("content")
        text = self._extract_text(content)
        if not text:
            raise RuntimeError("OpenRouter response is empty")
        return text

    async def generate_food_fact(self) -> str:
        """Генерирует короткий факт о еде.

        Args:
            Нет параметров.

        Returns:
            Сгенерированный факт о еде.
        """
        messages = [
            {
                "role": "system",
                "content": "Ты помощник кухни. Отвечай кратко и по делу.",
            },
            {
                "role": "user",
                "content": self.FOOD_FACT_PROMPT,
            },
        ]
        return await self.chat(messages, temperature=0.8, max_tokens=120)

    @staticmethod
    def _extract_text(content: Any) -> str:
        """Извлекает текст из ответа OpenRouter в разных форматах.

        Args:
            content: Контент ответа модели.

        Returns:
            Нормализованный текст.
        """
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            chunks: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = str(block.get("text", "")).strip()
                        if text:
                            chunks.append(text)
                    elif "text" in block:
                        text = str(block.get("text", "")).strip()
                        if text:
                            chunks.append(text)
                elif isinstance(block, str):
                    text = block.strip()
                    if text:
                        chunks.append(text)
            return "\n".join(chunks).strip()

        logger.warning("Unexpected OpenRouter content type: %s", type(content).__name__)
        return ""
