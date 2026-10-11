import json
import os
from pathlib import Path
from typing import Optional


def _app_data_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        new_dir = Path(appdata) / "WaveX"
        old_dir = Path(appdata) / "VKX"
        if old_dir.is_dir() and not new_dir.exists():
            try:
                old_dir.rename(new_dir)
            except OSError:
                pass
        return new_dir
    return Path(__file__).resolve().parent.parent / ".wavex_data"


class Config:
    FIELDS = (
        "token", "user_id", "token_type", "theme", "proxy", "cache_dir",
        "download_dir", "hires_covers", "volume", "local_music_dir",
        "yandex_token", "spotify_client_id", "spotify_client_secret",
        "youtube_cookies", "oauth_client_id", "auto_proxy",
        "eq_enabled", "eq_gains", "eq_tempo", "eq_pitch", "minimize_to_tray",
    )

    def __init__(self) -> None:
        self.config_dir = _app_data_dir()
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.config_dir = Path(__file__).resolve().parent.parent / ".wavex_data"
            self.config_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.config_dir / "config.json"

        home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
        self.token: str = ""
        self.user_id: int = 0
        self.token_type: str = "kate"
        self.theme: str = "dark"
        self.proxy: str = ""
        self.cache_dir: str = str(self.config_dir / "cache")
        self.download_dir: str = str(home / "Music" / "WaveX")
        self.hires_covers: bool = True
        self.volume: float = 0.8
        self.local_music_dir: str = ""
        self.yandex_token: str = ""
        self.spotify_client_id: str = ""
        self.spotify_client_secret: str = ""
        self.youtube_cookies: bool = False
        self.oauth_client_id: int = 6463690
        self.auto_proxy: bool = False
        self.eq_enabled: bool = False
        self.eq_gains: list = [0.0] * 5
        self.eq_tempo: float = 1.0   # скорость воспроизведения (0.5–2.0)
        self.eq_pitch: float = 0.0   # тон в полутонах (−12…+12)
        self.minimize_to_tray: bool = True
        self.load()

        from .proxy_manager import ProxyManager
        self.proxy_manager = ProxyManager(self.config_dir)

    def load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        for key in self.FIELDS:
            if key in data:
                setattr(self, key, data[key])
        if self.theme not in ("dark", "light"):
            self.theme = "dark"  # тема «стекло» удалена

    def save(self) -> None:
        data = {key: getattr(self, key) for key in self.FIELDS}
        try:
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            print(f"[config] не удалось сохранить: {e}")

    def current_proxy_str(self) -> str:
        """Активный прокси: ручной имеет приоритет над автоподбором."""
        proxy = (self.proxy or "").strip()
        if not proxy and self.auto_proxy:
            proxy = self.proxy_manager.best_proxy() or ""
        if proxy and "://" not in proxy:
            proxy = "http://" + proxy
        return proxy

    def get_proxies(self) -> Optional[dict]:
        proxy = self.current_proxy_str()
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}

    def proxy_attempts(self) -> list:
        """Порядок сетевых попыток для сторонних сервисов.
        Ручной прокси — только он (явный выбор пользователя).
        При автопрокси — сначала напрямую (быстро), при сбое — через прокси."""
        if (self.proxy or "").strip():
            return [self.get_proxies()]
        auto = self.get_proxies() if self.auto_proxy else None
        return [None, auto] if auto else [None]
