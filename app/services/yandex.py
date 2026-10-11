"""Яндекс Музыка (неофициальный API, нужен OAuth-токен пользователя).

Как получить токен: авторизуйтесь на music.yandex.ru, затем откройте
https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d
и скопируйте токен со страницы.
"""
import hashlib
import xml.etree.ElementTree as ET

import requests

from ..vk_api import Track

BASE = "https://api.music.yandex.net"
UA = "Yandex-Music-API"
SIGN_SALT = "XGRlBW9FXlekgbPrRHuSiA"


class YandexAuthError(Exception):
    pass


def _token(config) -> str:
    token = (getattr(config, "yandex_token", "") or "").strip()
    if not token:
        raise YandexAuthError(
            "Не задан токен Яндекс Музыки (Настройки → Сервисы)")
    return token


def _get(config, path: str, **params):
    last_exc = None
    for proxies in config.proxy_attempts():
        try:
            r = requests.get(
                BASE + path,
                params=params,
                headers={"Authorization": f"OAuth {_token(config)}", "User-Agent": UA},
                timeout=(10, 20),
                proxies=proxies,
            )
            if r.status_code in (401, 403):
                raise YandexAuthError("Токен Яндекс Музыки отклонён (401/403)")
            r.raise_for_status()
            return r.json().get("result", {})
        except YandexAuthError:
            raise
        except requests.exceptions.RequestException as e:
            last_exc = e
    raise last_exc


def _to_track(item: dict) -> Track:
    artists = ", ".join(a.get("name", "") for a in item.get("artists") or []) or "Яндекс Музыка"
    cover = ""
    albums = item.get("albums") or []
    if albums and albums[0].get("coverUri"):
        cover = "https://" + albums[0]["coverUri"].replace("%%", "200x200")
    return Track(
        id=0, owner_id=0,
        title=item.get("title") or "Без названия",
        artist=artists,
        duration=int(item.get("durationMs") or 0) // 1000,
        cover_url=cover,
        album=albums[0].get("title", "") if albums else "",
        service="yandex",
        source_id=str(item.get("id") or ""),
    )


def search(query: str, config, limit: int = 25) -> list:
    result = _get(config, "/search", text=query, type="track", page=0, nocorrect="false")
    tracks = (result.get("tracks") or {}).get("results") or []
    return [_to_track(t) for t in tracks[:limit]]


def resolve(track: Track, config) -> str:
    infos = _get(config, f"/tracks/{track.source_id}/download-info")
    mp3s = [i for i in infos if i.get("codec") == "mp3"] or list(infos)
    if not mp3s:
        raise RuntimeError("Яндекс не отдал варианты скачивания")
    best = max(mp3s, key=lambda i: i.get("bitrateInKbps", 0))
    last_exc = None
    for proxies in config.proxy_attempts():
        try:
            r = requests.get(best["src"], headers={"Authorization": f"OAuth {_token(config)}"},
                             timeout=(10, 20), proxies=proxies)
            r.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            last_exc = e
    else:
        raise last_exc
    root = ET.fromstring(r.text)
    host = root.findtext("host")
    path = root.findtext("path")
    ts = root.findtext("ts")
    s = root.findtext("s")
    sign = hashlib.md5((SIGN_SALT + path[1:] + s).encode()).hexdigest()
    return f"https://{host}/get-mp3/{sign}/{ts}{path}"
