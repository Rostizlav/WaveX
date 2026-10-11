from pathlib import Path
import webbrowser
from tkinter import filedialog, messagebox

import customtkinter as ctk
import requests
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

from ..vk_api import Track
from .widgets import TrackList, smooth_scroll

HEADER_FONT = lambda: ctk.CTkFont(size=22, weight="bold")


def playlist_cover_url(playlist: dict) -> str:
    photo = playlist.get("photo") or {}
    for k in ("photo_600", "photo_300", "photo_270", "photo_135", "photo_68"):
        if photo.get(k):
            return photo[k]
    for thumb in playlist.get("thumbs") or []:
        for k in ("photo_600", "photo_300", "photo_270"):
            if thumb.get(k):
                return thumb[k]
    return ""


def track_from_file(path: Path, idx: int) -> Track:
    title = path.stem
    artist = "Неизвестный исполнитель"
    album = ""
    duration = 0
    try:
        tags = EasyID3(str(path))
        title = tags.get("title", [title])[0]
        artist = tags.get("artist", [artist])[0]
        album = tags.get("album", [""])[0]
    except Exception:
        pass
    try:
        duration = int(MP3(str(path)).info.length)
    except Exception:
        pass
    if artist == "Неизвестный исполнитель" and " - " in path.stem:
        artist, title = path.stem.split(" - ", 1)
    return Track(id=idx + 1, owner_id=0, title=title, artist=artist,
                 duration=duration, album=album, local_path=str(path))


def scan_music_dir(directory: str) -> list:
    root = Path(directory) if directory else None
    if root is None or not root.exists():
        return []
    return [track_from_file(p, i) for i, p in enumerate(sorted(root.rglob("*.mp3")))]


class BaseView(ctk.CTkFrame):
    def __init__(self, master, app, **kw) -> None:
        super().__init__(master, fg_color="transparent", **kw)
        self.app = app

    def on_show(self) -> None:
        pass


class PlaylistCard(ctk.CTkFrame):
    def __init__(self, master, app, playlist: dict, on_open, size: int = 140) -> None:
        super().__init__(master, fg_color="transparent")
        self.playlist = playlist
        self.cover = ctk.CTkLabel(self, text="", width=size, height=size)
        self.cover.pack()
        title_lbl = ctk.CTkLabel(self, text=playlist.get("title", "Плейлист"),
                                 wraplength=size, font=ctk.CTkFont(size=12))
        title_lbl.pack()
        app.cover_manager.get(playlist_cover_url(playlist), size, self._cover_cb)
        for w in (self, self.cover, title_lbl):
            w.bind("<Button-1>", lambda e: on_open(playlist))

    def _cover_cb(self, img) -> None:
        try:
            self.after(0, lambda: self.cover.configure(image=img))
        except Exception:
            pass


