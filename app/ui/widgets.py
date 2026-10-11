import queue
import threading
import tkinter as tk
from collections import OrderedDict
from io import BytesIO
from typing import Callable, Optional

import customtkinter as ctk
import requests
from PIL import Image

from . import glass


def fmt_duration(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def smooth_scroll(sf) -> None:
    """Форсирует перерисовку после прокрутки колёсиком.

    На Windows при прокрутке CTkScrollableFrame встроенные виджеты
    на канвасе перерисовываются с опозданием и оставляют «следы».
    Прогон idle-задач сразу после прокрутки убирает эти артефакты.
    """
    def _redraw(event, _sf=sf):
        try:
            if _sf._check_if_valid_scroll(event.widget):
                _sf._parent_canvas.update_idletasks()
        except Exception:
            pass
    sf.bind_all("<MouseWheel>", _redraw, add=True)


class CoverCache:
    """LRU-кеш CTkImage в памяти."""

    def __init__(self, capacity: int = 300) -> None:
        self.capacity = capacity
        self._data: OrderedDict = OrderedDict()

    def get(self, key):
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key, image) -> None:
        self._data[key] = image
        self._data.move_to_end(key)
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)


class CoverManager:
    """Загрузка обложек в фоне.

    Колбэки маршалятся в UI-поток через очередь + поллер (set_dispatch),
    поэтому виджетам не нужен свой after(). Сеть: сначала напрямую,
    при сбое — через автопрокси (если включён)."""

    def __init__(self, config, capacity: int = 300) -> None:
        self.config = config
        self.cache = CoverCache(capacity)
        self._pending: set = set()
        self._waiting: dict = {}
        self._queue: queue.Queue = queue.Queue()
        self._dispatch: Optional[Callable] = None

    def set_dispatch(self, dispatch: Callable) -> None:
        """dispatch — app.after: планирует поллер очереди в UI-потоке."""
        self._dispatch = dispatch
        dispatch(100, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                cb, img = self._queue.get_nowait()
                try:
                    cb(img)
                except Exception:
                    pass
        except queue.Empty:
            pass
        if self._dispatch is not None:
            self._dispatch(100, self._poll)

    @staticmethod
    def _placeholder(size: int) -> ctk.CTkImage:
        img = Image.new("RGB", (size, size), (60, 60, 68))
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))

    def get(self, url: str, size: int, callback: Callable,
            fallback: Optional[str] = None) -> None:
        """fallback — «исполнитель название» для поиска обложки в iTunes,
        когда у трека нет своей (ВК отдаёт её только при наличии альбома)."""
        key = (url, size) if url else ("it", fallback or "", size)
        cached = self.cache.get(key)
        if cached is not None:
            callback(cached)
            return
        callback(self._placeholder(size))
        if not url and not fallback:
            return
        self._waiting.setdefault(key, []).append(callback)
        if key in self._pending:
            return  # уже качается — колбэк получит результат вместе с остальными
        self._pending.add(key)
        threading.Thread(target=self._job, args=(key, url, size, fallback),
                         daemon=True).start()

    def _job(self, key, url: str, size: int, fallback: Optional[str] = None) -> None:
        if not url and fallback:
            from ..metadata import find_cover_url
            artist, _, title = fallback.partition(" — ")
            url = find_cover_url(artist, title, self.config)
        img = None
        if url:
            for proxies in self.config.proxy_attempts():
                try:
                    r = requests.get(url, timeout=(8, 12), proxies=proxies)
                    r.raise_for_status()
                    pil = Image.open(BytesIO(r.content)).convert("RGB")
                    pil = pil.resize((size * 2, size * 2), Image.LANCZOS)
                    img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
                    break
                except Exception:
                    continue
        if img is not None:
            self.cache.put(key, img)
        callbacks = self._waiting.pop(key, [])
        self._pending.discard(key)
        for cb in callbacks:
            final = img if img is not None else self._placeholder(size)
            if self._dispatch is not None:
                self._queue.put((cb, final))
            else:
                # поллер не настроен (тесты) — отдаём как раньше, напрямую
                try:
                    cb(final)
                except Exception:
                    pass


