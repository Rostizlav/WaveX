import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import metadata
from ..cache import CacheManager
from ..downloader import Downloader
from ..player import Player
from ..vk_api import AuthError, VKApiError
from . import glass
from .fullscreen import FullscreenPlayer
from .views import (
    CatalogView, DownloadsView, MyMusicView, PlaylistsView,
    PodcastsView, SearchView, SettingsView, SupportView,
)
from .widgets import (
    CoverManager, DownloadDialog, EqualizerWindow, LyricsWindow, TrackList,
    fmt_duration,
)

VIEWS = {
    "my": ("Мои аудио", MyMusicView),
    "search": ("Поиск", SearchView),
    "catalog": ("Каталог", CatalogView),
    "playlists": ("Плейлисты", PlaylistsView),
    "podcasts": ("Подкасты", PodcastsView),
    "downloads": ("Загрузки", DownloadsView),
    "support": ("Поддержать автора", SupportView),
    "settings": ("Настройки", SettingsView),
}

VK_ONLY_VIEWS = {"my", "catalog", "playlists", "podcasts"}

REPEAT_LABELS = {"off": "Повтор: выкл", "all": "Повтор: все", "one": "Повтор: один"}


class MainApp(ctk.CTk):
    def __init__(self, config, api) -> None:
        super().__init__()
        self.config = config
        self.api = api
        self.want_relogin = False

        glass.apply_to_window(self, config)
        self.title("WaveX")
        try:
            import os
            import sys
            base = getattr(sys, "_MEIPASS",
                           os.path.dirname(os.path.dirname(os.path.dirname(
                               os.path.abspath(__file__)))))
            self.iconbitmap(os.path.join(base, "icon.ico"))
        except Exception:
            pass
        self.geometry("1100x720")
        self.minsize(900, 600)

        from ..tray import TrayIcon
        self._tray_notified = False
        self.tray = TrayIcon(self, title="WaveX")
        self.tray.start()

        self.cache = CacheManager(config)
        self.downloader = Downloader(config, self.cache)
        self.cover_manager = CoverManager(config)
        self.cover_manager.set_dispatch(self.after)
        self.player = Player(
            config, self.cache, self.downloader,
            ui_dispatch=lambda fn: self.after(0, fn),
            on_track_end=lambda t: None,
            on_state_change=self._on_player_state,
            on_error=lambda e: self.after(
                0, lambda: messagebox.showwarning("WaveX — воспроизведение", str(e))),
        )

        self.views: dict = {}
        self.current_view = None
        self._seek_dragging = False
        self._download_dialog = None

        self._build_layout()
        self._load_user_info()
        self.show_view("my" if self.api is not None else "search")
        self._tick_seek()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- каркас ----

    def _build_layout(self) -> None:
        g = glass.is_glass()
        pad = 8 if g else 0
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=180, corner_radius=16 if g else 0)
        self.sidebar.grid(row=0, column=0, sticky="ns", padx=(pad, pad // 2), pady=pad)
        self.sidebar.grid_propagate(False)
        glass.style_panel(self.sidebar)

        ctk.CTkLabel(self.sidebar, text="WaveX",
                     font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(16, 10))
        self.nav_buttons: dict = {}
        for key, (label, _cls) in VIEWS.items():
            btn = ctk.CTkButton(self.sidebar, text=label, anchor="w", height=36,
                                fg_color="transparent",
                                text_color=("gray10", "gray90"),
                                hover_color=("gray80", "gray25"),
                                corner_radius=12 if g else 6,
                                command=lambda k=key: self.show_view(k))
            if g:
                btn.configure(text_color=glass.GLASS.text,
                              hover_color=glass.GLASS.hover)
            btn.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = btn

        self.user_label = ctk.CTkLabel(
            self.sidebar, text="",
            text_color=glass.GLASS.text_secondary if g else "gray",
            font=ctk.CTkFont(size=11))
        self.user_label.pack(side="bottom", pady=10)

        self.content = ctk.CTkFrame(self, corner_radius=16 if g else 0,
                                    fg_color="transparent" if not g else glass.GLASS.panel)
        if g:
            self.content.configure(border_width=1, border_color=glass.GLASS.border)
        self.content.grid(row=0, column=1, sticky="nsew",
                          padx=(pad // 2, pad), pady=pad)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self._build_player_bar()

    def _build_player_bar(self) -> None:
        g = glass.is_glass()
        p = glass.current()
        bar = ctk.CTkFrame(self, height=84,
                           corner_radius=16 if g else 0,
                           fg_color=(p.panel if g else ("gray86", "gray16")))
        if g:
            bar.configure(border_width=1, border_color=p.border)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew",
                 padx=8 if g else 0, pady=(0, 8) if g else 0)
        bar.grid_propagate(False)
        bar.grid_columnconfigure(2, weight=1)

        txt = p.text if g else None
        sub = p.text_secondary if g else "gray"
        ghost_hover = p.hover if g else ("gray80", "gray25")
        ghost_text = p.text if g else ("gray10", "gray90")

        self.bar_cover = ctk.CTkLabel(bar, text="", width=56, height=56)
        self.bar_cover.grid(row=0, column=0, padx=(12, 8), pady=14)

        info = ctk.CTkFrame(bar, fg_color="transparent", width=200)
        info.grid(row=0, column=1, sticky="w")
        info.grid_propagate(False)
        self.bar_title = ctk.CTkLabel(info, text="", anchor="w", text_color=txt,
                                      font=ctk.CTkFont(size=13, weight="bold"))
        self.bar_title.pack(fill="x")
        self.bar_artist = ctk.CTkLabel(info, text="", anchor="w", text_color=sub,
                                       font=ctk.CTkFont(size=12))
        self.bar_artist.pack(fill="x")

        center = ctk.CTkFrame(bar, fg_color="transparent")
        center.grid(row=0, column=2, sticky="ew", padx=12)
        center.grid_columnconfigure(1, weight=1)

        controls = ctk.CTkFrame(center, fg_color="transparent")
        controls.grid(row=0, column=0, columnspan=3)
        ctk.CTkButton(controls, text="⏮", width=40, fg_color="transparent",
                      text_color=ghost_text, hover_color=ghost_hover,
                      command=self.player.prev).pack(side="left", padx=2)
        self.play_btn = ctk.CTkButton(controls, text="▶", width=44,
                                      command=self._toggle_play)
        if g:
            glass.style_accent_button(self.play_btn)
        self.play_btn.pack(side="left", padx=2)
        ctk.CTkButton(controls, text="⏭", width=40, fg_color="transparent",
                      text_color=ghost_text, hover_color=ghost_hover,
                      command=self.player.next).pack(side="left", padx=2)
        self.repeat_btn = ctk.CTkButton(controls, text=REPEAT_LABELS["off"], width=110,
                                        fg_color="transparent",
                                        text_color=ghost_text,
                                        hover_color=ghost_hover,
                                        command=self._cycle_repeat)
        self.repeat_btn.pack(side="left", padx=8)

        self.time_pos = ctk.CTkLabel(center, text="0:00", width=44, text_color=sub,
                                     font=ctk.CTkFont(size=11))
        self.time_pos.grid(row=1, column=0, sticky="e")
        self.seek = ctk.CTkSlider(center, from_=0, to=100, number_of_steps=1000)
        if g:
            self.seek.configure(progress_color=p.accent, button_color=p.accent,
                                button_hover_color=p.accent_hover)
        self.seek.grid(row=1, column=1, sticky="ew", padx=6)
        self.seek.bind("<ButtonPress-1>", lambda e: setattr(self, "_seek_dragging", True))
        self.seek.bind("<ButtonRelease-1>", self._seek_release)
        self.time_len = ctk.CTkLabel(center, text="0:00", width=44, text_color=sub,
                                     font=ctk.CTkFont(size=11))
        self.time_len.grid(row=1, column=2, sticky="w")

        vol_frame = ctk.CTkFrame(bar, fg_color="transparent")
        vol_frame.grid(row=0, column=3, padx=12)
        eq_btn = ctk.CTkButton(vol_frame, text="EQ", width=36,
                               fg_color="transparent", text_color=ghost_text,
                               hover_color=ghost_hover,
                               command=self._open_equalizer)
        eq_btn.pack(side="left", padx=(0, 4))
        ctk.CTkButton(vol_frame, text="⛶", width=36, fg_color="transparent",
                      text_color=ghost_text, hover_color=ghost_hover,
                      command=self._open_fullscreen).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(vol_frame, text="🔊", text_color=txt).pack(side="left")
        self.vol_slider = ctk.CTkSlider(vol_frame, from_=0, to=1, width=90,
                                        command=lambda v: self.player.set_volume(v))
        if g:
            self.vol_slider.configure(progress_color=p.accent, button_color=p.accent,
                                      button_hover_color=p.accent_hover)
        self.vol_slider.set(max(0.0, min(1.0, float(self.config.volume))))
        self.vol_slider.pack(side="left", padx=4)

    # ---- навигация ----

    def _guest_placeholder(self):
        frame = ctk.CTkFrame(self.content, fg_color="transparent")
        ctk.CTkLabel(frame, text="Раздел ВКонтакте",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(80, 6))
        ctk.CTkLabel(frame, text="Вы в гостевом режиме. Войдите в аккаунт ВК,\n"
                                 "чтобы открыть свои аудио, каталог и плейлисты.",
                     text_color="gray", justify="center").pack()
        login_btn = ctk.CTkButton(frame, text="Войти в аккаунт ВК", width=200,
                                  command=self.on_logout)
        glass.style_accent_button(login_btn)
        login_btn.pack(pady=14)
        return frame

    def show_view(self, name: str) -> None:
        if name not in VIEWS:
            return
        active_color = glass.GLASS.panel_alt if glass.is_glass() else ("gray75", "gray28")
        for key, btn in self.nav_buttons.items():
            btn.configure(fg_color=active_color if key == name else "transparent")
        if self.current_view is not None:
            self.current_view.pack_forget()
        if self.api is None and name in VK_ONLY_VIEWS:
            view = self.views.get("guest")
            if view is None:
                view = self._guest_placeholder()
                self.views["guest"] = view
        else:
            if name not in self.views:
                self.views[name] = VIEWS[name][1](self.content, self)
            view = self.views[name]
        view.pack(fill="both", expand=True)
        self.current_view = view
        if hasattr(view, "on_show"):
            view.on_show()

    # ---- фоновая работа ----

    def run_bg(self, work, on_success=None, on_error=None) -> None:
        def job() -> None:
            try:
                result = work()
            except AuthError as e:
                self.after(0, lambda e=e: self._on_auth_error(e))
                return
            except Exception as e:
                if on_error:
                    self.after(0, lambda e=e: on_error(e))
                else:
                    self.after(0, lambda e=e: messagebox.showerror("WaveX", str(e)))
                return
            if on_success:
                self.after(0, lambda: on_success(result))

        threading.Thread(target=job, daemon=True).start()

    def _on_auth_error(self, error: VKApiError) -> None:
        if messagebox.askyesno("WaveX", f"Сессия ВК истекла ({error.message}).\nВойти заново?"):
            self.on_logout()

    # ---- действия с треками ----

    def play_from_list(self, tracks, track, kind: str = "tracks", playlist_id=None) -> None:
        try:
            index = next(i for i, t in enumerate(tracks)
                         if t.full_id == track.full_id and t.local_path == track.local_path)
        except StopIteration:
            index = 0
        self.player.set_queue(tracks, index, kind=kind, playlist_id=playlist_id)
        self._highlight_current(tracks)

    def _highlight_current(self, tracks) -> None:
        for view in self.views.values():
            tl = getattr(view, "tracklist", None)
            if isinstance(tl, TrackList):
                tl.set_playing(self.player.current)

    def track_menu_handlers(self) -> dict:
        return {
            "download": lambda t: self.download_tracks([t], title=t.title),
            "download_to": self._download_to,
            "queue": self.player.add_to_queue,
            "lyrics": self._show_lyrics,
        }

    def download_tracks(self, tracks, title: str = "Загрузка") -> None:
        dest = Path(self.config.download_dir)
        dialog = self._get_download_dialog()
        dialog.title(title)
        items = [(dialog.add_item(f"{t.artist} — {t.title}"), t) for t in tracks]

        def job() -> None:
            for iid, t in items:
                def cb(done, total, _iid=iid) -> None:
                    frac = (done / total) if total else 0.0
                    self.after(0, lambda: dialog.set_progress(_iid, frac))
                try:
                    self.downloader.download_track(t, dest_dir=dest, progress_cb=cb)
                    self.after(0, lambda _iid=iid: dialog.mark_done(_iid))
                except Exception as e:
                    self.after(0, lambda _iid=iid, err=e: dialog.mark_error(_iid, str(err)))

        threading.Thread(target=job, daemon=True).start()

    def _download_to(self, track) -> None:
        d = filedialog.askdirectory()
        if not d:
            return
        dialog = self._get_download_dialog()
        iid = dialog.add_item(f"{track.artist} — {track.title}")

        def job() -> None:
            def cb(done, total) -> None:
                frac = (done / total) if total else 0.0
                self.after(0, lambda: dialog.set_progress(iid, frac))
            try:
                self.downloader.download_track(track, dest_dir=Path(d), progress_cb=cb)
                self.after(0, lambda: dialog.mark_done(iid))
            except Exception as e:
                self.after(0, lambda: dialog.mark_error(iid, str(e)))

        threading.Thread(target=job, daemon=True).start()

    def _get_download_dialog(self) -> DownloadDialog:
        if self._download_dialog is None or not self._download_dialog.winfo_exists():
            self._download_dialog = DownloadDialog(self)
            self._download_dialog.protocol(
                "WM_DELETE_WINDOW", self._download_dialog.withdraw)
        else:
            self._download_dialog.deiconify()
        self._download_dialog.lift()
        return self._download_dialog

    def _show_lyrics(self, track) -> None:
        def work() -> str:
            return metadata.get_lyrics(track, self.config, api=self.api) or ""

        def show(text: str) -> None:
            if text:
                LyricsWindow(self, f"{track.artist} — {track.title}", text)
            else:
                messagebox.showinfo("WaveX", "Текст песни не найден")

        self.run_bg(work, on_success=show)

    # ---- плеер <-> UI ----

    def _toggle_play(self) -> None:
        state = self.player.state
        if state == "playing":
            self.player.pause()
        elif state == "paused":
            self.player.resume()
        elif self.player.queue and self.player.index >= 0:
            self.player.play_index(self.player.index)

    def _cycle_repeat(self) -> None:
        order = ["off", "all", "one"]
        nxt = order[(order.index(self.player.repeat_mode) + 1) % len(order)]
        self.player.repeat_mode = nxt
        self.repeat_btn.configure(text=REPEAT_LABELS[nxt])

    def _on_player_state(self, state: str) -> None:
        def update() -> None:
            self.play_btn.configure(text="⏸" if state == "playing" else "▶")
            track = self.player.current
            if state in ("playing", "buffering") and track is not None:
                suffix = " (загрузка…)" if state == "buffering" else ""
                self.bar_title.configure(text=(track.title or "")[:60] + suffix)
                self.bar_artist.configure(text=(track.artist or "")[:60])
                self.cover_manager.get(track.cover_url or "", 56, self._bar_cover_cb,
                                       fallback=f"{track.artist} — {track.title}")
                self.time_len.configure(text=fmt_duration(self.player.get_length()))
                self._highlight_current(None)
            if state == "stopped":
                self.time_pos.configure(text="0:00")
            fs = getattr(self, "_fullscreen", None)
            if fs is not None:
                fs.on_state(state)
        self.after(0, update)

    def _bar_cover_cb(self, img) -> None:
        try:
            self.after(0, lambda: self.bar_cover.configure(image=img))
        except Exception:
            pass

    def _open_equalizer(self) -> None:
        win = getattr(self, "_eq_window", None)
        if win is not None and win.winfo_exists():
            win.focus()
            return
        self._eq_window = EqualizerWindow(self, self)

    def _open_fullscreen(self) -> None:
        if getattr(self, "_fullscreen", None) is None:
            self._fullscreen = FullscreenPlayer(self)
        self._fullscreen.show()

    def _seek_release(self, _event) -> None:
        self._seek_dragging = False
        length = self.player.get_length()
        if length > 0:
            self.player.seek(self.seek.get() / 100.0 * length)

    def _tick_seek(self) -> None:
        try:
            state = self.player.state
            length = self.player.get_length()
            if state in ("playing", "paused") and length > 0:
                pos = min(self.player.get_pos(), length)
                if not self._seek_dragging:
                    self.seek.set(pos / length * 100.0)
                self.time_pos.configure(text=fmt_duration(pos))
                self.time_len.configure(text=fmt_duration(length))
        except tk.TclError:
            return
        self.after(500, self._tick_seek)

    # ---- пользователь / выход ----

    def _load_user_info(self) -> None:
        if self.api is None:
            self.user_label.configure(text="Гостевой режим")
            return

        def work() -> str:
            u = self.api.users_get()
            return f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()

        self.run_bg(work, on_success=lambda name: self.user_label.configure(
            text=name or f"id{self.config.user_id}"))

    def on_logout(self) -> None:
        self.config.token = ""
        self.config.user_id = 0
        self.config.save()
        self.want_relogin = True
        self._shutdown()

    def _on_close(self) -> None:
        if getattr(self.config, "minimize_to_tray", True):
            # не выходим, а прячем окно в трей — музыка играет дальше
            self.withdraw()
            if not self._tray_notified:
                self._tray_notified = True
                self.tray.notify("Приложение продолжает работать в трее")
            return
        self.quit_from_tray()

    def restore_from_tray(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def quit_from_tray(self) -> None:
        self.config.volume = float(self.vol_slider.get())
        self.config.save()
        self._shutdown()

    def _shutdown(self) -> None:
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.player.shutdown()
        except Exception:
            pass
        self.destroy()