class PlaylistGrid(ctk.CTkScrollableFrame):
    COLS = 4

    def __init__(self, master, app, on_open, **kw) -> None:
        super().__init__(master, **kw)
        self.app = app
        self.on_open = on_open
        smooth_scroll(self)
        for c in range(self.COLS):
            self.grid_columnconfigure(c, weight=1, uniform="pl")

    def set_playlists(self, playlists) -> None:
        for w in self.winfo_children():
            w.destroy()
        for i, pl in enumerate(playlists):
            card = PlaylistCard(self, self.app, pl, on_open=self.on_open)
            card.grid(row=i // self.COLS, column=i % self.COLS, padx=6, pady=6, sticky="n")


class PlaylistTracksFrame(ctk.CTkFrame):
    """Оверлей со списком треков плейлиста поверх текущего экрана."""

    def __init__(self, master, app, playlist: dict, kind: str = "playlists", on_back=None) -> None:
        super().__init__(master, fg_color=("gray92", "gray14"))
        self.app = app
        self.playlist = playlist
        self.kind = kind

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        if on_back:
            ctk.CTkButton(top, text="← Назад", width=90, command=on_back).pack(side="left")
        ctk.CTkLabel(top, text=playlist.get("title", "Плейлист"),
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left", padx=10)
        ctk.CTkButton(top, text="Скачать весь плейлист", width=180,
                      command=self._download_all).pack(side="right")

        self.status = ctk.CTkLabel(self, text="Загрузка…")
        self.status.pack(anchor="w", padx=14)
        self.tracklist = TrackList(self, on_play=self._play,
                                   menu_handlers=app.track_menu_handlers(),
                                   cover_manager=app.cover_manager)
        self.tracklist.pack(fill="both", expand=True, padx=12, pady=6)
        self.tracks: list = []
        self._load()

    def _load(self) -> None:
        pl = self.playlist
        self.app.run_bg(
            lambda: self.app.api.get_playlist_tracks(
                pl.get("owner_id", 0), pl.get("id", 0), pl.get("access_key", ""), count=1000),
            on_success=self._on_loaded,
            on_error=lambda e: self.status.configure(text=f"Ошибка: {e}"))

    def _on_loaded(self, res) -> None:
        tracks, total = res
        self.tracks = tracks
        self.tracklist.set_tracks(tracks)
        self.status.configure(text=f"Треков: {total}")

    def _play(self, track) -> None:
        self.app.play_from_list(self.tracks, track, kind=self.kind,
                                playlist_id=self.playlist.get("id"))

    def _download_all(self) -> None:
        if self.tracks:
            self.app.download_tracks(self.tracks, title=self.playlist.get("title", "Плейлист"))


class MyMusicView(BaseView):
    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        ctk.CTkLabel(self, text="Мои аудио", font=HEADER_FONT()).pack(anchor="w", padx=16, pady=(14, 6))
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16)
        ctk.CTkButton(top, text="Обновить", width=100, command=self.reload).pack(side="left")
        ctk.CTkButton(top, text="Мои плейлисты", width=130,
                      command=lambda: app.show_view("playlists")).pack(side="left", padx=8)
        self.status = ctk.CTkLabel(self, text="")
        self.status.pack(anchor="w", padx=16, pady=(4, 0))
        self.tracklist = TrackList(self, on_play=self._play,
                                   menu_handlers=app.track_menu_handlers(),
                                   cover_manager=app.cover_manager)
        self.tracklist.pack(fill="both", expand=True, padx=16, pady=6)
        self.more_btn = ctk.CTkButton(self, text="Загрузить ещё", command=self._load)
        self.tracks: list = []
        self.offset = 0

    def on_show(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.tracks = []
        self.offset = 0
        self.tracklist.clear()
        self.more_btn.pack_forget()
        self._load()

    def _load(self) -> None:
        self.status.configure(text="Загрузка…")
        self.app.run_bg(lambda: self.app.api.get_audios(offset=self.offset, count=100),
                        on_success=self._on_loaded,
                        on_error=lambda e: self.status.configure(text=f"Ошибка: {e}"))

    def _on_loaded(self, res) -> None:
        tracks, total = res
        self.tracks.extend(tracks)
        self.offset += len(tracks)
        self.tracklist.add_tracks(tracks)
        self.status.configure(text=f"Треков: {len(self.tracks)} из {total}")
        if tracks and self.offset < total:
            self.more_btn.pack(pady=4)
        else:
            self.more_btn.pack_forget()

    def _play(self, track) -> None:
        self.app.play_from_list(self.tracks, track)


class SearchView(BaseView):
    SERVICE_NAMES = {
        "ВКонтакте": "vk", "YouTube": "youtube", "SoundCloud": "soundcloud",
        "Яндекс": "yandex", "Spotify": "spotify",
    }

    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        ctk.CTkLabel(self, text="Поиск", font=HEADER_FONT()).pack(anchor="w", padx=16, pady=(14, 6))
        self.service_var = ctk.StringVar(
            value="ВКонтакте" if app.api is not None else "YouTube")
        self.service_seg = ctk.CTkSegmentedButton(
            self, values=list(self.SERVICE_NAMES), variable=self.service_var,
            command=lambda _v: self._service_changed())
        self.service_seg.pack(fill="x", padx=16, pady=(0, 6))
        self.entry = ctk.CTkEntry(self, placeholder_text="Поиск музыки…", height=36)
        self.entry.pack(fill="x", padx=16)
        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Return>", lambda e: self._search())
        self.hints_frame = ctk.CTkFrame(self, fg_color=("gray88", "gray17"))
        self._hints_shown = False
        self._hints_enabled = True
        self._hint_after = None

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=6)
        ctk.CTkButton(row, text="Найти", width=90, command=self._search).pack(side="left")
        self.popular_btn = ctk.CTkButton(row, text="Популярное", width=120,
                                         command=self._popular)
        self.popular_btn.pack(side="left", padx=6)
        self.recommend_btn = ctk.CTkButton(row, text="Рекомендации", width=140,
                                           command=self._recommend)
        self.recommend_btn.pack(side="left")

        ctk.CTkLabel(row, text="Сорт.:", text_color="gray").pack(side="left", padx=(14, 2))
        self.sort_var = ctk.StringVar(value="Без сортировки")
        ctk.CTkOptionMenu(
            row, width=150,
            values=["Без сортировки", "По сервису", "По длительности", "По исполнителю"],
            variable=self.sort_var,
            command=lambda _v: self._render_list()).pack(side="left")

        self.status = ctk.CTkLabel(self, text="")
        self.status.pack(anchor="w", padx=16)
        self.tracklist = TrackList(self, on_play=self._play,
                                   menu_handlers=app.track_menu_handlers(),
                                   cover_manager=app.cover_manager)
        self.tracklist.pack(fill="both", expand=True, padx=16, pady=6)
        self.more_btn = ctk.CTkButton(self, text="Загрузить ещё", command=self._load)
        self.tracks: list = []
        self._mode = "search"  # search | popular | recommendations
        self._query = ""
        self._offset = 0
        self._service_changed()

    def _service(self) -> str:
        return self.SERVICE_NAMES.get(self.service_var.get(), "vk")

    def _service_changed(self) -> None:
        self._hide_hints()
        vk = self._service() == "vk" and self.app.api is not None
        # «Популярное»/«Рекомендации» — только для ВК; у сторонних сервисов
        # кнопок (и сообщений про вход) нет вовсе
        if vk:
            self.popular_btn.pack(side="left", padx=6)
            self.recommend_btn.pack(side="left")
        else:
            self.popular_btn.pack_forget()
            self.recommend_btn.pack_forget()
            self._mode = "search"

    # ---- подсказки ----

    def _on_key(self, event=None) -> None:
        self._hints_enabled = True
        if self._hint_after is not None:
            self.after_cancel(self._hint_after)
            self._hint_after = None
        self._hint_after = self.after(300, self._fetch_hints)

    def _fetch_hints(self) -> None:
        self._hint_after = None
        if self._service() != "vk" or self.app.api is None:
            return
        q = self.entry.get().strip()
        if len(q) < 2:
            self._hide_hints()
            return
        self.app.run_bg(lambda: self.app.api.get_search_hints(q),
                        on_success=lambda hints: self._show_hints(q, hints),
                        on_error=lambda e: self._hide_hints())

    def _show_hints(self, query: str, hints) -> None:
        if not self._hints_enabled or self.entry.get().strip() != query:
            return
        for w in self.hints_frame.winfo_children():
            w.destroy()
        if not hints:
            self._hide_hints()
            return
        for h in hints[:8]:
            ctk.CTkButton(self.hints_frame, text=h, anchor="w", fg_color="transparent",
                          text_color=("gray10", "gray90"),
                          command=lambda s=h: self._apply_hint(s)).pack(fill="x", padx=4, pady=1)
        if not self._hints_shown:
            self.hints_frame.pack(fill="x", padx=16, after=self.entry)
            self._hints_shown = True

    def _hide_hints(self) -> None:
        if self._hints_shown:
            self.hints_frame.pack_forget()
            self._hints_shown = False

    def _apply_hint(self, text: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self._search()

    # ---- поиск ----

    def _search(self) -> None:
        q = self.entry.get().strip()
        if not q:
            return
        self._hints_enabled = False
        self._hide_hints()
        self._mode = "search"
        self._query = q
        self._reset_and_load()

    def _popular(self) -> None:
        if self.app.api is None:
            self.status.configure(text="Требуется вход в ВКонтакте")
            return
        self.service_var.set("ВКонтакте")
        self._hints_enabled = False
        self._hide_hints()
        self._mode = "popular"
        self._reset_and_load()

    def _recommend(self) -> None:
        if self.app.api is None:
            self.status.configure(text="Требуется вход в ВКонтакте")
            return
        self.service_var.set("ВКонтакте")
        self._hints_enabled = False
        self._hide_hints()
        self._mode = "recommendations"
        self._reset_and_load()

    def _reset_and_load(self) -> None:
        self.tracks = []
        self._offset = 0
        self.tracklist.clear()
        self.more_btn.pack_forget()
        self._load()

    def _load(self) -> None:
        service = self._service()
        if service == "vk" and self.app.api is None:
            self.tracklist.clear()
            self.status.configure(
                text="Поиск по каталогу ВК требует входа (выберите другой сервис выше)")
            return
        self.status.configure(text="Загрузка…")
        if service != "vk":
            from .. import services
            # yt-dlp не умеет offset — запрашиваем больший лимит и дедупим
            limit = 50 if not self.tracks else min(len(self.tracks) + 50, 250)
            self.app.run_bg(
                lambda: (services.search(service, self._query, self.app.config,
                                         limit=limit), limit),
                on_success=self._on_loaded,
                on_error=lambda e: self.status.configure(text=f"Ошибка: {e}"))
            return
        if self._mode == "search":
            work = lambda: self.app.api.search(self._query, offset=self._offset, count=100)
        elif self._mode == "popular":
            work = lambda: self.app.api.get_popular(offset=self._offset, count=100)
        else:
            work = lambda: self.app.api.get_recommendations(offset=self._offset, count=100)
        self.app.run_bg(work, on_success=self._on_loaded,
                        on_error=lambda e: self.status.configure(text=f"Ошибка: {e}"))

    def _on_loaded(self, res) -> None:
        tracks, total_or_limit = res
        if self._service() != "vk":
            existing = {t.full_id for t in self.tracks}
            new = [t for t in tracks if t.full_id not in existing]
            self.tracks.extend(new)
            self._render_list()
            self.status.configure(text=f"Найдено: {len(self.tracks)}")
            # «ещё» есть смысл показывать, пока сервис отдаёт полный лимит
            if len(tracks) >= int(total_or_limit) and len(self.tracks) < 250:
                self.more_btn.pack(pady=4)
            else:
                self.more_btn.pack_forget()
            return
        self.tracks.extend(tracks)
        self._offset += len(tracks)
        self._render_list()
        self.status.configure(text=f"Найдено: {len(self.tracks)}")
        if tracks and self._mode in ("search", "popular"):
            self.more_btn.pack(pady=4)
        else:
            self.more_btn.pack_forget()

    def _render_list(self) -> None:
        """Отрисовка списка с учётом выбранной сортировки."""
        mode = self.sort_var.get()
        tracks = self.tracks
        if mode == "По сервису":
            order = {"vk": 0, "youtube": 1, "soundcloud": 2, "yandex": 3, "spotify": 4}
            tracks = sorted(tracks,
                            key=lambda t: order.get(getattr(t, "service", "vk"), 9))
        elif mode == "По длительности":
            tracks = sorted(tracks, key=lambda t: int(t.duration or 0))
        elif mode == "По исполнителю":
            tracks = sorted(tracks, key=lambda t: (t.artist or "").lower())
        self._display = list(tracks)
        self.tracklist.set_tracks(self._display)

    def _play(self, track) -> None:
        self.app.play_from_list(getattr(self, "_display", None) or self.tracks, track)


def _parse_sections(catalog: dict) -> list:
    out = []
    for sec in (catalog.get("sections") or []):
        title = sec.get("title") or sec.get("id", "")
        sec_id = sec.get("id", "")
        playlists = []
        for block in sec.get("blocks") or []:
            playlists.extend(block.get("playlists") or [])
        if not playlists:
            playlists.extend(sec.get("playlists") or [])
        if playlists:
            out.append((title, playlists, sec_id))
    return out


class CatalogView(BaseView):
    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        ctk.CTkLabel(self, text="Каталог", font=HEADER_FONT()).pack(anchor="w", padx=16, pady=(14, 6))
        self.status = ctk.CTkLabel(self, text="")
        self.status.pack(anchor="w", padx=16)
        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=12, pady=6)
        smooth_scroll(self.scroll)

    def on_show(self) -> None:
        self._load()

    def _load(self) -> None:
        self.status.configure(text="Загрузка…")
        self.app.run_bg(lambda: self.app.api.get_catalog(), on_success=self._render,
                        on_error=lambda e: self.status.configure(text=f"Ошибка: {e}"))

    def _render(self, catalog) -> None:
        for w in self.scroll.winfo_children():
            w.destroy()
        sections = _parse_sections(catalog)
        self.status.configure(text="" if sections else "Каталог пуст")
        for title, playlists, _sec_id in sections:
            ctk.CTkLabel(self.scroll, text=title,
                         font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=8, pady=(10, 2))
            strip = ctk.CTkScrollableFrame(self.scroll, orientation="horizontal", height=200)
            strip.pack(fill="x", padx=4, pady=2)
            for pl in playlists[:20]:
                PlaylistCard(strip, self.app, pl, on_open=self._open_playlist).pack(
                    side="left", padx=6, pady=4)

    def _open_playlist(self, playlist) -> None:
        overlay = PlaylistTracksFrame(self, self.app, playlist, kind="playlists",
                                      on_back=lambda: overlay.destroy())
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)