class TrackRow(ctk.CTkFrame):
    NORMAL_BG = ("#dbdbdb", "#2b2b2b")
    PLAYING_BG = ("#c9e0f5", "#1f3a52")

    # значок сервиса: (подпись, цвет фона, цвет текста)
    SERVICE_BADGES = {
        "vk": ("VK", "#0077FF", "white"),
        "youtube": ("YT", "#CC0000", "white"),
        "soundcloud": ("SC", "#FF5500", "white"),
        "yandex": ("ЯМ", "#FC3F1D", "white"),
        "spotify": ("SP", "#1DB954", "black"),
    }

    def __init__(self, master, track=None, on_play: Optional[Callable] = None,
                 menu_handlers: Optional[dict] = None, cover_manager=None, **kw) -> None:
        p = glass.current()
        self._normal_bg = p.row
        self._playing_bg = p.row_playing
        super().__init__(master, corner_radius=10, fg_color=self._normal_bg, **kw)
        sub_color = p.text_secondary
        title_color = p.text
        self.track = None
        self._cover_token = 0
        self.on_play = on_play
        self.menu_handlers = menu_handlers or {}
        self.cover_manager = cover_manager
        self.grid_columnconfigure(1, weight=1)

        self.cover_label = ctk.CTkLabel(self, text="", width=48, height=48)
        self.cover_label.grid(row=0, column=0, padx=6, pady=4)

        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.grid(row=0, column=1, sticky="ew", padx=4)
        self.title_label = ctk.CTkLabel(
            text_frame, text="", anchor="w", text_color=title_color,
            font=ctk.CTkFont(size=13, weight="bold"))
        self.title_label.pack(fill="x")
        self.artist_label = ctk.CTkLabel(
            text_frame, text="", anchor="w", text_color=sub_color,
            font=ctk.CTkFont(size=12))
        self.artist_label.pack(fill="x")

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=2, padx=6)
        self.badge_label = ctk.CTkLabel(
            right, text="", width=30, height=16, corner_radius=5,
            font=ctk.CTkFont(size=9, weight="bold"))
        self.dur_label = ctk.CTkLabel(right, text="", text_color=sub_color, width=48)
        self.dur_label.pack()
        menu_btn = ctk.CTkButton(self, text="⋯", width=32, command=self._show_menu)
        menu_btn.grid(row=0, column=3, padx=6)

        for w in (self, self.cover_label, text_frame, self.title_label, self.artist_label):
            w.bind("<Button-1>", lambda e: self._play())

        if track is not None:
            self.set_track(track)

    def set_track(self, track) -> None:
        """Переиспользование строки под другой трек (виртуализация списка)."""
        self.track = track
        self._cover_token += 1
        self.title_label.configure(text=track.title)
        self.artist_label.configure(text=track.artist)
        self.dur_label.configure(text=fmt_duration(track.duration))
        badge = self.SERVICE_BADGES.get(getattr(track, "service", "vk"))
        if badge and not track.is_local:
            text, bg, fg = badge
            self.badge_label.configure(text=text, fg_color=bg, text_color=fg)
            self.badge_label.pack(before=self.dur_label)
        else:
            self.badge_label.pack_forget()
        if self.cover_manager is not None:
            token = self._cover_token
            self.cover_manager.get(
                track.cover_url or "", 48,
                lambda img, t=token: self._cover_cb(img, t),
                fallback=f"{track.artist} — {track.title}")

    def _cover_cb(self, img, token: int) -> None:
        if token != self._cover_token:
            return
        try:
            self.after(0, lambda i=img, t=token: self._apply_cover(i, t))
        except Exception:
            pass

    def _apply_cover(self, img, token: int) -> None:
        if token == self._cover_token:
            self.cover_label.configure(image=img)

    def _play(self) -> None:
        if self.on_play and self.track is not None:
            self.on_play(self.track)

    def _show_menu(self) -> None:
        if self.track is None:
            return
        menu = tk.Menu(self, tearoff=0)
        h = self.menu_handlers
        local = self.track.is_local
        dl_state = "disabled" if local else "normal"
        menu.add_command(label="Скачать", state=dl_state,
                         command=lambda: h.get("download", lambda t: None)(self.track))
        menu.add_command(label="Скачать в…", state=dl_state,
                         command=lambda: h.get("download_to", lambda t: None)(self.track))
        menu.add_command(label="Добавить в очередь",
                         command=lambda: h.get("queue", lambda t: None)(self.track))
        menu.add_command(label="Текст песни",
                         command=lambda: h.get("lyrics", lambda t: None)(self.track))
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def set_playing(self, flag: bool) -> None:
        self.configure(fg_color=self._playing_bg if flag else self._normal_bg)


