"""Полноэкранный режим «Сейчас играет»: большая обложка слева, справа
название, прогресс, управление; снизу — текст песни, громкость, эквалайзер."""
import customtkinter as ctk

from .widgets import fmt_duration


class FullscreenPlayer(ctk.CTkFrame):
    def __init__(self, app) -> None:
        super().__init__(app, fg_color=("gray92", "gray10"))
        self.app = app
        self._dragging = False
        self._tick_after = None

        # ---- левая часть: большая обложка ----
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.pack(side="left", fill="both", padx=(30, 10), pady=30)
        self.cover = ctk.CTkLabel(left, text="")
        self.cover.pack(expand=True)

        # ---- правая часть ----
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(10, 30), pady=30)

        top = ctk.CTkFrame(right, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="").pack(side="left", expand=True)
        ctk.CTkButton(top, text="✕ Выход", width=90, fg_color="transparent",
                      border_width=1, command=self.hide).pack(side="right")

        self.title_lbl = ctk.CTkLabel(
            right, text="", anchor="w", justify="left", wraplength=520,
            font=ctk.CTkFont(size=28, weight="bold"))
        self.title_lbl.pack(fill="x", pady=(16, 2))
        self.artist_lbl = ctk.CTkLabel(
            right, text="", anchor="w", justify="left", wraplength=520,
            text_color="gray", font=ctk.CTkFont(size=18))
        self.artist_lbl.pack(fill="x")

        # ---- прогресс ----
        progress = ctk.CTkFrame(right, fg_color="transparent")
        progress.pack(fill="x", pady=(28, 4))
        self.time_pos = ctk.CTkLabel(progress, text="0:00", width=48,
                                     font=ctk.CTkFont(size=13))
        self.time_pos.pack(side="left")
        self.seek = ctk.CTkSlider(progress, from_=0, to=100, number_of_steps=1000,
                                  height=20)
        self.seek.pack(side="left", fill="x", expand=True, padx=8)
        self.seek.bind("<ButtonPress-1>", lambda e: setattr(self, "_dragging", True))
        self.seek.bind("<ButtonRelease-1>", self._seek_release)
        self.time_len = ctk.CTkLabel(progress, text="0:00", width=48,
                                     font=ctk.CTkFont(size=13))
        self.time_len.pack(side="left")

        # ---- управление ----
        controls = ctk.CTkFrame(right, fg_color="transparent")
        controls.pack(pady=12)
        ctk.CTkButton(controls, text="⏮", width=56, height=44,
                      fg_color="transparent", font=ctk.CTkFont(size=18),
                      command=self.app.player.prev).pack(side="left", padx=8)
        self.play_btn = ctk.CTkButton(controls, text="▶", width=72, height=52,
                                      font=ctk.CTkFont(size=20),
                                      command=self.app._toggle_play)
        self.play_btn.pack(side="left", padx=8)
        ctk.CTkButton(controls, text="⏭", width=56, height=44,
                      fg_color="transparent", font=ctk.CTkFont(size=18),
                      command=self.app.player.next).pack(side="left", padx=8)

        # ---- нижний ряд: текст, громкость, эквалайзер ----
        bottom = ctk.CTkFrame(right, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", pady=(8, 0))
        ctk.CTkButton(bottom, text="🎵 Текст", width=100, fg_color="transparent",
                      border_width=1, command=self._lyrics).pack(side="left")
        ctk.CTkLabel(bottom, text="🔊").pack(side="left", padx=(24, 4))
        vol = ctk.CTkSlider(bottom, from_=0, to=1, width=180,
                            command=lambda v: self.app.player.set_volume(v))
        vol.set(max(0.0, min(1.0, float(self.app.config.volume))))
        vol.pack(side="left")
        ctk.CTkButton(bottom, text="Эквалайзер", width=110, fg_color="transparent",
                      border_width=1,
                      command=self.app._open_equalizer).pack(side="right")

    # ---- показ/скрытие ----

    def show(self) -> None:
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()
        self.refresh()
        self.app.bind("<Escape>", lambda e: self.hide())
        self._tick()

    def hide(self) -> None:
        if self._tick_after is not None:
            try:
                self.after_cancel(self._tick_after)
            except Exception:
                pass
            self._tick_after = None
        try:
            self.app.unbind("<Escape>")
        except Exception:
            pass
        self.place_forget()

    # ---- обновление состояния ----

    def refresh(self) -> None:
        """Перечитать текущий трек/состояние из плеера."""
        track = self.app.player.current
        state = self.app.player.state
        self.play_btn.configure(text="⏸" if state == "playing" else "▶")
        size = max(280, min(520, self.app.winfo_height() - 140))
        if track is None:
            self.title_lbl.configure(text="Ничего не играет")
            self.artist_lbl.configure(text="")
            self.app.cover_manager.get("", size, self._cover_cb)
            return
        self.title_lbl.configure(text=track.title or "")
        self.artist_lbl.configure(text=track.artist or "")
        self.app.cover_manager.get(track.cover_url or "", size, self._cover_cb,
                                   fallback=f"{track.artist} — {track.title}")

    def _cover_cb(self, img) -> None:
        try:
            self.after(0, lambda: self.cover.configure(image=img))
        except Exception:
            pass

    def on_state(self, state: str) -> None:
        """Вызывается из MainApp при смене состояния плеера."""
        if not self.winfo_ismapped():
            return
        self.after(0, self.refresh)

    def _lyrics(self) -> None:
        track = self.app.player.current
        if track is not None:
            self.app._show_lyrics(track)

    def _seek_release(self, _event) -> None:
        self._dragging = False
        length = self.app.player.get_length()
        if length > 0:
            self.app.player.seek(self.seek.get() / 100.0 * length)

    def _tick(self) -> None:
        try:
            player = self.app.player
            length = player.get_length()
            if player.state in ("playing", "paused") and length > 0:
                pos = min(player.get_pos(), length)
                if not self._dragging:
                    self.seek.set(pos / length * 100.0)
                self.time_pos.configure(text=fmt_duration(pos))
                self.time_len.configure(text=fmt_duration(length))
        except Exception:
            pass
        self._tick_after = self.after(500, self._tick)