class PlaylistsView(BaseView):
    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        ctk.CTkLabel(self, text="Плейлисты", font=HEADER_FONT()).pack(anchor="w", padx=16, pady=(14, 6))
        self.status = ctk.CTkLabel(self, text="")
        self.status.pack(anchor="w", padx=16)
        self.grid = PlaylistGrid(self, app, on_open=self._open_playlist)
        self.grid.pack(fill="both", expand=True, padx=12, pady=6)

    def on_show(self) -> None:
        self._load()

    def _load(self) -> None:
        self.status.configure(text="Загрузка…")
        self.app.run_bg(lambda: self.app.api.get_playlists(self.app.config.user_id),
                        on_success=self._render,
                        on_error=lambda e: self.status.configure(text=f"Ошибка: {e}"))

    def _render(self, res) -> None:
        playlists, total = res
        self.status.configure(text=f"Плейлистов: {total}")
        self.grid.set_playlists(playlists)

    def _open_playlist(self, playlist) -> None:
        overlay = PlaylistTracksFrame(self, self.app, playlist, kind="playlists",
                                      on_back=lambda: overlay.destroy())
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)


class PodcastsView(BaseView):
    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        ctk.CTkLabel(self, text="Подкасты", font=HEADER_FONT()).pack(anchor="w", padx=16, pady=(14, 6))
        self.status = ctk.CTkLabel(self, text="")
        self.status.pack(anchor="w", padx=16)
        self.grid = PlaylistGrid(self, app, on_open=self._open_playlist)
        self.grid.pack(fill="both", expand=True, padx=12, pady=6)

    def on_show(self) -> None:
        self.status.configure(text="Загрузка…")
        self.app.run_bg(lambda: self.app.api.get_catalog(), on_success=self._render,
                        on_error=lambda e: self.status.configure(text=f"Ошибка: {e}"))

    def _render(self, catalog) -> None:
        playlists = []
        for title, pls, sec_id in _parse_sections(catalog):
            if "podcast" in sec_id.lower() or "подкаст" in title.lower():
                playlists.extend(pls)
        self.status.configure(text="" if playlists else "Раздел подкастов не найден в каталоге")
        self.grid.set_playlists(playlists)

    def _open_playlist(self, playlist) -> None:
        overlay = PlaylistTracksFrame(self, self.app, playlist, kind="podcasts",
                                      on_back=lambda: overlay.destroy())
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)


