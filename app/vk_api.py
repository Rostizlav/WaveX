from dataclasses import dataclass
import time
from typing import Optional

import requests

API_URL = "https://api.vk.com/method/"
API_VERSION = "5.131"
# UA официального Android-клиента Kate Mobile — без него audio.* методы не отдают ссылки.
KATE_USER_AGENT = "KateMobileAndroid/99 lite-460 (Android 12; SDK 31; arm64-v8a; samsung SM-S911B; ru)"
# Официальный клиент ВК: audio.* работает с его токеном даже после блокировки Kate.
OFFICIAL_USER_AGENT = "VKAndroidApp/8.74-12300 (Android 13; SDK 33; arm64-v8a; Google Pixel 7; ru; 2400x1080)"
OFFICIAL_CLIENT_ID = 2274003
OFFICIAL_CLIENT_SECRET = "hHbZxrka2uZ6jB1inYsH"

USER_AGENTS = {"kate": KATE_USER_AGENT, "official": OFFICIAL_USER_AGENT}


class VKApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class AuthError(VKApiError):
    pass


class CaptchaError(VKApiError):
    def __init__(self, captcha_sid: str, captcha_img: str) -> None:
        super().__init__(14, "Требуется ввод капчи")
        self.captcha_sid = captcha_sid
        self.captcha_img = captcha_img


def _report_proxy(config, proxies, ok: bool, elapsed: float) -> None:
    """Статистика для автоподобранных прокси (ручной прокси не трогаем)."""
    if not proxies \
            or (getattr(config, "proxy", "") or "").strip() \
            or not getattr(config, "auto_proxy", False):
        return
    pm = getattr(config, "proxy_manager", None)
    if pm is None:
        return
    if ok:
        pm.report_success(proxies["https"], elapsed)
    else:
        pm.report_failure(proxies["https"])


class Need2FA(Exception):
    def __init__(self, validation_type: str, phone_mask: str) -> None:
        self.validation_type = validation_type
        self.phone_mask = phone_mask
        super().__init__(f"Требуется код подтверждения ({phone_mask})")


def official_login(login: str, password: str, config, code: Optional[str] = None) -> dict:
    """Вход по логину и паролю через flow официального Android-клиента ВК.
    Возвращает {"access_token": ..., "user_id": ...}."""
    data = {
        "grant_type": "password",
        "client_id": OFFICIAL_CLIENT_ID,
        "client_secret": OFFICIAL_CLIENT_SECRET,
        "username": login,
        "password": password,
        "scope": "audio,offline",
        "2fa_supported": 1,
        "v": API_VERSION,
    }
    if code:
        data["code"] = code
    payload = None
    last_exc: Optional[Exception] = None
    for _ in range(2):
        proxies = config.get_proxies()
        try:
            r = requests.post(
                "https://oauth.vk.com/token",
                data=data,
                headers={"User-Agent": OFFICIAL_USER_AGENT},
                timeout=30,
                proxies=proxies,
            )
            payload = r.json()
            break
        except (requests.exceptions.RequestException, ValueError) as e:
            _report_proxy(config, proxies, False, 0.0)
            last_exc = e
    if payload is None:
        raise VKApiError(-1, f"Сетевая ошибка: {last_exc}")
    if "access_token" in payload:
        return {
            "access_token": payload["access_token"],
            "user_id": int(payload.get("user_id") or 0),
        }
    err = payload.get("error", "")
    desc = payload.get("error_description") or err
    if err == "need_validation":
        raise Need2FA(payload.get("validation_type", ""), payload.get("phone_mask", ""))
    if err == "need_captcha":
        raise CaptchaError(payload.get("captcha_sid", ""), payload.get("captcha_img", ""))
    low = str(desc).lower()
    if "flood" in low or "bruteforce" in str(payload.get("error_type", "")).lower():
        desc = ("ВК временно ограничил входы с вашего IP (flood control). "
                "Подождите 30–60 минут или смените сеть (например, раздайте "
                "мобильный интернет с телефона) и попробуйте снова.")
    elif "password" in low or "username" in low or "invalid" in low:
        desc = "Неверный логин или пароль"
    raise VKApiError(-2, str(desc))


@dataclass
class Track:
    id: int
    owner_id: int
    title: str
    artist: str
    duration: int = 0
    url: str = ""
    cover_url: str = ""
    access_key: str = ""
    lyrics_id: int = 0
    album: str = ""
    is_podcast: bool = False
    local_path: str = ""
    service: str = "vk"        # vk | youtube | soundcloud | yandex | spotify
    source_id: str = ""        # id/URL трека в стороннем сервисе

    @property
    def full_id(self) -> str:
        if self.service != "vk":
            return f"{self.service}:{self.source_id}"
        return f"{self.owner_id}_{self.id}"

    @property
    def is_local(self) -> bool:
        return bool(self.local_path)


