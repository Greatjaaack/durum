#!/usr/bin/env python3
"""
Шаг 1: логин + отправка 2FA кода на email.
Сохраняет состояние сессии в .session_state.json для step2.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

USERNAME = os.getenv("XIAOMI_USERNAME", "")
PASSWORD = os.getenv("XIAOMI_PASSWORD", "")

STATE_FILE = Path(__file__).parent / ".session_state.pkl"


def make_agent() -> str:
    import random
    agent_id = "".join(chr(r) for r in [random.randint(65, 69) for _ in range(13)])
    prefix = "".join(chr(r) for r in [random.randint(97, 122) for _ in range(18)])
    return f"{prefix}-{agent_id} APP/com.xiaomi.mihome APPV/10.5.201"


def make_device_id() -> str:
    import random
    return "".join(chr(random.randint(97, 122)) for _ in range(6))


def to_json(text: str) -> dict:
    return json.loads(text.replace("&&&START&&&", ""))


def main() -> None:
    agent = make_agent()
    device_id = make_device_id()
    session = requests.Session()
    session.cookies.set("sdkVersion", "accountsdk-18.8.15", domain="mi.com")
    session.cookies.set("sdkVersion", "accountsdk-18.8.15", domain="xiaomi.com")
    session.cookies.set("deviceId", device_id, domain="mi.com")
    session.cookies.set("deviceId", device_id, domain="xiaomi.com")

    headers = {"User-Agent": agent, "Content-Type": "application/x-www-form-urlencoded"}

    # ── Step 1: получаем _sign ──
    print("[ 1 / 3 ] Получаю sign...")
    r = session.get(
        "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true",
        headers=headers,
        cookies={"userId": USERNAME},
    )
    j1 = to_json(r.text)
    sign = j1.get("_sign", "")

    # ── Step 2: отправляем credentials ──
    print("[ 2 / 3 ] Отправляю credentials...")
    fields = {
        "sid": "xiaomiio",
        "hash": hashlib.md5(PASSWORD.encode()).hexdigest().upper(),
        "callback": "https://sts.api.io.mi.com/sts",
        "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
        "user": USERNAME,
        "_sign": sign,
        "_json": "true",
    }
    r = session.post(
        "https://account.xiaomi.com/pass/serviceLoginAuth2",
        headers=headers, params=fields, allow_redirects=False,
    )
    j2 = to_json(r.text)
    print(f"   code={j2.get('code')} captchaUrl={j2.get('captchaUrl')} notificationUrl={'есть' if j2.get('notificationUrl') else 'нет'}")

    notification_url = j2.get("notificationUrl")
    if not notification_url:
        print("❌ 2FA не требуется или другая ошибка. Полный ответ:")
        print(json.dumps({k: v for k, v in j2.items() if k not in ("ssecurity", "passToken")}, ensure_ascii=False, indent=2))
        exit(1)

    # ── Step 3: запускаем 2FA flow ──
    print("[ 3 / 3 ] Запускаю 2FA email flow...")

    # authStart
    session.get(notification_url, headers=headers)

    # identity/list
    context = parse_qs(urlparse(notification_url).query)["context"][0]
    session.get(
        "https://account.xiaomi.com/identity/list",
        params={"sid": "xiaomiio", "context": context, "_locale": "en_US"},
        headers=headers,
    )

    # sendEmailTicket — отправляет письмо
    r = session.post(
        "https://account.xiaomi.com/identity/auth/sendEmailTicket",
        params={"_dc": str(int(time.time() * 1000)), "sid": "xiaomiio", "context": context, "mask": "0", "_locale": "en_US"},
        data={"retry": "0", "icode": "", "_json": "true", "ick": session.cookies.get("ick", "")},
        headers=headers,
    )
    print(f"   sendEmailTicket: status={r.status_code}")

    # Сохраняем состояние
    state = {
        "context": context,
        "agent": agent,
    }
    STATE_FILE.write_bytes(pickle.dumps({"session": session, "state": state}))
    print(f"\n✅ Письмо отправлено на {USERNAME}")
    print(f"   Состояние сохранено в {STATE_FILE}")
    print(f"\n→ Теперь проверь почту и запусти:")
    print(f"   XIAOMI_2FA_CODE=<код> venv/bin/python step2_verify.py")


if __name__ == "__main__":
    main()
