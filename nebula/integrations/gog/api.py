"""Thin client for the GOG web/Galaxy HTTP APIs."""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
import zlib
from typing import Any, Iterable

import requests

from nebula.integrations.gog.auth import GogAuth

log = logging.getLogger(__name__)

EMBED = "https://embed.gog.com"
API = "https://api.gog.com"
CONTENT = "https://content-system.gog.com"
CDN = "https://gog-cdn-fastly.gog.com"
GAMESDB = "https://gamesdb.gog.com"
GAMEPLAY = "https://gameplay.gog.com"
CATALOG = "https://catalog.gog.com/v1/catalog"
REMOTE_CONFIG = "https://remote-config.gog.com/components/galaxy_client/clients/{client_id}?component_version=2.0.45"
CLOUD = "https://cloudstorage.gog.com"
PRESENCE = "https://presence.gog.com"
USERS = "https://users.gog.com"
CHAT = "https://chat.gog.com"

# goodies packs that show up as "games" in libraries (same list as minigalaxy)
IGNORE_IDS = {
    "1424856371",  # Hotline Miami 2: Wrong Number - Digital Comics
    "1980301910",  # The Witcher Goodies Collection
    "2005648906",  # Spring Sale Goodies Collection #1
    "1486144755",  # Cyberpunk 2077 Goodies Collection
    "1581684020",  # A Plague Tale Digital Goodies Pack
    "1185685769",  # CDPR Goodie Pack Content
}


class ApiError(Exception):
    pass


