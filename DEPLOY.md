# Деплой Durum Bot на сервер

Прод-запуск в Docker с автоматическим HTTPS для дашборда на `https://bot.iskendy.ru`.
Локальная разработка — в `CLAUDE.md` (`docker compose up --build`, дашборд на :8000 без TLS).

## Что нужно на сервере

- Docker + docker compose (плагин v2).
- **Открытые порты 80 и 443** (для Let's Encrypt и доступа к дашборду).
- **Домен** `bot.iskendy.ru` с A-записью DNS на IP сервера (для авто-HTTPS).
  Без домена можно по IP с self-signed (см. `Caddyfile`), но браузер будет ругаться.

## Архитектура прода (общий reverse-proxy)

На сервере уже работает **один Caddy от проекта аналитики** — он держит порты
80/443 и обслуживает оба домена. Второй Caddy у бота **не поднимается** (иначе
конфликт портов). Дашборд бота подключается к сети аналитики, и тот Caddy
проксирует на него `bot.iskendy.ru`.

```
Интернет ─┬─ analytics.iskendy.ru → frontend (аналитика)
          └─ bot.iskendy.ru       → durum-dashboard-1:8000 (этот проект)
                 ▲
          общий Caddy (443, авто-TLS), сеть dashboards_default
Telegram  → bot (long polling, наружу ничего не публикует)
```

- **Caddy аналитики** терминирует HTTPS и сам продлевает сертификаты обоих доменов.
- **dashboard** наружу НЕ опубликован (`expose`, не `ports`) — доступен только через
  Caddy. Подключён к внешней сети `dashboards_default`, чтобы Caddy резолвил его по имени.
- **bot** работает по long polling к Telegram — входящих портов не требует.
- Cookie сессии в проде ставится с флагом `Secure` (`DASHBOARD_COOKIE_SECURE=true`).
- БД (`/data/shifts.db`) и медиа — в томах `./data`/`./logs`, переживают пересборку.

## Подключение к общему Caddy (разово)

1. **Узнать имя сети аналитики** (где живёт её Caddy):
   ```bash
   docker network ls | grep -i caddy   # или: docker inspect <caddy_контейнер> | grep -i network
   ```
   Обычно `dashboards_default`. Если иначе — поправь `name:` в блоке `networks` в
   `docker-compose.prod.yml`.

2. **Добавить домен бота в Caddyfile аналитики** (в папке аналитики на сервере):
   ```caddy
   bot.iskendy.ru {
       encode gzip
       reverse_proxy durum-dashboard-1:8000
   }
   ```

3. **Перезагрузить Caddy аналитики** (из папки аналитики):
   ```bash
   docker compose -f docker-compose.prod.yml exec caddy caddy reload --config /etc/caddy/Caddyfile
   # или, если reload недоступен:  docker compose -f docker-compose.prod.yml restart caddy
   ```

> DNS: на `bot.iskendy.ru` нужна A-запись на IP сервера — иначе Caddy не выпустит cert.

## Чек-лист первого запуска

1. **Склонировать репозиторий и зайти в папку.**

2. **Создать `.env`** (его нет в git):
   ```bash
   cp .env.example .env
   ```
   Обязательно проставить реальные значения:
   - `BOT_TOKEN`, `OWNER_ID`, `WORK_CHAT_ID` — Telegram;
   - `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` — **свой** логин/пароль входа в дашборд;
   - `DASHBOARD_SECRET` — свой случайный секрет (`openssl rand -hex 32`).

3. **Поднять прод-конфигурацию:**
   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```

4. **Один раз подключить к общему Caddy** — см. раздел «Подключение к общему Caddy»
   выше (добавить блок в Caddyfile аналитики и перезагрузить её Caddy).

5. **Проверить логи:**
   ```bash
   docker compose -f docker-compose.prod.yml logs -f
   ```

6. Открыть `https://bot.iskendy.ru` → войти логином/паролем из `.env`.

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
