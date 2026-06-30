# Деплой Durum Bot на сервер

Прод-запуск в Docker с автоматическим HTTPS для дашборда на `https://bot.iskendy.ru`.
Локальная разработка — в `CLAUDE.md` (`docker compose up --build`, дашборд на :8000 без TLS).

## Что нужно на сервере

- Docker + docker compose (плагин v2).
- **Открытые порты 80 и 443** (для Let's Encrypt и доступа к дашборду).
- **Домен** `bot.iskendy.ru` с A-записью DNS на IP сервера (для авто-HTTPS).
  Без домена можно по IP с self-signed (см. `Caddyfile`), но браузер будет ругаться.

## Архитектура прода

```
Интернет → Caddy (443, авто-TLS) → dashboard:8000 (FastAPI, cookie-auth)
Telegram  → bot (long polling, наружу ничего не публикует)
```

- **Caddy** терминирует HTTPS и сам продлевает сертификат.
- **dashboard** наружу НЕ опубликован (`expose`, не `ports`) — доступен только через
  Caddy по 443. Пароль входа не летит по сети открытым текстом.
- **bot** работает по long polling к Telegram — входящих портов не требует.
- Cookie сессии в проде ставится с флагом `Secure` (`DASHBOARD_COOKIE_SECURE=true`
  задан в `docker-compose.prod.yml`).
- БД (`/data/shifts.db`) и медиа — в томах `./data`/`./logs`, переживают пересборку.

## Чек-лист первого запуска

1. **Склонировать репозиторий и зайти в папку.**

2. **Создать `.env`** (его нет в git):
   ```bash
   cp .env.example .env
   ```
   Обязательно проставить реальные значения:
   - `BOT_TOKEN`, `OWNER_ID`, `WORK_CHAT_ID` — Telegram;
   - `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` — **свой** логин/пароль входа в дашборд;
   - `DASHBOARD_SECRET` — свой случайный секрет (`openssl rand -hex 32`);
   - `DOMAIN` — `bot.iskendy.ru`.

3. **Поднять прод-конфигурацию:**
   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```
   Caddy сам получит TLS-сертификат для `DOMAIN` (нужны открытые 80/443 и DNS).

4. **Проверить логи:**
   ```bash
   docker compose -f docker-compose.prod.yml logs -f
   ```

5. Открыть `https://bot.iskendy.ru` → войти логином/паролем из `.env`.

## Эксплуатация

```bash
# Обновить до свежего кода:
git pull
docker compose -f docker-compose.prod.yml up -d --build

# Логи / статус:
docker compose -f docker-compose.prod.yml logs -f
docker compose -f docker-compose.prod.yml ps

# Остановить (тома data/logs сохраняются):
docker compose -f docker-compose.prod.yml down
```

## Запуск без домена (по IP, для теста)

В `Caddyfile` закомментировать блок `{$DOMAIN}` и раскомментировать блок `:443`
с `tls internal`. Caddy отдаст self-signed сертификат — браузер предупредит, но
соединение будет зашифровано.
