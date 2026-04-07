#!/usr/bin/env python3
"""
Шаг 2: верификация 2FA кода + получение токенов.
Запускать: XIAOMI_2FA_CODE=123456 venv/bin/python step2_verify.py
"""
from __future__ import annotations

import json
import os
import pickle
import re
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

STATE_FILE = Path(__file__).parent / ".session_state.pkl"
SESSION_FILE = Path(__file__).parent / ".xiaomi_session.json"

code = os.getenv("XIAOMI_2FA_CODE", "").strip()
if not code:
    print("❌ Передай код: XIAOMI_2FA_CODE=123456 venv/bin/python step2_verify.py")
    exit(1)

if not STATE_FILE.exists():
    print("❌ Нет .session_state.pkl — сначала запусти step1_send_code.py")
    exit(1)

# Загружаем сессию и контекст
saved = pickle.loads(STATE_FILE.read_bytes())
session = saved["session"]
context = saved["state"]["context"]
agent = saved["state"]["agent"]

headers = {"User-Agent": agent, "Content-Type": "application/x-www-form-urlencoded"}

print(f"[ 1 / 3 ] Верифицирую код {code}...")
r = session.post(
    "https://account.xiaomi.com/identity/auth/verifyEmail",
    params={"_flag": "8", "_json": "true", "sid": "xiaomiio", "context": context, "mask": "0", "_locale": "en_US"},
    data={"_flag": "8", "ticket": code, "trust": "false", "_json": "true", "ick": session.cookies.get("ick", "")},
    headers=headers,
)
print(f"   verifyEmail: status={r.status_code}")
if r.status_code != 200:
    print(f"❌ Ошибка: {r.text[:300]}")
    exit(1)

try:
    jr = r.json()
    finish_loc = jr.get("location")
except Exception:
    finish_loc = r.headers.get("Location")
    if not finish_loc and r.text:
        m = re.search(r'https://account\.xiaomi\.com/identity/result/check\?[^"\']+', r.text)
        if m:
            finish_loc = m.group(0)

if not finish_loc:
    r0 = session.get(
        "https://account.xiaomi.com/identity/result/check",
        params={"sid": "xiaomiio", "context": context, "_locale": "en_US"},
        headers=headers, allow_redirects=False,
    )
    if r0.status_code in (301, 302):
        finish_loc = r0.headers.get("Location")

if not finish_loc:
    print(f"❌ Нет finish_loc. Ответ: {r.text[:300]}")
    exit(1)

print("[ 2 / 3 ] Получаю ssecurity...")
if "identity/result/check" in finish_loc:
    r = session.get(finish_loc, headers=headers, allow_redirects=False)
    end_url = r.headers.get("Location")
else:
    end_url = finish_loc

if not end_url:
    print(f"❌ Нет Auth2/end URL. finish_loc={finish_loc}")
    exit(1)

r = session.get(end_url, headers=headers, allow_redirects=False)
if r.status_code == 200 and "Xiaomi Account - Tips" in r.text:
    r = session.get(end_url, headers=headers, allow_redirects=False)

ssecurity = None
ext_prag = r.headers.get("extension-pragma")
if ext_prag:
    try:
        ep = json.loads(ext_prag)
        ssecurity = ep.get("ssecurity")
    except Exception:
        pass

if not ssecurity:
    print(f"❌ ssecurity не найден. Headers: {dict(r.headers)}")
    print(f"   Body: {r.text[:300]}")
    exit(1)

print("[ 3 / 3 ] Получаю serviceToken...")
sts_url = r.headers.get("Location")
if not sts_url and r.text:
    idx = r.text.find("https://sts.api.io.mi.com/sts")
    if idx != -1:
        end_idx = r.text.find('"', idx)
        sts_url = r.text[idx:] if end_idx == -1 else r.text[idx:end_idx]

if not sts_url:
    print("❌ Нет STS URL")
    exit(1)

r = session.get(sts_url, headers=headers, allow_redirects=True)
service_token = session.cookies.get("serviceToken")

if not service_token:
    print(f"❌ serviceToken не получен. Cookies: {dict(session.cookies)}")
    exit(1)

# userId
user_id = (
    session.cookies.get("userId", domain=".xiaomi.com")
    or session.cookies.get("userId", domain=".sts.api.io.mi.com")
    or session.cookies.get("userId")
)

# Сохраняем сессию для дальнейшего использования
session_data = {
    "user_id": user_id,
    "service_token": service_token,
    "ssecurity": ssecurity,
}
SESSION_FILE.write_text(json.dumps(session_data))

print(f"\n✅ Успешно!")
print(f"   user_id:       {user_id}")
print(f"   ssecurity:     {ssecurity[:20]}...")
print(f"   service_token: {service_token[:20]}...")
print(f"   Сохранено в:   {SESSION_FILE}")
print(f"\n→ Теперь запусти: venv/bin/python explore2.py")
