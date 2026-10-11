"""YouTube / YouTube Music через yt-dlp."""
from ..vk_api import Track


def _ydl_opts(config, proxy_override=None, **extra):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": True,
        "socket_timeout": 15,
        "retries": 2,
        "extractor_retries": 2,
        "fragment_retries": 2,
        # клиент android/ios: их ссылки на googlevideo не режутся по скорости
        # (web-клиент YouTube душит до ~30 КБ/с)
        "extractor_args": {"youtube": {"player_client": ["android", "ios"]}},
    }
    if getattr(config, "youtube_cookies", False):
        # сессия Google берётся из установленного Chrome — отдельный вход не нужен
        opts["cookiesfrombrowser"] = ("chrome",)
    proxy = proxy_override if proxy_override is not None else _default_proxy(config)
    if proxy:
        opts["proxy"] = proxy
    opts.update(extra)
    return opts


def _default_proxy(config) -> str:
    # ручной прокси из настроек — всегда; автоподобранный подставляется
    # только как запасной вариант в ydl_extract
    proxy = (getattr(config, "proxy", "") or "").strip()
    if proxy and "://" not in proxy:
        proxy = "http://" + proxy
    return proxy


def _auto_proxy(config) -> str:
    if (getattr(config, "proxy", "") or "").strip():
        return ""
    if not getattr(config, "auto_proxy", False):
        return ""
    return config.current_proxy_str()


def ydl_extract(url: str, config, **extra):
    """Извлечение через yt-dlp. Если включён автопрокси — сначала пробуем
    напрямую (быстро), при сбое повторяем через прокси (обход блокировки)."""
    from yt_dlp import YoutubeDL
    backup = _auto_proxy(config)
    attempts = [""] if not backup else ["", backup]
    last_exc = None
    for proxy in attempts:
        try:
            with YoutubeDL(_ydl_opts(config, proxy_override=proxy, **extra)) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:
            last_exc = e
    raise last_exc


def _to_track(entry: dict) -> Track:
    vid = entry.get("id") or ""
    url = entry.get("url") or ""
    if url and not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={url}"
    if not url and vid:
        url = f"https://music.youtube.com/watch?v={vid}"
    return Track(
        id=0, owner_id=0,
        title=entry.get("title") or "Без названия",
        artist=entry.get("uploader") or entry.get("channel") or "YouTube",
        duration=int(entry.get("duration") or 0),
        cover_url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else "",
        service="youtube",
        source_id=vid or url,
    )


def search(query: str, config, limit: int = 25) -> list:
    info = ydl_extract(f"ytsearch{limit}:{query}", config)
    return [_to_track(e) for e in (info.get("entries") or []) if e]


def resolve(track: Track, config) -> str:
    last_exc = None
    for host in ("music.youtube.com", "www.youtube.com"):
        url = f"https://{host}/watch?v={track.source_id}"
        try:
            info = ydl_extract(url, config, extract_flat=False,
                               format="bestaudio/best")
        except Exception as e:
            last_exc = e
            continue
        direct = info.get("url")
        if not direct:
            formats = info.get("formats") or []
            if formats:
                direct = formats[-1].get("url")
        if direct:
            return direct
        last_exc = RuntimeError("YouTube не отдал ссылку на поток")
    raise last_exc
