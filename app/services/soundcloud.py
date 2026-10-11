"""SoundCloud через yt-dlp."""
from ..vk_api import Track
from .youtube import ydl_extract


def _to_track(entry: dict) -> Track:
    page_url = entry.get("url") or entry.get("webpage_url") or ""
    thumb = ""
    thumbs = entry.get("thumbnails") or []
    if thumbs:
        thumb = thumbs[-1].get("url", "")
    return Track(
        id=0, owner_id=0,
        title=entry.get("title") or "Без названия",
        artist=entry.get("uploader") or "SoundCloud",
        duration=int(entry.get("duration") or 0),
        cover_url=thumb,
        service="soundcloud",
        source_id=page_url,
    )


def search(query: str, config, limit: int = 25) -> list:
    info = ydl_extract(f"scsearch{limit}:{query}", config)
    return [_to_track(e) for e in (info.get("entries") or []) if e]


def resolve(track: Track, config) -> str:
    info = ydl_extract(track.source_id, config, extract_flat=False,
                       format="bestaudio/best")
    direct = info.get("url")
    if not direct:
        formats = info.get("formats") or []
        if formats:
            direct = formats[-1].get("url")
    if not direct:
        raise RuntimeError("SoundCloud не отдал ссылку на поток")
    return direct