class DownloadsView(BaseView):
    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        ctk.CTkLabel(self, text="Загрузки", font=HEADER_FONT()).pack(anchor="w", padx=16, pady=(14, 6))
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16)
        ctk.CTkButton(top, text="Обновить", width=100, command=self.reload).pack(side="left")
        ctk.CTkLabel(top, text=app.config.download_dir, text_color="gray").pack(side="left", padx=10)
        self.status = ctk.CTkLabel(self, text="")
        self.status.pack(anchor="w", padx=16)
        self.tracklist = TrackList(self, on_play=self._play,
                                   menu_handlers=app.track_menu_handlers(),
                                   cover_manager=app.cover_manager)
        self.tracklist.pack(fill="both", expand=True, padx=16, pady=6)
        self.tracks: list = []

    def on_show(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.status.configure(text="Сканирование…")
        self.app.run_bg(lambda: scan_music_dir(self.app.config.download_dir),
                        on_success=self._render,
                        on_error=lambda e: self.status.configure(text=f"Ошибка: {e}"))

    def _render(self, tracks) -> None:
        self.tracks = tracks
        self.tracklist.set_tracks(tracks)
        self.status.configure(text=f"Файлов: {len(tracks)}")

    def _play(self, track) -> None:
        self.app.play_from_list(self.tracks, track)


# Ссылка на страницу поддержки автора (донат).
SUPPORT_URL = "https://www.donationalerts.com/r/rostizlav"


class SupportView(BaseView):
    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        box = ctk.CTkFrame(self, corner_radius=14)
        box.pack(expand=True, padx=40, pady=40)
        ctk.CTkLabel(box, text="Поддержать автора",
                     font=HEADER_FONT()).pack(padx=30, pady=(26, 8))
        ctk.CTkLabel(
            box,
            text="WaveX — бесплатное приложение без рекламы.\n"
                 "Если оно вам нравится, вы можете поддержать автора —\n"
                 "это помогает развивать проект и выпускать обновления.",
            justify="center").pack(padx=30, pady=6)
        ctk.CTkButton(box, text="Открыть страницу поддержки", width=260, height=38,
                      command=lambda: webbrowser.open(SUPPORT_URL)).pack(pady=(12, 4))
        ctk.CTkButton(box, text="Скопировать ссылку", width=260,
                      fg_color="transparent", border_width=1,
                      command=self._copy).pack(pady=(0, 8))
        self.copied = ctk.CTkLabel(box, text="", text_color="gray",
                                   font=ctk.CTkFont(size=11))
        self.copied.pack(pady=(0, 22))

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(SUPPORT_URL)
        self.copied.configure(text="Ссылка скопирована")


class SettingsView(BaseView):
    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        cfg = app.config
        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=16, pady=12)
        smooth_scroll(scroll)
        ctk.CTkLabel(scroll, text="Настройки", font=HEADER_FONT()).pack(anchor="w", pady=(2, 12))

        row = self._row(scroll, "Тема")
        theme_names = {"dark": "Тёмная", "light": "Светлая"}
        self._theme_ids = {v: k for k, v in theme_names.items()}
        self.theme_var = ctk.StringVar(value=theme_names.get(cfg.theme, "Тёмная"))
        ctk.CTkOptionMenu(row, values=list(theme_names.values()), variable=self.theme_var,
                          command=self._set_theme).pack(side="left")

        row = self._row(scroll, "Прокси (http:// / socks5://)")
        self.proxy_entry = ctk.CTkEntry(row, width=260, placeholder_text="socks5://127.0.0.1:9050")
        self.proxy_entry.pack(side="left")
        if cfg.proxy:
            self.proxy_entry.insert(0, cfg.proxy)
        ctk.CTkButton(row, text="Сохранить", width=90, command=self._save_proxy).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Проверить", width=90, command=self._test_proxy).pack(side="left")
        self.proxy_status = ctk.CTkLabel(scroll, text=self._proxy_status_text(), anchor="w", text_color="gray")
        self.proxy_status.pack(fill="x", pady=(0, 6))

        row = self._row(scroll, "Автоподбор прокси")
        self.auto_proxy_var = ctk.BooleanVar(value=bool(cfg.auto_proxy))
        ctk.CTkCheckBox(row, text="Включён",
                        variable=self.auto_proxy_var,
                        command=self._save_auto_proxy).pack(side="left")
        ctk.CTkButton(row, text="Подобрать сейчас", width=150,
                      command=self._auto_proxy_now).pack(side="left", padx=6)

        self.cache_entry = self._dir_row(scroll, "Папка кеша", cfg.cache_dir, self._choose_cache)
        self.download_entry = self._dir_row(scroll, "Папка загрузок", cfg.download_dir, self._choose_download)

        ctk.CTkLabel(scroll, text="Сервисы", font=ctk.CTkFont(size=16, weight="bold")
                     ).pack(anchor="w", pady=(16, 2))
        ctk.CTkLabel(
            scroll,
            text="Яндекс Музыка: откройте в браузере\n"
                 "oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d\n"
                 "и вставьте токен ниже.",
            text_color="gray", font=ctk.CTkFont(size=11), justify="left").pack(anchor="w")
        row = self._row(scroll, "Токен Яндекс Музыки")
        self.yandex_entry = ctk.CTkEntry(row, width=260, show="•")
        self.yandex_entry.pack(side="left")
        if cfg.yandex_token:
            self.yandex_entry.insert(0, cfg.yandex_token)
        ctk.CTkButton(row, text="Сохранить", width=90,
                      command=self._save_yandex).pack(side="left", padx=6)

        ctk.CTkLabel(
            scroll,
            text="Spotify: создайте приложение на developer.spotify.com/dashboard\n"
                 "и вставьте Client ID и Client Secret (полные треки недоступны без Premium —\n"
                 "играется 30-сек превью или соответствие с YouTube).",
            text_color="gray", font=ctk.CTkFont(size=11), justify="left").pack(anchor="w", pady=(8, 0))
        row = self._row(scroll, "Spotify Client ID / Secret")
        self.spotify_id_entry = ctk.CTkEntry(row, width=130, placeholder_text="Client ID")
        self.spotify_id_entry.pack(side="left")
        if cfg.spotify_client_id:
            self.spotify_id_entry.insert(0, cfg.spotify_client_id)
        self.spotify_secret_entry = ctk.CTkEntry(row, width=130, placeholder_text="Secret", show="•")
        self.spotify_secret_entry.pack(side="left", padx=4)
        if cfg.spotify_client_secret:
            self.spotify_secret_entry.insert(0, cfg.spotify_client_secret)
        ctk.CTkButton(row, text="Сохранить", width=90,
                      command=self._save_spotify).pack(side="left", padx=6)

        self.hires_var = ctk.BooleanVar(value=bool(cfg.hires_covers))
        ctk.CTkCheckBox(scroll, text="Обложки в высоком разрешении (до 1200×1200)",
                        variable=self.hires_var, command=self._save_hires).pack(anchor="w", pady=6)

        self.tray_var = ctk.BooleanVar(value=bool(getattr(cfg, "minimize_to_tray", True)))
        ctk.CTkCheckBox(scroll, text="Сворачивать в трей при закрытии (музыка играет дальше)",
                        variable=self.tray_var, command=self._save_tray).pack(anchor="w", pady=6)

        row = self._row(scroll, "Громкость")
        vol = ctk.CTkSlider(row, from_=0, to=1, width=220, command=self._set_volume)
        vol.set(max(0.0, min(1.0, float(cfg.volume))))
        vol.pack(side="left")

        row = self._row(scroll, "Кеш")
        self.cache_size_lbl = ctk.CTkLabel(row, text="…", text_color="gray")
        self.cache_size_lbl.pack(side="left")
        ctk.CTkButton(row, text="Очистить кеш", width=130, command=self._clear_cache).pack(side="left", padx=10)
        self._update_cache_size()

        row = self._row(scroll, "ffmpeg")
        self.ffmpeg_status = ctk.CTkLabel(row, text=self._ffmpeg_text(), text_color="gray")
        self.ffmpeg_status.pack(side="left")
        ctk.CTkButton(row, text="Установить ffmpeg", width=160,
                      command=self._install_ffmpeg).pack(side="left", padx=10)

        if app.api is None:
            login_btn = ctk.CTkButton(scroll, text="Войти в аккаунт ВК",
                                      command=lambda: app.on_logout())
            login_btn.pack(anchor="w", pady=18)
        else:
            ctk.CTkButton(scroll, text="Выйти из аккаунта", fg_color="#a33333",
                          hover_color="#c44444", command=self._logout).pack(anchor="w", pady=18)

    @staticmethod
    def _row(parent, label: str) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkLabel(row, text=label, width=220, anchor="w").pack(side="left")
        return row

    def _dir_row(self, parent, label: str, value: str, command) -> ctk.CTkEntry:
        row = self._row(parent, label)
        entry = ctk.CTkEntry(row, width=340)
        entry.pack(side="left")
        if value:
            entry.insert(0, value)
        ctk.CTkButton(row, text="Выбрать…", width=90, command=command).pack(side="left", padx=6)
        return entry

    @staticmethod
    def _replace_entry(entry: ctk.CTkEntry, value: str) -> None:
        entry.delete(0, "end")
        entry.insert(0, value)

    # ---- обработчики ----

    def _set_theme(self, choice: str) -> None:
        mode = self._theme_ids.get(choice, "dark")
        self.app.config.theme = mode
        self.app.config.save()
        ctk.set_appearance_mode(mode)

    def _save_proxy(self) -> None:
        self.app.config.proxy = self.proxy_entry.get().strip()
        self.app.config.save()
        self.proxy_status.configure(text="Сохранено. " + self._proxy_status_text())

    def _proxy_status_text(self) -> str:
        cfg = self.app.config
        active = cfg.current_proxy_str()
        if active and (cfg.proxy or "").strip():
            return f"Активен ручной прокси: {active}"
        if active:
            return f"Активен автоподобранный прокси: {active}"
        pm = getattr(cfg, "proxy_manager", None)
        if cfg.auto_proxy and pm is not None:
            return ("Автоподбор включён, живых прокси в базе пока нет — "
                    "нажмите «Подобрать сейчас»")
        return "Прокси не используется"

    def _save_auto_proxy(self) -> None:
        cfg = self.app.config
        cfg.auto_proxy = bool(self.auto_proxy_var.get())
        cfg.save()
        self.proxy_status.configure(text=self._proxy_status_text())
        if cfg.auto_proxy and not (cfg.proxy or "").strip() \
                and cfg.proxy_manager.best_proxy() is None:
            self._auto_proxy_now()

    def _auto_proxy_now(self) -> None:
        cfg = self.app.config
        self.proxy_status.configure(text="Подбор прокси: загрузка списков…")

        def progress(msg: str) -> None:
            self.app.after(0, lambda m=msg: self.proxy_status.configure(
                text="Подбор прокси: " + m))

        self.app.run_bg(
            lambda: cfg.proxy_manager.refresh(on_progress=progress),
            on_success=self._auto_proxy_done,
            on_error=lambda e: self.proxy_status.configure(text=f"Ошибка подбора: {e}"))

    def _auto_proxy_done(self, summary: dict) -> None:
        cfg = self.app.config
        cfg.auto_proxy = True
        cfg.save()
        self.auto_proxy_var.set(True)
        best = summary.get("best") or "—"
        self.proxy_status.configure(
            text=f"Живых прокси: {summary.get('alive', 0)} из "
                 f"{summary.get('tested', 0)}. Выбран: {best}")

    def _save_yandex(self) -> None:
        self.app.config.yandex_token = self.yandex_entry.get().strip()
        self.app.config.save()

    def _save_spotify(self) -> None:
        self.app.config.spotify_client_id = self.spotify_id_entry.get().strip()
        self.app.config.spotify_client_secret = self.spotify_secret_entry.get().strip()
        self.app.config.save()

    def _test_proxy(self) -> None:
        proxy = self.proxy_entry.get().strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None

        def work():
            r = requests.get("https://api.vk.com/method/users.get",
                             params={"v": "5.131"}, timeout=10, proxies=proxies)
            return r.status_code

        self.proxy_status.configure(text="Проверка…")
        self.app.run_bg(work,
                        on_success=lambda code: self.proxy_status.configure(text=f"OK (HTTP {code})"),
                        on_error=lambda e: self.proxy_status.configure(text=f"Ошибка: {e}"))

    def _choose_cache(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.app.cache.change_root(d)
            self._replace_entry(self.cache_entry, d)
            self._update_cache_size()

    def _choose_download(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.app.config.download_dir = d
            self.app.config.save()
            self._replace_entry(self.download_entry, d)

    def _save_hires(self) -> None:
        self.app.config.hires_covers = bool(self.hires_var.get())
        self.app.config.save()

    def _save_tray(self) -> None:
        self.app.config.minimize_to_tray = bool(self.tray_var.get())
        self.app.config.save()

    def _set_volume(self, v) -> None:
        self.app.player.set_volume(float(v))
        self.app.config.save()

    @staticmethod
    def _fmt_size(size: int) -> str:
        if size >= 1 << 30:
            return f"{size / (1 << 30):.2f} ГБ"
        return f"{size / (1 << 20):.1f} МБ"

    def _update_cache_size(self) -> None:
        self.cache_size_lbl.configure(text="…")
        self.app.run_bg(lambda: self.app.cache.size(),
                        on_success=lambda b: self.cache_size_lbl.configure(text=self._fmt_size(b)))

    def _ffmpeg_text(self) -> str:
        return ("установлен" if self.app.downloader._find_ffmpeg()
                else "не найден (нужен для YouTube/SoundCloud и части треков ВК)")

    def _install_ffmpeg(self) -> None:
        self.ffmpeg_status.configure(text="Скачивание… 0%")

        def progress(done, total) -> None:
            frac = (done / total) if total else 0
            self.app.after(0, lambda: self.ffmpeg_status.configure(
                text=f"Скачивание… {frac:.0%}"))

        def work() -> None:
            self.app.downloader.download_ffmpeg(progress_cb=progress)

        def done(_=None) -> None:
            self.ffmpeg_status.configure(text=self._ffmpeg_text())

        self.app.run_bg(work, on_success=done,
                        on_error=lambda e: self.ffmpeg_status.configure(text=f"Ошибка: {e}"))

    def _clear_cache(self) -> None:
        if not messagebox.askyesno("WaveX", "Удалить все кешированные треки?"):
            return
        # останавливаем плеер и отпускаем файл — иначе играющий трек
        # заблокирован и не удаляется
        self.app.player.stop()

        def done(failed: int) -> None:
            self._update_cache_size()
            if failed:
                self.cache_size_lbl.configure(
                    text=f"не удалось удалить файлов: {failed} (заняты)")

        self.app.run_bg(lambda: self.app.cache.clear(), on_success=done)

    def _logout(self) -> None:
        if messagebox.askyesno("WaveX", "Выйти из аккаунта ВКонтакте?"):
            self.app.on_logout()
