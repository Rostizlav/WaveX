import re
from typing import Optional

import requests

_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_JUNK_WORDS = re.compile(
    r"(?i)\b(official\s*(music\s*)?(video|audio|clip)|music\s*video|lyric\s*video|"
    r"prod\.?\s*\S*|prod\s+by\s+\S+|speed\s*up|sped\s*up|slowed|reverb|"
    r"bass\s*boost(ed)?|nightcore|remix\s*by\s+\S+|hd|4k|lyrics?|клип|"
    r"official\s*lyric\s*video|visualizer)\b")


def _clean(text: str) -> str:
    """Убирает мусор из названий сторонних сервисов: скобки, значки, теги."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text or "")
    text = _JUNK_WORDS.sub(" ", text)
    # значки и декоративные символы вокруг слов (☆, ♥, ★ и т.п.)
    text = re.sub(r"[^\w\s\-\+\.&,'\"!?а-яА-ЯёЁ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -")


def _candidates(track) -> list:
    """Варианты (artist, title) для поиска текста — от точного к эвристикам."""
    artist, title = _clean(track.artist), _clean(track.title)
    out = []
    if artist and title:
        out.append((artist, title))
    if " - " in title:
        left, right = title.split(" - ", 1)
        left, right = left.strip(), right.strip()
        if left and right:
            # у YouTube/SoundCloud «artist» — часто канал, а настоящий
            # исполнитель в названии: "MiyaGi - Колибри (Music Clip)"
            out.append((left, right))
            if artist and (artist, right) not in out:
                out.append((artist, right))
    if title and not out:
        out.append(("", title))
    return out or [(track.artist or "", track.title or "")]


def _fetch(url: str, config) -> Optional[bytes]:
    for proxies in config.proxy_attempts():
        try:
            r = requests.get(url, timeout=(10, 15), proxies=proxies)
            if r.ok:
                return r.content
        except requests.exceptions.RequestException:
            continue
    return None


def get_hires_cover(track, config) -> Optional[bytes]:
    # лучший размер из VK (thumb уже выбран максимальный, вплоть до photo_1200)
    if track.cover_url:
        data = _fetch(track.cover_url, config)
        if data:
            return data
    # фолбэк — iTunes Search API, 1200x1200
    for proxies in config.proxy_attempts():
        try:
            r = requests.get(
                "https://itunes.apple.com/search",
                params={"term": f"{track.artist} {track.title}",
                        "entity": "song", "limit": 1},
                timeout=(10, 15),
                proxies=proxies,
            )
            results = r.json().get("results", [])
        except (requests.exceptions.RequestException, ValueError):
            continue
        if results:
            art = results[0].get("artworkUrl100", "")
            if art:
                data = _fetch(art.replace("100x100", "1200x1200"), config)
                if data:
                    return data
    return None


def find_cover_url(artist: str, title: str, config) -> str:
    """URL обложки 600×600 через iTunes Search API — для треков,
    у которых нет своей обложки (ВК отдаёт её только при наличии альбома)."""
    for proxies in config.proxy_attempts():
        try:
            r = requests.get(
                "https://itunes.apple.com/search",
                params={"term": f"{artist} {title}".strip(),
                        "entity": "song", "limit": 1},
                timeout=(8, 12),
                proxies=proxies,
            )
            results = r.json().get("results", [])
        except (requests.exceptions.RequestException, ValueError):
            continue
        if results:
            art = results[0].get("artworkUrl100", "")
            if art:
                return art.replace("100x100", "600x600")
    return ""


# ---- тексты песен ----

def _lrclib(artist: str, title: str, duration: int, config) -> Optional[str]:
    # точный запрос
    try:
        r = requests.get(
            "https://lrclib.net/api/get",
            params={"artist_name": artist, "track_name": title,
                    "duration": duration},
            timeout=10,
        )
        if r.ok:
            data = r.json()
            text = data.get("plainLyrics") or data.get("syncedLyrics")
            if text:
                return text.strip()
    except (requests.exceptions.RequestException, ValueError):
        pass
    # нечёткий поиск
    try:
        r = requests.get(
            "https://lrclib.net/api/search",
            params={"q": f"{artist} {title}".strip()},
            timeout=10,
        )
        if not r.ok:
            return None
        items = r.json()
    except (requests.exceptions.RequestException, ValueError):
        return None
    if not isinstance(items, list):
        return None

    def score(item) -> float:
        s = 0.0
        if item.get("plainLyrics") or item.get("syncedLyrics"):
            s += 100.0
        if duration and item.get("duration"):
            s -= min(abs(float(item["duration"]) - duration), 60.0)
        want = (artist + " " + title).lower()
        got = (str(item.get("artistName", "")) + " "
               + str(item.get("trackName", ""))).lower()
        s += 20.0 * len(set(want.split()) & set(got.split())) / max(
            1, len(set(want.split())))
        return s

    items.sort(key=score, reverse=True)
    for item in items[:3]:
        text = item.get("plainLyrics") or item.get("syncedLyrics")
        if text and score(item) > 60.0:
            return text.strip()
    return None


def _extract_div(page: str, start: int) -> tuple:
    """Возвращает (inner_html, end_index) div-а с балансировкой вложенности —
    regex до первого </div> не подходит: внутри есть вложенные div."""
    open_end = page.find(">", start)
    if open_end < 0:
        return "", len(page)
    depth = 1
    i = open_end + 1
    while depth > 0:
        m = re.search(r"<div\b|</div>", page[i:])
        if not m:
            break
        pos = i + m.start()
        if m.group(0) == "<div":
            depth += 1
            i = pos + 4
        else:
            depth -= 1
            if depth == 0:
                return page[open_end + 1:pos], pos + 6
            i = pos + 6
    return page[open_end + 1:], len(page)


_JUNK_LINE = re.compile(
    r"(?i)^(\[\s*текст песни.*\]|\d[\d\s,.]*\s*contributors?|translations?|"
    r"read more|embed|you might also like|more on genius|about genius|"
    r"see .+ live|get tickets.*|.+ \(.*\) lyrics)$")


def _genius_parse(page: str) -> str:
    import html as html_mod

    parts = []
    for m in re.finditer(r'<div[^>]*data-lyrics-container="true"[^>]*>', page):
        inner, _ = _extract_div(page, m.start())
        # служебная шапка контейнера (Contributors/Translations) —
        # вырезаем целиком, тоже с балансировкой
        hm = re.search(r'<div[^>]*data-exclude-from-selection="true"[^>]*>', inner)
        if hm:
            _, hend = _extract_div(inner, hm.start())
            inner = inner[:hm.start()] + inner[hend:]
        # рекламный/инфо-футер внутри контейнера
        fm = re.search(r'<div[^>]*class="[^"]*LyricsFooter', inner)
        if fm:
            _, fend = _extract_div(inner, fm.start())
            inner = inner[:fm.start()] + inner[fend:]
        inner = re.sub(r"<br\s*/?>", "\n", inner)
        inner = re.sub(r"<[^>]+>", "", inner)
        parts.append(html_mod.unescape(inner))
    lines = []
    for part in parts:
        for ln in part.splitlines():
            ln = ln.strip()
            if ln and not _JUNK_LINE.match(ln):
                lines.append(ln)
    return "\n".join(lines).strip()


def _genius_lyrics(artist: str, title: str, config) -> Optional[str]:
    """Текст с Genius: поиск через веб-API + разбор страницы песни."""
    headers = {"User-Agent": _BROWSER_UA}
    for proxies in config.proxy_attempts():
        try:
            r = requests.get(
                "https://genius.com/api/search/multi",
                params={"q": f"{artist} {title}".strip(), "per_page": 5},
                headers=headers, timeout=(10, 15), proxies=proxies,
            )
            if not r.ok:
                continue
            sections = r.json().get("response", {}).get("sections", [])
        except (requests.exceptions.RequestException, ValueError):
            continue
        song_url = None
        for sec in sections:
            if sec.get("type") == "song":
                for hit in sec.get("hits", []):
                    song_url = (hit.get("result") or {}).get("url")
                    if song_url:
                        break
            if song_url:
                break
        if not song_url:
            continue
        try:
            page = requests.get(song_url, headers=headers, timeout=(10, 15),
                                proxies=proxies)
        except requests.exceptions.RequestException:
            continue
        if not page.ok:
            continue
        text = _genius_parse(page.text)
        if len(text) > 100:  # отсекаем мусор без собственно текста
            return text
    return None


def _lyrics_ovh(artist: str, title: str, config) -> Optional[str]:
    """Текст с lyrics.ovh (бесплатный API без ключа)."""
    from urllib.parse import quote
    if not artist:
        return None
    url = f"https://api.lyrics.ovh/v1/{quote(artist)}/{quote(title)}"
    for proxies in config.proxy_attempts():
        try:
            r = requests.get(url, headers={"User-Agent": _BROWSER_UA},
                             timeout=(10, 15), proxies=proxies)
            if not r.ok:
                continue
            text = (r.json().get("lyrics") or "").strip()
            if text:
                return text
        except (requests.exceptions.RequestException, ValueError):
            continue
    return None


def get_lyrics(track, config, api=None) -> Optional[str]:
    if track.lyrics_id and api is not None:
        try:
            text = api.get_lyrics(track.lyrics_id)
            if text:
                return text
        except Exception:
            pass
    duration = int(track.duration or 0)
    variants = _candidates(track)[:2]
    # основной источник — Genius, затем lyrics.ovh, затем LRCLIB
    for artist, title in variants:
        text = _genius_lyrics(artist, title, config)
        if text:
            return text
    for artist, title in variants:
        text = _lyrics_ovh(artist, title, config)
        if text:
            return text
    for artist, title in variants:
        text = _lrclib(artist, title, duration, config)
        if text:
            return text
    return None
