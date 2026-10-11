import threading
import time
from pathlib import Path
from typing import Callable, Optional

import pygame

from .downloader import DownloadCancelled
from .vk_api import Track


class Player:
    """Воспроизведение через pygame.mixer. Трек сначала прогрессивно
    скачивается в кеш, затем играется из файла."""

    def __init__(self, config, cache, downloader, ui_dispatch: Callable,
                 on_track_end: Optional[Callable] = None,
                 on_state_change: Optional[Callable] = None,
                 on_error: Optional[Callable] = None) -> None:
        self.config = config
        self.cache = cache
        self.downloader = downloader
        self.ui_dispatch = ui_dispatch
        self.on_track_end = on_track_end
        self.on_state_change = on_state_change
        self.on_error = on_error

        self.queue: list = []
        self.index: int = -1
        self.repeat_mode: str = "off"  # off | all | one
        self.current: Optional[Track] = None
        self.state: str = "stopped"    # stopped | buffering | playing | paused

        self._offset = 0.0
        self._started_at = 0.0
        self._length = 0.0
        self._rendered_tempo = 1.0
        self._kind = "tracks"
        self._playlist_id = None
        self._cancel = threading.Event()

        self.available = True
        try:
            pygame.mixer.init(44100, -16, 2, 2048)
            pygame.mixer.music.set_volume(max(0.0, min(1.0, float(config.volume))))
        except Exception as e:
            print(f"[player] нет аудиоустройства: {e}")
            self.available = False

        threading.Thread(target=self._tick, daemon=True).start()

    # ---- очередь ----

    def set_queue(self, tracks, start_index: int = 0, kind: str = "tracks", playlist_id=None) -> None:
        self.queue = list(tracks)
        self._kind = kind
        self._playlist_id = playlist_id
        self.play_index(start_index)

    def add_to_queue(self, track: Track) -> None:
        self.queue.append(track)
        if self.state == "stopped":
            self.play_index(len(self.queue) - 1)

    def play_index(self, i: int) -> None:
        if not self.queue:
            return
        i = max(0, min(int(i), len(self.queue) - 1))
        self.index = i
        self.play_track(self.queue[i])

    def next(self) -> None:
        if not self.queue:
            return
        nxt = self.index + 1
        if nxt >= len(self.queue):
            if self.repeat_mode == "all":
                nxt = 0
            else:
                self.stop()
                return
        self.play_index(nxt)

    def prev(self) -> None:
        if not self.queue:
            return
        if self.get_pos() > 3:
            self.seek(0)
            return
        self.play_index(max(0, self.index - 1))

    # ---- воспроизведение ----

    def play_track(self, track: Track) -> None:
        self._cancel.set()
        self._cancel = threading.Event()
        self.current = track
        self._offset = 0.0
        self._length = float(track.duration or 0)
        if self.available:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self._set_state("buffering")
        cancel = self._cancel

        def job() -> None:
            try:
                path = self._prepare_source(track, cancel)
                path = self._apply_eq(path)
            except DownloadCancelled:
                return
            except Exception as e:
                if not cancel.is_set():
                    self.ui_dispatch(lambda e=e: self._on_download_error(e))
                return
            if cancel.is_set() or self.current is not track:
                return
            self.ui_dispatch(lambda: self._play_file(path))

        threading.Thread(target=job, daemon=True).start()

    def _prepare_source(self, track: Track, cancel) -> Path:
        """Локальный файл, кеш или скачивание (с resolve для сторонних сервисов)."""
        if track.is_local:
            return Path(track.local_path)
        cached = self.cache.get_cached_path(track)
        if cached is not None:
            return cached
        kind = "podcasts" if track.is_podcast else self._kind
        playlist_id = self._playlist_id if kind == "playlists" else None
        return self.downloader.download_to_cache(
            track, kind=kind, playlist_id=playlist_id, cancel_event=cancel)

    def _apply_eq(self, path: Path) -> Path:
        """Рендерит файл через ffmpeg, если включён эквалайзер или
        изменены скорость/тон. Запоминает темп рендера в _rendered_tempo."""
        from . import equalizer
        tempo = equalizer.normalize_tempo(getattr(self.config, "eq_tempo", 1.0))
        pitch = equalizer.normalize_pitch(getattr(self.config, "eq_pitch", 0.0))
        gains = equalizer.normalize_gains(getattr(self.config, "eq_gains", []))
        if not getattr(self.config, "eq_enabled", False):
            gains = [0.0] * len(gains)
        if not equalizer.needs_render(gains, tempo, pitch):
            self._rendered_tempo = 1.0
            return path
        ffmpeg = self.downloader._find_ffmpeg()
        if not ffmpeg:
            self._rendered_tempo = 1.0
            return path
        dest = equalizer.rendered_path(self.config.cache_dir, path, gains,
                                       tempo, pitch)
        out = equalizer.render(ffmpeg, path, gains, dest, tempo, pitch)
        self._rendered_tempo = tempo
        return out

    def apply_eq(self) -> None:
        """Перерендерить текущий трек с новыми настройками эквалайзера,
        продолжив с той же позиции."""
        track = self.current
        if track is None or self.state not in ("playing", "paused"):
            return
        old_tempo = getattr(self, "_rendered_tempo", 1.0)
        new_tempo = float(getattr(self.config, "eq_tempo", 1.0) or 1.0)
        pos = self.get_pos()
        was_paused = self.state == "paused"
        cancel = self._cancel

        def job() -> None:
            try:
                src = self._prepare_source(track, cancel)
                path = self._apply_eq(src)
            except Exception as e:
                print(f"[player] eq: {e}")
                return
            if cancel.is_set() or self.current is not track:
                return

            def ui() -> None:
                self._play_file(path)
                if self.state == "playing":
                    # позиция в исходном треке = pos * old_tempo;
                    # в новом рендере — делим на новый темп
                    self.seek(pos * old_tempo / max(0.01, new_tempo))
                    if was_paused:
                        self.pause()
            self.ui_dispatch(ui)

        threading.Thread(target=job, daemon=True).start()

    def _play_file(self, path: Path) -> None:
        if not self.available:
            self._set_state("stopped")
            return
        try:
            pygame.mixer.music.load(str(path))
            self._offset = 0.0
            pygame.mixer.music.play()
            self._started_at = time.monotonic()
            # длина всегда из реального файла: при изменённом темпе рендер
            # короче/длиннее исходника, а track.duration про него не знает
            try:
                from mutagen.mp3 import MP3
                self._length = float(MP3(str(path)).info.length)
            except Exception:
                pass
            self._set_state("playing")
        except Exception as e:
            print(f"[player] не удалось воспроизвести {path}: {e}")
            self._set_state("stopped")

    def pause(self) -> None:
        if not self.available or self.state != "playing":
            return
        try:
            pygame.mixer.music.pause()
        except Exception:
            return
        self._offset += time.monotonic() - self._started_at
        self._set_state("paused")

    def resume(self) -> None:
        if not self.available or self.state != "paused":
            return
        try:
            pygame.mixer.music.unpause()
        except Exception:
            return
        self._started_at = time.monotonic()
        self._set_state("playing")

    def stop(self) -> None:
        if self.available:
            try:
                pygame.mixer.music.stop()
                # освобождаем файловый дескриптор — иначе на Windows
                # играющий файл не даёт удалить себя при очистке кеша
                pygame.mixer.music.unload()
            except Exception:
                pass
        self._offset = 0.0
        self._set_state("stopped")

    def seek(self, seconds: float) -> None:
        if not self.available or self.current is None:
            return
        if self.state not in ("playing", "paused"):
            return
        seconds = max(0.0, float(seconds))
        was_paused = self.state == "paused"
        try:
            pygame.mixer.music.play(start=seconds)
            if was_paused:
                pygame.mixer.music.pause()
        except Exception as e:
            print(f"[player] seek: {e}")
            return
        self._offset = seconds
        self._started_at = time.monotonic()
        self._set_state("paused" if was_paused else "playing")

    def set_volume(self, v: float) -> None:
        v = max(0.0, min(1.0, float(v)))
        self.config.volume = v
        if self.available:
            try:
                pygame.mixer.music.set_volume(v)
            except Exception:
                pass

    def get_pos(self) -> float:
        if self.state == "playing":
            return self._offset + (time.monotonic() - self._started_at)
        if self.state == "paused":
            return self._offset
        return 0.0

    def get_length(self) -> float:
        return self._length

    def shutdown(self) -> None:
        self._cancel.set()
        if self.available:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.quit()
            except Exception:
                pass

    # ---- конец трека ----

    def _tick(self) -> None:
        # get_busy() надёжнее set_endevent при seek/pause; 1с — защита от
        # ложного срабатывания сразу после play().
        while True:
            time.sleep(0.25)
            if self.available and self.state == "playing":
                try:
                    busy = pygame.mixer.music.get_busy()
                except Exception:
                    busy = True
                if not busy and (time.monotonic() - self._started_at) > 1.0:
                    self.ui_dispatch(self._on_end)

    def _on_end(self) -> None:
        if self.state != "playing":
            return
        track = self.current
        if self.repeat_mode == "one" and self.index >= 0:
            self.play_index(self.index)
        elif self.index + 1 < len(self.queue):
            self.play_index(self.index + 1)
        elif self.repeat_mode == "all" and self.queue:
            self.play_index(0)
        else:
            self.stop()
        if self.on_track_end and track is not None:
            self.on_track_end(track)

    def _on_download_error(self, error: Exception) -> None:
        print(f"[player] загрузка трека: {error}")
        self._set_state("stopped")
        if self.on_error:
            self.on_error(error)

    def _set_state(self, state: str) -> None:
        self.state = state
        if self.on_state_change:
            self.on_state_change(state)