class TrackList(ctk.CTkFrame):
    """Виртуализированный список треков.

    CTkScrollableFrame держит все строки как окна на одном канвасе — под
    Windows при прокрутке они не успевают перерисовываться и оставляют
    «следы». Здесь строки переиспользуются: создано лишь столько виджетов,
    сколько помещается на экране (+2), а прокрутка просто переставляет их.
    """

    ROW_H = 58

    def __init__(self, master, on_play: Optional[Callable] = None,
                 menu_handlers: Optional[dict] = None, cover_manager=None, **kw) -> None:
        kw.setdefault("fg_color", "transparent")
        super().__init__(master, **kw)
        self.on_play = on_play
        self.menu_handlers = menu_handlers
        self.cover_manager = cover_manager
        self.tracks: list = []
        self.rows: list = []          # пул переиспользуемых строк
        self._scroll = 0.0
        self._playing_key = None

        self._scrollbar = ctk.CTkScrollbar(self, orientation="vertical",
                                           command=self._yview)
        self._viewport = ctk.CTkFrame(self, fg_color="transparent")
        self._viewport.pack(side="left", fill="both", expand=True)
        self._viewport.bind("<Configure>", lambda e: self._refresh())
        self._bind_wheel(self._viewport)

    # ---- данные ----

    def add_tracks(self, tracks) -> None:
        self.tracks.extend(tracks)
        self._refresh()

    def set_tracks(self, tracks) -> None:
        self.tracks = list(tracks)
        self._scroll = 0.0
        self._refresh()

    def clear(self) -> None:
        self.tracks = []
        self._scroll = 0.0
        self._refresh()

    def set_playing(self, track) -> None:
        self._playing_key = (None if track is None
                             else (track.full_id, track.local_path))
        for row in self.rows:
            if row.winfo_manager():
                row.set_playing(self._row_matches(row))

    def _row_matches(self, row) -> bool:
        return (self._playing_key is not None and row.track is not None
                and (row.track.full_id, row.track.local_path) == self._playing_key)

    # ---- прокрутка ----

    def _yview(self, *args) -> None:
        h = self._viewport.winfo_height()
        total_h = len(self.tracks) * self.ROW_H
        if total_h <= h:
            return
        if args[0] == "moveto":
            self._scroll_to(float(args[1]) * total_h)
        elif args[0] == "scroll":
            step = self.ROW_H * 3 if args[2] == "units" else h
            self._scroll_to(self._scroll + int(args[1]) * step)

    def _scroll_to(self, pos: float) -> None:
        h = max(self._viewport.winfo_height(), 1)
        max_scroll = max(0, len(self.tracks) * self.ROW_H - h)
        pos = max(0.0, min(pos, float(max_scroll)))
        if pos != self._scroll:
            self._scroll = pos
            self._refresh()

    def _on_wheel(self, event) -> str:
        h = self._viewport.winfo_height()
        total_h = len(self.tracks) * self.ROW_H
        if total_h > h:
            self._scroll_to(self._scroll - event.delta / 120 * self.ROW_H * 3)
        return "break"

    def _bind_wheel(self, widget) -> None:
        widget.bind("<MouseWheel>", self._on_wheel)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    # ---- раскладка ----

    def _refresh(self) -> None:
        h = self._viewport.winfo_height()
        if h <= 1:
            return
        total = len(self.tracks)
        max_scroll = max(0, total * self.ROW_H - h)
        if self._scroll > max_scroll:
            self._scroll = float(max_scroll)
        first = int(self._scroll // self.ROW_H)
        need = max(0, min(total - first, h // self.ROW_H + 2))

        while len(self.rows) < need:
            row = TrackRow(self._viewport, None, on_play=self.on_play,
                           menu_handlers=self.menu_handlers,
                           cover_manager=self.cover_manager,
                           height=self.ROW_H - 2)
            row.grid_propagate(False)
            self._bind_wheel(row)
            self.rows.append(row)

        for i, row in enumerate(self.rows):
            idx = first + i
            if i < need:
                track = self.tracks[idx]
                if row.track is not track:
                    row.set_track(track)
                row.set_playing(self._row_matches(row))
                row.place(x=0, y=int(idx * self.ROW_H - self._scroll),
                          relwidth=1)
            else:
                row.place_forget()

        total_h = total * self.ROW_H
        if total_h > h:
            if not self._scrollbar.winfo_manager():
                self._scrollbar.pack(side="right", fill="y")
            self._scrollbar.set(self._scroll / total_h,
                                (self._scroll + h) / total_h)
        else:
            self._scrollbar.pack_forget()


class EqualizerWindow(ctk.CTkToplevel):
    def __init__(self, master, app) -> None:
        super().__init__(master)
        from ..equalizer import BAND_LABELS, PRESETS, normalize_gains
        self.app = app
        cfg = app.config
        self.title("Эквалайзер")
        self.geometry("480x460")
        self.transient(master)
        self._apply_after = None

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))
        self.enabled_var = ctk.BooleanVar(value=bool(cfg.eq_enabled))
        ctk.CTkCheckBox(top, text="Включён", variable=self.enabled_var,
                        command=self._changed).pack(side="left")
        self.preset_var = ctk.StringVar(value="Плоский")
        ctk.CTkOptionMenu(top, values=list(PRESETS), variable=self.preset_var,
                          command=self._preset).pack(side="left", padx=12)

        gains = normalize_gains(cfg.eq_gains)
        self.sliders: list = []
        self.val_labels: list = []
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=14, pady=8)
        for i, label in enumerate(BAND_LABELS):
            col = ctk.CTkFrame(frame, fg_color="transparent")
            col.pack(side="left", fill="y", expand=True, padx=4)
            val = ctk.CTkLabel(col, text=self._fmt(gains[i]),
                               font=ctk.CTkFont(size=11))
            val.pack()
            slider = ctk.CTkSlider(col, from_=-12, to=12, orientation="vertical",
                                   height=150, number_of_steps=48,
                                   command=lambda v, i=i: self._slider(i, v))
            slider.set(gains[i])
            slider.pack()
            ctk.CTkLabel(col, text=label, text_color="gray",
                         font=ctk.CTkFont(size=10)).pack()
            self.sliders.append(slider)
            self.val_labels.append(val)

        # ---- скорость и тон ----
        from ..equalizer import normalize_pitch, normalize_tempo
        extra = ctk.CTkFrame(self, fg_color="transparent")
        extra.pack(fill="x", padx=14, pady=(2, 0))

        speed_row = ctk.CTkFrame(extra, fg_color="transparent")
        speed_row.pack(fill="x", pady=2)
        ctk.CTkLabel(speed_row, text="Скорость", width=70, anchor="w").pack(side="left")
        self.speed_label = ctk.CTkLabel(
            speed_row, text=f"×{normalize_tempo(cfg.eq_tempo):.2f}",
            width=48, font=ctk.CTkFont(size=11))
        self.speed_label.pack(side="left")
        self.speed_slider = ctk.CTkSlider(
            speed_row, from_=0.5, to=2.0, number_of_steps=30,
            command=self._speed)
        self.speed_slider.set(normalize_tempo(cfg.eq_tempo))
        self.speed_slider.pack(side="left", fill="x", expand=True, padx=6)

        pitch_row = ctk.CTkFrame(extra, fg_color="transparent")
        pitch_row.pack(fill="x", pady=2)
        ctk.CTkLabel(pitch_row, text="Тон", width=70, anchor="w").pack(side="left")
        self.pitch_label = ctk.CTkLabel(
            pitch_row, text=f"{normalize_pitch(cfg.eq_pitch):+.0f} пт",
            width=48, font=ctk.CTkFont(size=11))
        self.pitch_label.pack(side="left")
        self.pitch_slider = ctk.CTkSlider(
            pitch_row, from_=-12, to=12, number_of_steps=24,
            command=self._pitch)
        self.pitch_slider.set(normalize_pitch(cfg.eq_pitch))
        self.pitch_slider.pack(side="left", fill="x", expand=True, padx=6)

        ctk.CTkLabel(self, text="Изменения применяются к играющему треку автоматически",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(pady=(0, 10))

    @staticmethod
    def _fmt(v: float) -> str:
        return f"{v:+.0f} дБ"

    def _slider(self, i: int, v: float) -> None:
        self.val_labels[i].configure(text=self._fmt(v))
        self.app.config.eq_gains = [round(s.get(), 1) for s in self.sliders]
        self._schedule()

    def _speed(self, v: float) -> None:
        self.speed_label.configure(text=f"×{v:.2f}")
        self.app.config.eq_tempo = round(float(v), 2)
        self._schedule()

    def _pitch(self, v: float) -> None:
        self.pitch_label.configure(text=f"{v:+.0f} пт")
        self.app.config.eq_pitch = round(float(v))
        self._schedule()

    def _preset(self, name: str) -> None:
        from ..equalizer import PRESETS
        gains = PRESETS.get(name, [0.0] * 5)
        for slider, lbl, g in zip(self.sliders, self.val_labels, gains):
            slider.set(g)
            lbl.configure(text=self._fmt(g))
        self.app.config.eq_gains = list(gains)
        self._schedule()

    def _changed(self) -> None:
        self.app.config.eq_enabled = bool(self.enabled_var.get())
        self._schedule()

    def _schedule(self) -> None:
        self.app.config.save()
        if self._apply_after is not None:
            self.after_cancel(self._apply_after)
        self._apply_after = self.after(500, self.app.player.apply_eq)


class LyricsWindow(ctk.CTkToplevel):
    def __init__(self, master, title: str, text: str) -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("480x560")
        box = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(size=13))
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("1.0", text)
        box.configure(state="disabled")


class DownloadDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str = "Загрузки") -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("460x360")
        self.frame = ctk.CTkScrollableFrame(self)
        self.frame.pack(fill="both", expand=True, padx=10, pady=10)
        smooth_scroll(self.frame)
        self.items: dict = {}
        self._next_id = 0

    def add_item(self, name: str) -> int:
        iid = self._next_id
        self._next_id += 1
        f = ctk.CTkFrame(self.frame)
        f.pack(fill="x", pady=3)
        ctk.CTkLabel(f, text=name, anchor="w").pack(fill="x", padx=6, pady=(4, 0))
        bar = ctk.CTkProgressBar(f)
        bar.pack(fill="x", padx=6, pady=2)
        bar.set(0)
        status = ctk.CTkLabel(f, text="", anchor="e", text_color="gray",
                              font=ctk.CTkFont(size=11))
        status.pack(fill="x", padx=6, pady=(0, 4))
        self.items[iid] = (bar, status)
        return iid

    def set_progress(self, iid: int, frac: float) -> None:
        if iid in self.items:
            self.items[iid][0].set(max(0.0, min(1.0, frac)))

    def mark_done(self, iid: int) -> None:
        if iid in self.items:
            self.items[iid][0].set(1.0)
            self.items[iid][1].configure(text="Готово")

    def mark_error(self, iid: int, msg: str) -> None:
        if iid in self.items:
            self.items[iid][1].configure(text=f"Ошибка: {msg}", text_color="#d9534f")
