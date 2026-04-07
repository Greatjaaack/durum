#!/usr/bin/env python3
"""
Шаг 2: аутентификация + исследование устройств и API камер.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from xiaomi_cloud import XiaomiCloudConnector

USERNAME = os.getenv("XIAOMI_USERNAME", "")
PASSWORD = os.getenv("XIAOMI_PASSWORD", "")
REGION   = os.getenv("XIAOMI_REGION", "ru")


def section(t: str) -> None:
    print(f"\n{'='*60}\n  {t}\n{'='*60}")


connector = XiaomiCloudConnector(USERNAME, PASSWORD, REGION)

section("1. Логин")
if not connector.login():
    print("Логин не удался. Выход.")
    sys.exit(1)


section("2. Список домов")
homes_resp = connector.get_homes()
print(json.dumps(homes_resp, ensure_ascii=False, indent=2)[:2000])

homes = []
if homes_resp and "result" in homes_resp:
    result = homes_resp["result"]
    home_list = result.get("homelist") or result.get("result", {}).get("homelist", [])
    if isinstance(home_list, list):
        homes = home_list
    print(f"\nНайдено домов: {len(homes)}")
    for h in homes:
        print(f"  [{h.get('id')}] {h.get('name')}")


section("3. Устройства во всех домах (включая shared)")
all_devices: list[dict] = []
seen_dids: set = set()

result_data = homes_resp.get("result", {}) if homes_resp else {}
all_home_lists = (result_data.get("homelist") or []) + (result_data.get("share_home_list") or [])

for home in all_home_lists:
    hid = home.get("id")
    uid = home.get("uid") or connector.user_id
    shared = home.get("shareflag", 0)
    label = "shared" if shared else "own"
    print(f"\n  Дом [{label}]: {home.get('name')} (id={hid}, uid={uid})")
    devs_resp = connector.get_devices(hid, uid)
    if devs_resp:
        dev_result = devs_resp.get("result") or {}
        devlist = dev_result.get("device_info") or dev_result.get("device_list") or []
        for d in devlist:
            did = d.get("did", "")
            if did in seen_dids:
                continue
            seen_dids.add(did)
            all_devices.append(d)
            model = d.get("model", "?")
            name  = d.get("name", "?")
            ip    = d.get("localip", "нет IP")
            print(f"    [{model}] {name}  did={did}  ip={ip}")

if not all_devices:
    print("\n  Устройства не найдены. Пробуем прямой список...")
    fallback = connector.get_all_devices()
    for d in fallback:
        all_devices.append(d)
        model = d.get("model", "?")
        name  = d.get("name", "?")
        did   = d.get("did", "?")
        ip    = d.get("localip", "нет IP")
        print(f"    [{model}] {name}  did={did}  ip={ip}")

# Сохраняем
with open("all_devices.json", "w") as f:
    json.dump(all_devices, f, ensure_ascii=False, indent=2)
print(f"\n→ Все устройства сохранены в all_devices.json")


section("4. Камеры")
cameras = [
    d for d in all_devices
    if any(k in d.get("model", "").lower() for k in (
        "camera", "cam", "c200", "chuangmi", "isa.cam", "miot.cam", "xiaomi.cam"
    ))
]
if not cameras:
    # Если не нашли по модели — покажем все модели для ручного выбора
    print("Камеры не найдены автоматически. Все модели:")
    for d in all_devices:
        print(f"  {d.get('model')} — {d.get('name')}")
    print("\nПоправь фильтр выше если видишь камеры в списке.")
else:
    print(f"Найдено камер: {len(cameras)}")
    for cam in cameras:
        print(f"\n  Камера: {cam.get('name')}")
        print(f"  model={cam.get('model')}  did={cam.get('did')}  token={cam.get('token','нет')}")

        did = cam.get("did", "")

        # Попробуем MIoT свойства (siid=1 — device info, siid=3 — camera)
        print("\n  → MIoT свойства (siid=3 prop 1-10):")
        props_resp = connector.miot_get_props(did, [
            {"siid": s, "piid": p} for s in [1, 2, 3, 4, 5, 6] for p in [1, 2, 3, 4, 5]
        ])
        print(json.dumps(props_resp, ensure_ascii=False, indent=2)[:1000] if props_resp else "  нет ответа")

print("\n" + "="*60 + "\n  Готово\n" + "="*60)
