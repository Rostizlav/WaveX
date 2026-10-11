import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin

import requests
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, ID3NoHeaderError

from . import metadata
from .cache import make_track_filename


class DownloadCancelled(Exception):
    pass


class Downloader:
    def __init__(self, config, cache) -> None:
        self.config = config
        self.cache = cache

    def download_to_cache(self, track, kind: str = "tracks", playlist_id=None,
                          progress_cb: Optional[Callable] = None, cancel_event=None) -> Path:
        return self.download_track(track, dest_dir=None, progress_cb=progress_cb,
                                   cancel_event=cancel_event, with_tags=False,
                                   kind=kind, playlist_id=playlist_id)

    def download_track(self, track, dest_dir=None, progress_cb: Optional[Callable] = None,
                       cancel_event=None, with_tags: bool = True,
                       kind: str = "tracks", playlist_id=None) -> Path:
        if dest_dir is None:
            path = self.cache.cache_path(track, kind, playlist_id)
        else:
            d = Path(dest_dir)
            d.mkdir(parents=True, exist_ok=True)
            path = d / make_track_filename(track.artist, track.title)
        if path.exists() and path.stat().st_size > 0:
            if with_tags:
                self._write_tags(path, track)
            if dest_dir is None:
                self.cache.register(track, path, kind)
            return path
        url = track.url
        if not url:
            # нет прямой ссылки: resolve для сторонних сервисов, а для ВК —
            # запасной подбор того же трека на YouTube/SoundCloud
            from .services import resolve_any_url
            url = resolve_any_url(track, self.config)
        if ".m3u8" in url:
            self._download_hls(url, path, cancel_event)
        else:
            self._download_file(url, path, progress_cb, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            path.unlink(missing_ok=True)
            raise DownloadCancelled()
        self._ensure_mp3(path)
        if with_tags:
            self._write_tags(path, track)
        if dest_dir is None:
            self.cache.register(track, path, kind)
        return path

    def download_playlist(self, tracks, dest_dir, progress_cb: Optional[Callable] = None,
                          cancel_event=None) -> list:
        total = len(tracks)
        results = []
        for i, t in enumerate(tracks):
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled()
            if progress_cb:
                progress_cb(i, total, t)
            results.append(self.download_track(t, dest_dir=dest_dir))
        if progress_cb:
            progress_cb(total, total, None)
        return results

    # ---- внутреннее ----

    def _proxy_attempts(self) -> list:
        return self.config.proxy_attempts()

    def _env_attempts(self) -> list:
        """То же для ffmpeg: список env со значениями http_proxy/https_proxy."""
        cfg = self.config
        if (cfg.proxy or "").strip():
            p = cfg.current_proxy_str()
            return [p] if p else [""]
        auto = cfg.current_proxy_str() if getattr(cfg, "auto_proxy", False) else ""
        return ["", auto] if auto else [""]

    @staticmethod
    def _looks_like_mp3(path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(3)
        except OSError:
            return False
        return head == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")

    def _ensure_mp3(self, path: Path) -> None:
        """Сторонние сервисы отдают webm/opus/m4a — конвертируем в MP3 через ffmpeg."""
        if self._looks_like_mp3(path):
            return
        ffmpeg = self._find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError(
                "Трек скачан не в MP3 — нужен ffmpeg для конвертации "
                "(Настройки → «Установить ffmpeg»)")
        tmp = path.with_suffix(".conv.mp3")
        cmd = [ffmpeg, "-y", "-i", str(path), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(tmp)]
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "timeout": 900}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(cmd, **kwargs)
        if proc.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise RuntimeError("ffmpeg не смог конвертировать трек в MP3")
        tmp.replace(path)

    def _download_file(self, url: str, path: Path, progress_cb, cancel_event) -> None:
        # ссылки googlevideo привязаны к IP — через прокси они отдают 403,
        # поэтому для них только прямое скачивание
        attempts = [None] if "googlevideo.com" in url else self._proxy_attempts()
        last_exc: Optional[Exception] = None
        for proxies in attempts:
            try:
                self._download_file_once(url, path, progress_cb, cancel_event, proxies)
                return
            except DownloadCancelled:
                raise
            except Exception as e:
                last_exc = e
        raise last_exc

    def _download_file_once(self, url: str, path: Path, progress_cb,
                            cancel_event, proxies) -> None:
        tmp = path.with_suffix(path.suffix + ".part")
        done = 0
        try:
            # connect 10 с, пауза между чанками 20 с — чтобы мёртвый прокси
            # не превращал загрузку в бесконечное ожидание
            with requests.get(url, stream=True, timeout=(10, 20),
                              proxies=proxies) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if cancel_event is not None and cancel_event.is_set():
                            raise DownloadCancelled()
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb:
                            progress_cb(done, total)
            tmp.replace(path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _find_ffmpeg(self):
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
        local = Path(self.config.config_dir) / "bin" / "ffmpeg.exe"
        if local.exists():
            return str(local)
        return None

    def download_ffmpeg(self, progress_cb=None) -> Path:
        """Скачивает статическую сборку ffmpeg (gyan.dev) в %APPDATA%/WaveX/bin."""
        import zipfile

        bin_dir = Path(self.config.config_dir) / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        target = bin_dir / "ffmpeg.exe"
        if target.exists():
            return target
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        tmp_zip = bin_dir / "ffmpeg.zip"
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                with open(tmp_zip, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb:
                            progress_cb(done, total)
            with zipfile.ZipFile(tmp_zip) as zf:
                for name in zf.namelist():
                    if name.endswith("/bin/ffmpeg.exe"):
                        with zf.open(name) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        break
                else:
                    raise RuntimeError("ffmpeg.exe не найден в архиве")
            return target
        finally:
            tmp_zip.unlink(missing_ok=True)

    def _download_hls(self, url: str, path: Path, cancel_event) -> None:
        ffmpeg = self._find_ffmpeg()
        if ffmpeg:
            cmd = [ffmpeg, "-y", "-i", url, "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(path)]
            last_exc: Optional[Exception] = None
            for proxy in self._env_attempts():
                kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                          "timeout": 300}
                if os.name == "nt":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if proxy:
                    env = dict(os.environ)
                    env["http_proxy"] = proxy
                    env["https_proxy"] = proxy
                    kwargs["env"] = env
                try:
                    proc = subprocess.run(cmd, **kwargs)
                except subprocess.TimeoutExpired as e:
                    last_exc = e
                    continue
                if proc.returncode == 0 and path.exists():
                    return
                last_exc = RuntimeError("ffmpeg не смог скачать поток")
            raise last_exc
        print("[downloader] ffmpeg не найден — склейка HLS-сегментов без перекодирования")
        self._download_hls_fallback(url, path, cancel_event)

    def _download_hls_fallback(self, url: str, path: Path, cancel_event) -> None:
        last_exc: Optional[Exception] = None
        for proxies in self._proxy_attempts():
            try:
                self._download_hls_fallback_once(url, path, cancel_event, proxies)
                return
            except DownloadCancelled:
                raise
            except Exception as e:
                last_exc = e
        raise last_exc

    def _download_hls_fallback_once(self, url: str, path: Path,
                                    cancel_event, proxies) -> None:
        def get(u: str) -> requests.Response:
            r = requests.get(u, timeout=(10, 20), proxies=proxies)
            r.raise_for_status()
            return r

        manifest = get(url).text
        lines = [ln.strip() for ln in manifest.splitlines() if ln.strip()]
        # вариантный плейлист -> берём первый поток
        for ln in lines:
            if not ln.startswith("#") and ".m3u8" in ln.lower():
                url = urljoin(url, ln)
                manifest = get(url).text
                lines = [x.strip() for x in manifest.splitlines() if x.strip()]
                break
        segments = [urljoin(url, ln) for ln in lines if not ln.startswith("#")]
        if not segments:
            raise RuntimeError("Пустой HLS-манифест")
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            with open(tmp, "wb") as f:
                for seg in segments:
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelled()
                    f.write(get(seg).content)
            tmp.replace(path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _write_tags(self, path: Path, track) -> None:
        try:
            try:
                tags = ID3(str(path))
            except ID3NoHeaderError:
                tags = ID3()
            for key in ("TIT2", "TPE1", "TALB", "APIC"):
                tags.delall(key)
            tags.add(TIT2(encoding=3, text=track.title))
            tags.add(TPE1(encoding=3, text=track.artist))
            if track.album:
                tags.add(TALB(encoding=3, text=track.album))
            cover = self._get_cover(track)
            if cover:
                tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
            tags.save(str(path), v2_version=3)
        except Exception as e:
            print(f"[downloader] id3: {e}")

    def _get_cover(self, track) -> Optional[bytes]:
        try:
            if self.config.hires_covers:
                return metadata.get_hires_cover(track, self.config)
            if track.cover_url:
                r = requests.get(track.cover_url, timeout=15, proxies=self.config.get_proxies())
                if r.ok:
                    return r.content
        except requests.exceptions.RequestException:
            pass
        return None
