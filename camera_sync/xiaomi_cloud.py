"""
Xiaomi cloud connector — аутентификация + API-вызовы.
Адаптировано из PiotrMachowski/Xiaomi-cloud-tokens-extractor.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Any

import requests
from Crypto.Cipher import ARC4


class XiaomiCloudConnector:
    """Подключение к Xiaomi cloud с CAPTCHA/2FA поддержкой."""

    SESSION_FILE = Path(__file__).parent / ".xiaomi_session.json"

    def __init__(self, username: str, password: str, region: str = "ru") -> None:
        self._username = username
        self._password = password
        self._region = region
        self._agent = self._make_agent()
        self._device_id = self._make_device_id()
        self._session = requests.Session()
        self._ssecurity: str | None = None
        self.user_id: str | None = None
        self._service_token: str | None = None
        self._sign: str | None = None
        self._location: str | None = None

    # ──────────────────────────────────────────────────────────
    # Инициализация
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _make_agent() -> str:
        agent_id = "".join(chr(random.randint(65, 69)) for _ in range(13))
        prefix = "".join(chr(random.randint(97, 122)) for _ in range(18))
        return f"{prefix}-{agent_id} APP/com.xiaomi.mihome APPV/10.5.201"

    @staticmethod
    def _make_device_id() -> str:
        return "".join(chr(random.randint(97, 122)) for _ in range(6))

    @staticmethod
    def _to_json(text: str) -> dict:
        return json.loads(text.replace("&&&START&&&", ""))

    # ──────────────────────────────────────────────────────────
    # Сохранение / загрузка сессии
    # ──────────────────────────────────────────────────────────

    def _save_session(self) -> None:
        data = {
            "user_id": self.user_id,
            "service_token": self._service_token,
            "ssecurity": self._ssecurity,
        }
        self.SESSION_FILE.write_text(json.dumps(data))
        print(f"  [session] сохранена в {self.SESSION_FILE}")

    def _load_session(self) -> bool:
        if not self.SESSION_FILE.exists():
            return False
        try:
            data = json.loads(self.SESSION_FILE.read_text())
            self.user_id = data["user_id"]
            self._service_token = data["service_token"]
            self._ssecurity = data["ssecurity"]
            if self.user_id and self._service_token and self._ssecurity:
                print("  [session] загружена из кэша")
                return True
        except Exception:
            pass
        return False

    # ──────────────────────────────────────────────────────────
    # Логин
    # ──────────────────────────────────────────────────────────

    def login(self) -> bool:
        """Логин с поддержкой CAPTCHA. Кэширует сессию."""
        if self._load_session():
            # Проверяем что сессия ещё рабочая
            if self._ping():
                return True
            print("  [session] истекла, повторный логин...")

        self._session.cookies.set("sdkVersion", "accountsdk-18.8.15", domain="mi.com")
        self._session.cookies.set("sdkVersion", "accountsdk-18.8.15", domain="xiaomi.com")
        self._session.cookies.set("deviceId", self._device_id, domain="mi.com")
        self._session.cookies.set("deviceId", self._device_id, domain="xiaomi.com")

        if not self._step1():
            print("❌ Шаг 1 провалился (неверный username?)")
            return False
        if not self._step2():
            print("❌ Шаг 2 провалился")
            return False
        if self._location and not self._step3():
            print("❌ Шаг 3 провалился (не получили service token)")
            return False

        self._save_session()
        print(f"✓ Залогинились как user_id={self.user_id}")
        return True

    def _step1(self) -> bool:
        url = "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true"
        headers = {"User-Agent": self._agent, "Content-Type": "application/x-www-form-urlencoded"}
        r = self._session.get(url, headers=headers, cookies={"userId": self._username})
        j = self._to_json(r.text)
        if r.status_code == 200 and "_sign" in j:
            self._sign = j["_sign"]
            return True
        return False

    def _step2(self, captcha_code: str | None = None, _attempt: int = 0) -> bool:
        if _attempt > 3:
            print("❌ Превышено количество попыток CAPTCHA")
            return False

        url = "https://account.xiaomi.com/pass/serviceLoginAuth2"
        headers = {"User-Agent": self._agent, "Content-Type": "application/x-www-form-urlencoded"}
        fields: dict[str, str] = {
            "sid": "xiaomiio",
            "hash": hashlib.md5(self._password.encode()).hexdigest().upper(),
            "callback": "https://sts.api.io.mi.com/sts",
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "user": self._username,
            "_sign": self._sign or "",
            "_json": "true",
        }
        if captcha_code:
            fields["captCode"] = captcha_code

        r = self._session.post(url, headers=headers, params=fields, allow_redirects=False)
        j = self._to_json(r.text)

        # Нужна CAPTCHA
        captcha_url = j.get("captchaUrl")
        if captcha_url:
            if captcha_url.startswith("/"):
                captcha_url = "https://account.xiaomi.com" + captcha_url
            if captcha_code:
                print(f"   ❌ Код '{captcha_code}' неверный, нужна новая CAPTCHA")
            code = self._solve_captcha(captcha_url)
            if not code:
                return False
            return self._step2(captcha_code=code, _attempt=_attempt + 1)

        # 2FA
        notification_url = j.get("notificationUrl")
        if notification_url:
            return self._do_2fa_email_flow(notification_url)

        if "ssecurity" in j and len(str(j["ssecurity"])) > 4:
            self._ssecurity = j["ssecurity"]
            self.user_id = str(j.get("userId", ""))
            self._location = j.get("location")
            return True

        print(f"  step2 response: code={j.get('code')} desc={j.get('description') or j.get('desc', '?')}")
        return False

    def _step3(self) -> bool:
        headers = {"User-Agent": self._agent}
        r = self._session.get(self._location, headers=headers)  # type: ignore[arg-type]
        if r.status_code == 200:
            self._service_token = r.cookies.get("serviceToken")
            return bool(self._service_token)
        return False

    def _solve_captcha(self, captcha_url: str) -> str:
        """Скачивает CAPTCHA, открывает в Preview, просит ввод."""
        import sys

        print("\n⚠️  Xiaomi требует CAPTCHA.")
        img_r = self._session.get(captcha_url)
        captcha_path = Path("/tmp/xiaomi_captcha.jpg")
        captcha_path.write_bytes(img_r.content)
        print(f"   Изображение: {captcha_path}")
        try:
            subprocess.run(["open", str(captcha_path)], check=False)
        except Exception:
            pass

        # Принять из переменной окружения (используется только один раз)
        env_code = os.environ.pop("XIAOMI_CAPTCHA_CODE", "").strip()
        if env_code:
            print(f"   Используем CAPTCHA из env: {env_code}")
            return env_code

        # Интерактивный ввод
        try:
            code = input("   Введи текст с картинки: ").strip()
        except EOFError:
            print("   (stdin закрыт, используй XIAOMI_CAPTCHA_CODE=... env var)")
            code = ""
        return code

    def _do_2fa_email_flow(self, notification_url: str) -> bool:
        """2FA через email: отправляет письмо с кодом, ждёт ввода."""
        from urllib.parse import parse_qs, urlparse

        headers = {"User-Agent": self._agent, "Content-Type": "application/x-www-form-urlencoded"}

        # 1) authStart
        self._session.get(notification_url, headers=headers)

        # 2) identity/list
        context = parse_qs(urlparse(notification_url).query)["context"][0]
        list_params = {"sid": "xiaomiio", "context": context, "_locale": "en_US"}
        self._session.get("https://account.xiaomi.com/identity/list", params=list_params, headers=headers)

        # 3) sendEmailTicket — шлёт письмо с кодом
        send_params = {
            "_dc": str(int(time.time() * 1000)),
            "sid": "xiaomiio",
            "context": context,
            "mask": "0",
            "_locale": "en_US",
        }
        send_data = {
            "retry": "0",
            "icode": "",
            "_json": "true",
            "ick": self._session.cookies.get("ick", ""),
        }
        self._session.post(
            "https://account.xiaomi.com/identity/auth/sendEmailTicket",
            params=send_params, data=send_data, headers=headers,
        )

        print("\n📧  Xiaomi отправил письмо с кодом на твою почту.")
        print("   Проверь greatjaaack@gmail.com (и папку Spam).")

        # Принять код из env или интерактивно
        env_code = os.environ.pop("XIAOMI_2FA_CODE", "").strip()
        if env_code:
            code = env_code
            print(f"   Используем 2FA код из env: {code}")
        else:
            try:
                code = input("   Введи код из письма: ").strip()
            except EOFError:
                print("   (stdin закрыт, используй XIAOMI_2FA_CODE=... env var)")
                return False

        # 4) verifyEmail
        verify_params = {
            "_flag": "8", "_json": "true", "sid": "xiaomiio",
            "context": context, "mask": "0", "_locale": "en_US",
        }
        verify_data = {
            "_flag": "8", "ticket": code, "trust": "false",
            "_json": "true", "ick": self._session.cookies.get("ick", ""),
        }
        r = self._session.post(
            "https://account.xiaomi.com/identity/auth/verifyEmail",
            params=verify_params, data=verify_data, headers=headers,
        )
        if r.status_code != 200:
            print(f"❌ verifyEmail вернул {r.status_code}")
            return False

        import re
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
            r0 = self._session.get(
                "https://account.xiaomi.com/identity/result/check",
                params={"sid": "xiaomiio", "context": context, "_locale": "en_US"},
                headers=headers, allow_redirects=False,
            )
            if r0.status_code in (301, 302) and r0.headers.get("Location"):
                finish_loc = r0.headers["Location"]

        if not finish_loc:
            print("❌ Не удалось получить finish_loc после verifyEmail")
            return False

        # 5) Первый хоп — identity/result/check
        if "identity/result/check" in finish_loc:
            r = self._session.get(finish_loc, headers=headers, allow_redirects=False)
            end_url = r.headers.get("Location")
        else:
            end_url = finish_loc

        if not end_url:
            print("❌ Нет Auth2/end URL")
            return False

        # 6) Auth2/end — получаем ssecurity из заголовка extension-pragma
        r = self._session.get(end_url, headers=headers, allow_redirects=False)
        if r.status_code == 200 and "Xiaomi Account - Tips" in r.text:
            r = self._session.get(end_url, headers=headers, allow_redirects=False)

        ext_prag = r.headers.get("extension-pragma")
        if ext_prag:
            try:
                ep = json.loads(ext_prag)
                if ep.get("ssecurity"):
                    self._ssecurity = ep["ssecurity"]
            except Exception:
                pass

        if not self._ssecurity:
            print("❌ ssecurity не найден в extension-pragma")
            return False

        # 7) STS → serviceToken cookie
        sts_url = r.headers.get("Location")
        if not sts_url and r.text:
            idx = r.text.find("https://sts.api.io.mi.com/sts")
            if idx != -1:
                end = r.text.find('"', idx)
                sts_url = r.text[idx:] if end == -1 else r.text[idx:end]

        if not sts_url:
            print("❌ Нет STS URL")
            return False

        r = self._session.get(sts_url, headers=headers, allow_redirects=True)
        self._service_token = self._session.cookies.get("serviceToken")

        # Установим cookie на все домены
        if self._service_token:
            for domain in [".api.io.mi.com", ".io.mi.com", ".mi.com"]:
                self._session.cookies.set("serviceToken", self._service_token, domain=domain)
                self._session.cookies.set("yetAnotherServiceToken", self._service_token, domain=domain)

        # userId из cookie
        if not self.user_id:
            self.user_id = (
                self._session.cookies.get("userId", domain=".xiaomi.com")
                or self._session.cookies.get("userId", domain=".sts.api.io.mi.com")
            )

        return bool(self._service_token)

    def _ping(self) -> bool:
        """Проверяет что сессия рабочая."""
        try:
            homes = self.get_homes()
            return homes is not None
        except Exception:
            return False

    # ──────────────────────────────────────────────────────────
    # RC4-шифрованные API-вызовы
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _api_url(region: str) -> str:
        prefix = "" if region == "cn" else f"{region}."
        return f"https://{prefix}api.io.mi.com/app"

    def _signed_nonce(self, nonce: str) -> str:
        h = hashlib.sha256(base64.b64decode(self._ssecurity) + base64.b64decode(nonce))  # type: ignore[arg-type]
        return base64.b64encode(h.digest()).decode()

    @staticmethod
    def _gen_nonce() -> str:
        raw = os.urandom(8) + (int(time.time() * 1000 / 60000)).to_bytes(4, "big")
        return base64.b64encode(raw).decode()

    @staticmethod
    def _enc_sign(url: str, method: str, signed_nonce: str, params: dict) -> str:
        parts = [method.upper(), url.split("com")[1].replace("/app/", "/")]
        for k, v in params.items():
            parts.append(f"{k}={v}")
        parts.append(signed_nonce)
        sig_str = "&".join(parts)
        return base64.b64encode(hashlib.sha1(sig_str.encode()).digest()).decode()

    @staticmethod
    def _encrypt_rc4(password: str, payload: str) -> str:
        r = ARC4.new(base64.b64decode(password))
        r.encrypt(bytes(1024))
        return base64.b64encode(r.encrypt(payload.encode())).decode()

    @staticmethod
    def _decrypt_rc4(password: str, payload: str) -> bytes:
        r = ARC4.new(base64.b64decode(password))
        r.encrypt(bytes(1024))
        return r.encrypt(base64.b64decode(payload))

    def _call(self, endpoint: str, params: dict) -> Any:
        """Выполняет зашифрованный POST-запрос к Xiaomi cloud API."""
        url = self._api_url(self._region) + endpoint
        headers = {
            "Accept-Encoding": "identity",
            "User-Agent": self._agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
        }
        cookies = {
            "userId": str(self.user_id),
            "yetAnotherServiceToken": str(self._service_token),
            "serviceToken": str(self._service_token),
            "locale": "en_GB",
            "timezone": "GMT+03:00",
            "is_daylight": "1",
            "dst_offset": "3600000",
            "channel": "MI_APP_STORE",
        }
        nonce = self._gen_nonce()
        signed_nonce = self._signed_nonce(nonce)

        params["rc4_hash__"] = self._enc_sign(url, "POST", signed_nonce, params)
        for k, v in params.items():
            params[k] = self._encrypt_rc4(signed_nonce, v)
        params.update({
            "signature": self._enc_sign(url, "POST", signed_nonce, params),
            "ssecurity": self._ssecurity,
            "_nonce": nonce,
        })

        r = self._session.post(url, headers=headers, cookies=cookies, params=params)
        if r.status_code == 200:
            return json.loads(self._decrypt_rc4(self._signed_nonce(params["_nonce"]), r.text))
        print(f"  API {endpoint} HTTP {r.status_code}: {r.text[:200]}")
        return None

    # ──────────────────────────────────────────────────────────
    # Высокоуровневые методы
    # ──────────────────────────────────────────────────────────

    def get_homes(self) -> Any:
        return self._call("/v2/homeroom/gethome", {
            "data": '{"fg":true,"fetch_share":true,"fetch_share_dev":true,"limit":300,"app_ver":7}'
        })

    def get_devices(self, home_id: int | str, owner_id: int | str) -> Any:
        return self._call("/v2/home/home_device_list", {
            "data": json.dumps({
                "home_owner": int(owner_id),
                "home_id": int(home_id),
                "limit": 200,
                "get_split_device": True,
                "support_smart_home": True,
            })
        })

    def miot_action(self, did: str, siid: int, aiid: int, ins: list | None = None) -> Any:
        """Вызов MIoT action на устройстве через cloud."""
        payload = {
            "did": did,
            "siid": siid,
            "aiid": aiid,
            "in": ins or [],
        }
        return self._call("/v2/home/rpc/" + did, {
            "data": json.dumps({"method": "action", "params": payload})
        })

    def miot_get_props(self, did: str, props: list[dict]) -> Any:
        """Получить MIoT свойства устройства через cloud."""
        return self._call("/v2/home/rpc/" + did, {
            "data": json.dumps({
                "method": "get_properties",
                "params": [{"did": did, **p} for p in props],
            })
        })

    def get_all_devices(self) -> list[dict]:
        """Получить все устройства аккаунта напрямую (без привязки к домам)."""
        resp = self._call("/v2/device/getnewusrdev", {
            "data": json.dumps({"getVirtualModel": True, "getHuamiDevices": 1})
        })
        if resp and "result" in resp:
            result = resp["result"]
            return result.get("list", []) if isinstance(result, dict) else []
        return []

    def get_video_list(self, did: str, start_time: int, end_time: int) -> Any:
        """Получить список видеозаписей с камеры через cloud storage.

        ВАЖНО: siid=4 (SD card service) намеренно НЕ используется —
        aiid=1 на этом siid оказался форматированием карты, а не чтением.
        Используется только чтение через cloud storage API.
        """
        # Только cloud recording API (не MIoT SD card actions)
        resp = self._call("/v2/cloudstorage/video/list", {
            "data": json.dumps({
                "did": did,
                "begin": start_time,
                "end": end_time,
                "limit": 100,
            })
        })
        if resp and resp.get("code") == 0:
            return resp
        return None