def track_from_api(item: dict) -> Track:
    cover = ""
    album = item.get("album") or {}
    thumb = album.get("thumb") or {}
    for size in ("photo_1200", "photo_600", "photo_300", "photo_270", "photo_135", "photo_68"):
        if thumb.get(size):
            cover = thumb[size]
            break
    if not cover:
        sizes = thumb.get("sizes") or []
        if sizes:
            best = max(sizes, key=lambda s: s.get("width", 0))
            cover = best.get("src", "")
    return Track(
        id=item.get("id", 0),
        owner_id=item.get("owner_id", 0),
        title=item.get("title") or "Без названия",
        artist=item.get("artist") or "Неизвестный исполнитель",
        duration=int(item.get("duration") or 0),
        url=item.get("url") or "",
        cover_url=cover,
        access_key=item.get("access_key") or "",
        lyrics_id=int(item.get("lyrics_id") or 0),
        album=album.get("title") or "",
        is_podcast=bool(item.get("podcast_info")),
    )


class VKApi:
    def __init__(self, token: str, config) -> None:
        self.token = token
        self.config = config
        self.session = requests.Session()
        ua = USER_AGENTS.get(getattr(config, "token_type", "kate"), KATE_USER_AGENT)
        self.session.headers.update({"User-Agent": ua})

    def call(self, method: str, **params):
        data = {"access_token": self.token, "v": API_VERSION}
        data.update(params)
        payload = None
        last_exc: Optional[Exception] = None
        for _ in range(2):
            proxies = self.config.get_proxies()
            start = time.monotonic()
            try:
                r = self.session.post(
                    API_URL + method,
                    data=data,
                    timeout=30,
                    proxies=proxies,
                )
                payload = r.json()
            except (requests.exceptions.RequestException, ValueError) as e:
                # вторая попытка уйдёт уже через другой прокси —
                # менеджер понизил упавший в рейтинге
                _report_proxy(self.config, proxies, False, 0.0)
                last_exc = e
                continue
            _report_proxy(self.config, proxies, True, time.monotonic() - start)
            break
        if payload is None:
            raise VKApiError(-1, f"Сетевая ошибка: {last_exc}")
        if "error" in payload:
            err = payload["error"]
            code = int(err.get("error_code", -1))
            msg = err.get("error_msg", "Неизвестная ошибка")
            if code == 5:
                raise AuthError(code, msg)
            if code == 14:
                raise CaptchaError(err.get("captcha_sid", ""), err.get("captcha_img", ""))
            raise VKApiError(code, msg)
        return payload.get("response")

    # ---- audio.* ----

    @staticmethod
    def _tracks_response(resp) -> tuple:
        if isinstance(resp, dict):
            items = resp.get("items", [])
            total = int(resp.get("count", len(items)))
        else:
            items = resp or []
            total = len(items)
        return [track_from_api(i) for i in items], total

    def get_audios(self, owner_id: Optional[int] = None, offset: int = 0, count: int = 100) -> tuple:
        params: dict = {"offset": offset, "count": count}
        if owner_id is not None:
            params["owner_id"] = owner_id
        return self._tracks_response(self.call("audio.get", **params))

    def search(self, q: str, offset: int = 0, count: int = 100) -> tuple:
        resp = self.call("audio.search", q=q, auto_complete=1, sort=2, offset=offset, count=count)
        return self._tracks_response(resp)

    def get_popular(self, offset: int = 0, count: int = 100) -> tuple:
        resp = self.call("audio.getPopular", only_eng=0, offset=offset, count=count)
        return self._tracks_response(resp)

    def get_recommendations(self, offset: int = 0, count: int = 100) -> tuple:
        resp = self.call("audio.getRecommendations", offset=offset, count=count)
        return self._tracks_response(resp)

    def get_playlists(self, owner_id: int, offset: int = 0, count: int = 50) -> tuple:
        resp = self.call("audio.getPlaylists", owner_id=owner_id, offset=offset, count=count)
        items = resp.get("items", []) if isinstance(resp, dict) else (resp or [])
        total = int(resp.get("count", len(items))) if isinstance(resp, dict) else len(items)
        return items, total

    def get_playlist_by_id(self, owner_id: int, playlist_id: int, access_key: str = "") -> dict:
        params: dict = {"owner_id": owner_id, "playlist_id": playlist_id}
        if access_key:
            params["access_key"] = access_key
        return self.call("audio.getPlaylistById", **params) or {}

    def get_playlist_tracks(self, owner_id: int, playlist_id: int, access_key: str = "",
                            offset: int = 0, count: int = 1000) -> tuple:
        params: dict = {"owner_id": owner_id, "album_id": playlist_id, "offset": offset, "count": count}
        if access_key:
            params["access_key"] = access_key
        return self._tracks_response(self.call("audio.get", **params))

    def get_catalog(self) -> dict:
        return self.call("audio.getCatalog") or {}

    def get_search_hints(self, q: str) -> list:
        resp = self.call("audio.getSearchHints", q=q, limit=10)
        if isinstance(resp, dict):
            resp = resp.get("items") or resp.get("hints") or []
        hints = []
        for h in resp or []:
            if isinstance(h, str):
                hints.append(h)
            elif isinstance(h, dict):
                hints.append(h.get("text") or h.get("q") or "")
        return [h for h in hints if h]

    def get_lyrics(self, lyrics_id: int) -> str:
        resp = self.call("audio.getLyrics", lyrics_id=lyrics_id)
        if isinstance(resp, dict):
            return resp.get("text") or ""
        return ""

    # ---- users.get (единственный не-audio метод: имя и аватар, без «онлайна») ----

    def users_get(self) -> dict:
        resp = self.call("users.get", fields="photo_100,photo_200")
        if isinstance(resp, list) and resp:
            return resp[0]
        return {}
