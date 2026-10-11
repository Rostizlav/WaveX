import json
from pathlib import Path
from typing import Optional

INVALID_CHARS = '<>:"/\\|?*'


def sanitize_filename(name: str) -> str:
    name = "".join("_" if (c in INVALID_CHARS or ord(c) < 32) else c for c in (name or ""))
    name = name.strip().rstrip(".")
    return name or "track"


def make_track_filename(artist: str, title: str, suffix: str = "") -> str:
    base = f"{sanitize_filename(artist)} - {sanitize_filename(title)}"[:140].rstrip(" .")
    return f"{base}{suffix}.mp3"


class CacheManager:
    """Раздельный кеш: cache_dir/tracks, cache_dir/playlists/<id>, cache_dir/podcasts."""

    def __init__(self, config) -> None:
        self.config = config
        self._ensure_dirs()
        self.index = self._load_index()

    def _root(self) -> Path:
        return Path(self.config.cache_dir)

    def _ensure_dirs(self) -> None:
        root = self._root()
        for d in (root, root / "tracks", root / "playlists", root / "podcasts"):
            d.mkdir(parents=True, exist_ok=True)

    def _dir_for(self, kind: str = "tracks", playlist_id=None) -> Path:
        root = self._root()
        if kind == "playlists":
            d = root / "playlists" / str(playlist_id if playlist_id is not None else "misc")
        elif kind == "podcasts":
            d = root / "podcasts"
        else:
            d = root / "tracks"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def cache_path(self, track, kind: str = "tracks", playlist_id=None) -> Path:
        uid_src = track.source_id or track.full_id
        if "://" in uid_src:
            uid_src = uid_src.rstrip("/").rsplit("/", 1)[-1]
        uid = sanitize_filename(f"{track.service}_{uid_src}")[:40]
        name = make_track_filename(track.artist, track.title, suffix=f" [{uid}]")
        return self._dir_for(kind, playlist_id) / name

    def _index_path(self) -> Path:
        return self._root() / "cache.json"

    def _load_index(self) -> dict:
        try:
            return json.loads(self._index_path().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_index(self) -> None:
        try:
            self._index_path().write_text(
                json.dumps(self.index, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            print(f"[cache] индекс не сохранён: {e}")

    def register(self, track, path: Path, kind: str = "tracks") -> None:
        self.index[track.full_id] = {
            "title": track.title,
            "artist": track.artist,
            "path": str(path),
            "kind": kind,
        }
        self._save_index()

    def is_cached(self, track, kind: str = "tracks", playlist_id=None) -> bool:
        p = self.cache_path(track, kind, playlist_id)
        return p.exists() and p.stat().st_size > 0

    def get_cached_path(self, track) -> Optional[Path]:
        entry = self.index.get(track.full_id)
        if entry:
            p = Path(entry.get("path", ""))
            if p.exists():
                return p
        for kind in ("tracks", "podcasts"):
            p = self.cache_path(track, kind)
            if p.exists() and p.stat().st_size > 0:
                return p
        return None

    def size(self) -> int:
        total = 0
        root = self._root()
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file() and p.name != "cache.json":
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        return total

    def clear(self) -> int:
        """Удаляет все файлы кеша. Возвращает число файлов, которые
        не удалось удалить (заняты другим процессом)."""
        failed = 0
        root = self._root()
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file() and p.name != "cache.json":
                    try:
                        p.unlink()
                    except OSError:
                        failed += 1
            # пустые папки — от самых глубоких к корню
            dirs = sorted((d for d in root.rglob("*") if d.is_dir()),
                          key=lambda d: len(d.parts), reverse=True)
            for d in dirs:
                try:
                    d.rmdir()
                except OSError:
                    pass
        self.index = {}
        self._ensure_dirs()
        self._save_index()
        return failed

    def change_root(self, new_dir) -> None:
        self.config.cache_dir = str(new_dir)
        self.config.save()
        self._ensure_dirs()
        self.index = self._load_index()