def https(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return url


def galaxy_path(h: str) -> str:
    return h if "/" in h else f"{h[0:2]}/{h[2:4]}/{h}"


def gamesdb_image(obj: Any) -> str:
    if isinstance(obj, dict) and obj.get("url_format"):
        return obj["url_format"].replace("{formatter}", "").replace("{ext}", "jpg")
    return ""


def localized(obj: Any, lang: str = "en-US") -> str:
    if isinstance(obj, dict):
        return obj.get(lang) or obj.get("*") or next(iter(obj.values()), "")
    return obj or ""


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"</p>|</li>|</h\d>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class GogApi:
    def __init__(self, auth: GogAuth, session: requests.Session):
        self.auth = auth
        self.session = session

    # ---------------------------------------------------------- helpers
    def _get(self, url: str, auth: bool = True, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {}) or {}
        if auth and self.auth.logged_in:
            headers.update(self.auth.headers())
        r = self.session.get(url, headers=headers, **kwargs)
        return r

    def json(self, url: str, auth: bool = True, **kwargs) -> Any:
        r = self._get(url, auth=auth, **kwargs)
        if not r.ok:
            raise ApiError(f"HTTP {r.status_code} for {url.split('?')[0]}")
        return r.json()

    def zjson(self, url: str, auth: bool = True) -> tuple[Any, dict]:
        """GET a (possibly zlib compressed) JSON document."""
        r = self._get(url, auth=auth)
        if not r.ok:
            raise ApiError(f"HTTP {r.status_code} for {url.split('?')[0]}")
        try:
            return json.loads(zlib.decompress(r.content, 15)), dict(r.headers)
        except zlib.error:
            return r.json(), dict(r.headers)

    # ------------------------------------------------------------- user
    def user_data(self) -> dict:
        return self.json(f"{EMBED}/userData.json")

    def user_profile(self, user_id: str) -> dict:
        return self.json(f"{USERS}/users/{user_id}")

    def owned_ids(self) -> set[str]:
        data = self.json(f"{EMBED}/user/data/games")
        return {str(i) for i in data.get("owned", [])}

    def library_page(self, page: int) -> dict:
        return self.json(f"{EMBED}/account/getFilteredProducts", params={"mediaType": 1, "page": page,
                                                                        "sortBy": "title"})

    def library(self) -> list[dict]:
        first = self.library_page(1)
        products = list(first.get("products", []))
        for page in range(2, int(first.get("totalPages", 1)) + 1):
            products += self.library_page(page).get("products", [])
        return [p for p in products if str(p.get("id")) not in IGNORE_IDS]

    # --------------------------------------------------------- products
    def product(self, product_id: str, expand: Iterable[str] = ("downloads", "expanded_dlcs"),
                locale: str = "en-US") -> dict:
        return self.json(f"{API}/products/{product_id}",
                         params={"expand": ",".join(expand), "locale": locale}, auth=True)

    def products(self, ids: list[str], expand: Iterable[str] = ()) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            params = {"ids": ",".join(batch)}
            if expand:
                params["expand"] = ",".join(expand)
            try:
                out += self.json(f"{API}/products", params=params, auth=False)
            except ApiError as exc:
                log.debug("products batch failed: %s", exc)
        return out

    def gamesdb(self, product_id: str, platform: str = "gog") -> dict:
        return self.json(f"{GAMESDB}/platforms/{platform}/external_releases/{product_id}", auth=False)

    def prices(self, product_id: str, country: str = "US") -> dict:
        return self.json(f"{API}/products/{product_id}/prices", params={"countryCode": country}, auth=False)

    # ----------------------------------------------------------- content
    def builds(self, product_id: str, os_name: str, generation: int = 2) -> list[dict]:
        try:
            data = self.json(f"{CONTENT}/products/{product_id}/os/{os_name}/builds",
                             params={"generation": generation})
        except ApiError:
            return []
        return data.get("items", []) or []

    def secure_link(self, product_id: str, path: str = "/", root: str | None = None) -> list[dict]:
        params = {"_version": 2, "generation": 2, "path": path}
        if root:
            params["root"] = root
        data = self.json(f"{CONTENT}/products/{product_id}/secure_link", params=params)
        return data.get("urls", [])

    def dependency_link(self) -> list[dict]:
        data = self.json(f"{CONTENT}/open_link", params={"generation": 2, "_version": 2,
                                                        "path": "/dependencies/store/"})
        return data.get("urls", [])

    def dependencies_repository(self) -> dict:
        info = self.json(f"{CONTENT}/dependencies/repository", params={"generation": 2}, auth=False)
        repo, _ = self.zjson(info["repository_manifest"], auth=False)
        repo["build_id"] = info.get("build_id")
        return repo

    def downlink(self, url: str) -> dict:
        return self.json(url)

    def checksum_info(self, url: str) -> dict:
        if not url:
            return {}
        try:
            r = self.session.get(url, timeout=20)
            if r.ok and r.text.strip():
                return dict(ET.fromstring(r.text).attrib)
        except (requests.RequestException, ET.ParseError):
            pass
        return {}

    def remote_config(self, client_id: str) -> dict:
        return self.json(REMOTE_CONFIG.format(client_id=client_id), auth=False)

    # ---------------------------------------------------------- gameplay
    def achievements(self, product_id: str, token: str | None = None, lang: str = "en-US") -> list[dict]:
        headers = {"X-Gog-Lc": lang}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            r = self.session.get(f"{GAMEPLAY}/clients/{product_id}/users/{self.auth.user_id}/achievements",
                                 headers=headers)
        else:
            r = self._get(f"{GAMEPLAY}/clients/{product_id}/users/{self.auth.user_id}/achievements", headers=headers)
        if not r.ok:
            raise ApiError(f"achievements HTTP {r.status_code}")
        return r.json().get("items", []) or []

    def playtime(self, product_id: str) -> int | None:
        r = self._get(f"{GAMEPLAY}/games/{product_id}/users/{self.auth.user_id}/sessions")
        if not r.ok:
            return None
        try:
            return int(r.json().get("time_sum") or 0)
        except (ValueError, TypeError):
            return None

    def post_session(self, product_id: str, session_date: int, minutes: int) -> bool:
        r = self.session.post(f"{GAMEPLAY}/games/{product_id}/users/{self.auth.user_id}/sessions",
                              json={"session_date": int(session_date), "time": int(minutes)},
                              headers=self.auth.headers())
        return r.status_code in (200, 201, 204)

    def set_presence(self, game_id: str | None, version: str) -> bool:
        payload = {"application_type": "Nebula", "force_update": False, "presence": "online", "version": version}
        if game_id:
            payload["game_id"] = game_id
        r = self.session.post(f"{PRESENCE}/users/{self.auth.user_id}/status", json=payload,
                              headers=self.auth.headers())
        return r.status_code in (200, 204)

    def delete_presence(self) -> None:
        try:
            self.session.delete(f"{PRESENCE}/users/{self.auth.user_id}/status", headers=self.auth.headers(),
                                timeout=5)
        except requests.RequestException:
            pass

    def friends(self) -> list[dict]:
        data = self.json(f"{CHAT}/users/{self.auth.user_id}/friends")
        if isinstance(data, dict):
            return data.get("items", []) or []
        return data or []

    # ------------------------------------------------------------- store
    def catalog(self, query: str = "", page: int = 1, order: str = "desc:trending", limit: int = 48,
                linux_only: bool = False, discounted: bool = False, country: str = "US",
                currency: str = "USD", product_ids: list[str] | None = None) -> dict:
        params: dict[str, Any] = {
            "limit": limit,
            "order": order,
            "productType": "in:game,pack",
            "page": page,
            "countryCode": country,
            "locale": "en-US",
            "currencyCode": currency,
        }
        if query:
            params["query"] = f"like:{query}"
        if linux_only:
            params["systems"] = "in:linux"
        if discounted:
            params["discounted"] = "eq:true"
        if product_ids:
            params["productId"] = "in:" + ",".join(product_ids)
        return self.json(CATALOG, params=params, auth=False)

    def wishlist(self) -> set[str]:
        data = self.json(f"{EMBED}/user/wishlist.json")
        wl = data.get("wishlist", {})
        if isinstance(wl, dict):
            return {str(k) for k, v in wl.items() if v}
        return {str(k) for k in wl}

    def wishlist_add(self, product_id: str) -> None:
        self.json(f"{EMBED}/user/wishlist/add/{product_id}")

    def wishlist_remove(self, product_id: str) -> None:
        self.json(f"{EMBED}/user/wishlist/remove/{product_id}")
