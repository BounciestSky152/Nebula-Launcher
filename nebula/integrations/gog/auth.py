"""GOG OAuth2 authentication (the same public client used by every open-source GOG launcher)."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import requests

from nebula import paths

log = logging.getLogger(__name__)

CLIENT_ID = "46899977096215655"
CLIENT_SECRET = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"
REDIRECT_URI = "https://embed.gog.com/on_login_success?origin=client"
TOKEN_URL = "https://auth.gog.com/token"
LOGIN_URL = (
    f"https://auth.gog.com/auth?client_id={CLIENT_ID}&redirect_uri={quote(REDIRECT_URI, safe='')}"
    "&response_type=code&layout=client2"
)
SUCCESS_PREFIX = "https://embed.gog.com/on_login_success"


class AuthError(Exception):
    pass


class GogAuth:
    def __init__(self, session: requests.Session, path: Path | None = None):
        self.session = session
        self.path = Path(path) if path else paths.config_dir() / "gog_credentials.json"
        self._lock = threading.RLock()
        self._data: dict = {}
        self._load()

    # ----------------------------------------------------------- storage
    def _load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self._data = {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(self._data, fh)
        os.replace(tmp, self.path)

    # -------------------------------------------------------------- info
    @property
    def logged_in(self) -> bool:
        return bool(self._data.get(CLIENT_ID, {}).get("refresh_token"))

    @property
    def user_id(self) -> str:
        return str(self._data.get(CLIENT_ID, {}).get("user_id", ""))

    @staticmethod
    def extract_code(text: str) -> str:
        """Accept either the bare code or the full redirect URL."""
        text = (text or "").strip()
        if text.startswith("http"):
            qs = parse_qs(urlparse(text).query)
            code = (qs.get("code") or [""])[0]
            if not code:
                raise AuthError("The URL does not contain a login code")
            return code
        return text

    # ------------------------------------------------------------ tokens
    def _token_request(self, params: dict) -> dict:
        try:
            r = self.session.get(TOKEN_URL, params=params, timeout=20)
        except requests.RequestException as exc:
            raise AuthError(f"Could not reach GOG: {exc.__class__.__name__}") from None
        if not r.ok:
            raise AuthError(f"GOG rejected the request (HTTP {r.status_code})")
        data = r.json()
        if "access_token" not in data:
            raise AuthError(data.get("error_description") or "Invalid response from GOG")
        data["loginTime"] = time.time()
        return data

    def login_with_code(self, code_or_url: str) -> dict:
        code = self.extract_code(code_or_url)
        data = self._token_request({
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        })
        with self._lock:
            self._data = {CLIENT_ID: data}
            self._save()
        return data

    def logout(self) -> None:
        with self._lock:
            self._data = {}
            try:
                self.path.unlink()
            except OSError:
                pass

    def _expired(self, creds: dict) -> bool:
        return time.time() >= creds.get("loginTime", 0) + creds.get("expires_in", 0) - 120

    def credentials(self, client_id: str = CLIENT_ID, client_secret: str = CLIENT_SECRET) -> dict:
        with self._lock:
            main = self._data.get(CLIENT_ID)
            if not main:
                raise AuthError("Not logged in to GOG")
            creds = self._data.get(client_id)
            if creds and not self._expired(creds):
                return creds
            params = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": main["refresh_token"],
            }
            if client_id != CLIENT_ID:
                params["without_new_session"] = "1"
            data = self._token_request(params)
            self._data[client_id] = data
            if client_id == CLIENT_ID:
                # GOG rotates the refresh token; keep it in sync
                self._data[CLIENT_ID] = data
            self._save()
            return data

    def token(self) -> str:
        return self.credentials()["access_token"]

    def game_credentials(self, client_id: str, client_secret: str) -> dict:
        return self.credentials(client_id, client_secret)

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token()}"}
