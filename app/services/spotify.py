"""Spotify: поиск через официальный Web API (Client Credentials),
воспроизведение — 30-секундное превью, либо подбор того же трека на YouTube.

Полные треки Spotify отдаёт только с Premium через официальный SDK —
стороннему приложению они недоступны, поэтому без превью играем
соответствие с YouTube.
"""
import base64
import time

import requests

from ..vk_api import Track

_token_cache = {"value": "", "expires": 0.0}


class SpotifyAuthError(Exception):
    pass


def _creds(config):
    cid = (getattr(config, "spotify_client_id", "") or "").strip()
    secret = (getattr(config, "spotify_client_secret", "") or "").strip()
    if not cid or not secret:
        raise SpotifyAuthError(
            "Не заданы Client ID/Secret Spotify (Настройки → Сервисы; "
            "создайте приложение на developer.spotify.com)")
    return cid, secret


def _token(config) -> str:
    if _token_cache["value"] and time.time() < _token_cache["expires"] - 60:
        return _token_cache["value"]
    cid, secret = _creds(config)
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    last_exc = None
    for proxies in config.proxy_attempts():
        try:
            r = requests.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {auth}"},
                timeout=(10, 15),
                proxies=proxies,
            )
            break
        except requests.exceptions.RequestException as e:
            last_exc = e
    else:
        raise last_exc
    if r.status_code != 200:
        raise SpotifyAuthError(f"Spotify auth: HTTP {r.status_code}")
    data = r.json()
    _token_cache["value"] = data["access_token"]
    _token_cache["expires"] = time.time() + int(data.get("expires_in", 3600))
    return _token_cache["value"]


def _to_track(item: dict) -> Track:
    artists = ", ".join(a.get("name", "") for a in item.get("artists") or []) or "Spotify"
    images = (item.get("album") or {}).get("images") or []
    return Track(
        id=0, owner_id=0,
        title=item.get("name") or "Без названия",
        artist=artists,
        duration=int(item.get("duration_ms") or 0) // 1000,
        url=item.get("preview_url") or "",
        cover_url=images[0]["url"] if images else "",
        album=(item.get("album") or {}).get("name", ""),
        service="spotify",
        source_id=item.get("id") or "",
    )


def search(query: str, config, limit: int = 25) -> list:
    last_exc = None
    for proxies in config.proxy_attempts():
        try:
            r = requests.get(
                "https://api.spotify.com/v1/search",
                params={"q": query, "type": "track", "limit": limit},
                headers={"Authorization": f"Bearer {_token(config)}"},
                timeout=(10, 20),
                proxies=proxies,
            )
            r.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            last_exc = e
    else:
        raise last_exc
    items = (r.json().get("tracks") or {}).get("items") or []
    return [_to_track(i) for i in items]


def resolve(track: Track, config) -> str:
    if track.url:
        return track.url
    # полной версии нет — подбираем соответствие на YouTube
    from . import youtube
    query = f"{track.artist} {track.title} audio"
    info = youtube.ydl_extract(f"ytsearch1:{query}", config)
    entries = info.get("entries") or []
    if not entries:
        raise RuntimeError("Трек не найден на YouTube")
    yt_track = youtube._to_track(entries[0])
    return youtube.resolve(yt_track, config)
