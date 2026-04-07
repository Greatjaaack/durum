#!/usr/bin/env python3
"""
Исследовательский скрипт: Xiaomi cloud API для камер.
Пробует разные подходы к аутентификации и получению списка записей.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

USERNAME = os.getenv("XIAOMI_USERNAME", "")
PASSWORD = os.getenv("XIAOMI_PASSWORD", "")
REGION = os.getenv("XIAOMI_REGION", "ru")

if not USERNAME or not PASSWORD:
    print("ERROR: Set XIAOMI_USERNAME and XIAOMI_PASSWORD in .env")
    sys.exit(1)


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("="*60)


# ──────────────────────────────────────────────────────────────
# 1. Аутентификация через micloud
# ──────────────────────────────────────────────────────────────
section("1. micloud — login + get_devices")
try:
    from micloud import MiCloud  # type: ignore

    mc = MiCloud(USERNAME, PASSWORD)
    login_ok = mc.login()
    print(f"login(): {login_ok}")

    if login_ok:
        devices = mc.get_devices(country=REGION)
        print(f"\nНайдено устройств: {len(devices) if devices else 0}")
        for d in (devices or []):
            model = d.get("model", "?")
            name = d.get("name", "?")
            did = d.get("did", "?")
            localip = d.get("localip", "нет IP")
            print(f"  [{model}] {name}  did={did}  ip={localip}")

        # Сохраним полный список для анализа
        with open("devices.json", "w") as f:
            json.dump(devices, f, ensure_ascii=False, indent=2)
        print("\n→ Полный список сохранён в devices.json")

except ImportError:
    print("micloud не установлен")
except Exception as exc:
    print(f"Ошибка micloud: {exc}")


# ──────────────────────────────────────────────────────────────
# 2. python-miio CloudInterface
# ──────────────────────────────────────────────────────────────
section("2. python-miio — CloudInterface")
try:
    from miio.cloud_interface import CloudInterface  # type: ignore

    ci = CloudInterface(USERNAME, PASSWORD)
    devices_miio = ci.get_devices()
    print(f"Устройств через miio: {len(devices_miio)}")
    for did, dev in devices_miio.items():
        print(f"  {did}: {dev}")

except ImportError:
    print("miio.cloud_interface не доступен в этой версии")
except Exception as exc:
    print(f"Ошибка miio CloudInterface: {exc}")


# ──────────────────────────────────────────────────────────────
# 3. Попытка получить token и подключиться к камере через cloud
# ──────────────────────────────────────────────────────────────
section("3. Попытка отправить команды камере через cloud (miio)")
try:
    from micloud import MiCloud  # type: ignore
    from miio import DeviceFactory  # type: ignore

    mc2 = MiCloud(USERNAME, PASSWORD)
    if mc2.login():
        devices2 = mc2.get_devices(country=REGION) or []
        cameras = [d for d in devices2 if "camera" in d.get("model", "").lower()
                   or "cam" in d.get("model", "").lower()
                   or "c200" in d.get("model", "").lower()
                   or "chuangmi" in d.get("model", "").lower()
                   or "isa" in d.get("model", "").lower()]

        if not cameras:
            print("Камеры не найдены по имени модели. Все устройства:")
            for d in devices2:
                print(f"  model={d.get('model')} name={d.get('name')}")
        else:
            for cam in cameras:
                print(f"\nКамера: {cam.get('name')} | model={cam.get('model')}")
                print(f"  did={cam.get('did')} ip={cam.get('localip')} token={cam.get('token','нет')}")

                token = cam.get("token", "")
                ip = cam.get("localip", "")
                if token and ip:
                    try:
                        device = DeviceFactory.create(ip, token)
                        print(f"  Устройство создано: {device}")
                        # Попробуем базовые команды
                        info = device.info()
                        print(f"  info(): {info}")
                    except Exception as e:
                        print(f"  Ошибка подключения: {e}")
                else:
                    print("  Нет IP или токена (устройство вне локальной сети)")

except Exception as exc:
    print(f"Ошибка: {exc}")


# ──────────────────────────────────────────────────────────────
# 4. MIoT cloud команды (если устройство поддерживает)
# ──────────────────────────────────────────────────────────────
section("4. MIoT cloud — попытка через cloud_interface + miot_send")
try:
    from micloud import MiCloud  # type: ignore
    import requests

    mc3 = MiCloud(USERNAME, PASSWORD)
    if mc3.login():
        devices3 = mc3.get_devices(country=REGION) or []
        # Пробуем получить cloud token для MIoT запросов
        token = getattr(mc3, "service_token", None) or getattr(mc3, "_service_token", None)
        user_id = getattr(mc3, "userId", None) or getattr(mc3, "_user_id", None)
        print(f"service_token: {'есть' if token else 'нет'}")
        print(f"userId: {user_id}")

        # Атрибуты объекта MiCloud
        attrs = [a for a in dir(mc3) if not a.startswith("__")]
        print(f"Доступные атрибуты MiCloud: {attrs}")

except Exception as exc:
    print(f"Ошибка MIoT: {exc}")


print("\n" + "="*60)
print("  Исследование завершено")
print("="*60)
