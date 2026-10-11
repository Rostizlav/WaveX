"""Текущая тема приложения: тёмная или светлая.

Тема «жидкое стекло» удалена. Модуль и функции сохранены под старым
именем, чтобы не переписывать все места вызовов: is_glass() всегда
False, style_* — безопасные no-op.
"""

_theme = "dark"


def set_theme(theme: str) -> None:
    global _theme
    _theme = theme if theme in ("dark", "light") else "dark"


def is_glass() -> bool:
    return False


class Palette:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


DEFAULT = Palette(
    bg=None,
    bg_alt=None,
    panel=None,
    panel_alt=None,
    border=None,
    text=None,
    text_secondary="gray",
    accent=None,
    accent_text=None,
    accent_hover=None,
    hover=None,
    row=("#dbdbdb", "#2b2b2b"),
    row_playing=("#c9e0f5", "#1f3a52"),
    danger="#a33333",
)

# совместимость со старым кодом, который читает GLASS.* напрямую
GLASS = DEFAULT


def current() -> Palette:
    return DEFAULT


def apply_to_window(window, config) -> None:
    """Вызывается на старте: задаёт базовый режим CTk."""
    import customtkinter as ctk

    set_theme(getattr(config, "theme", "dark"))
    ctk.set_appearance_mode(_theme)


def style_panel(frame) -> None:
    pass


def style_accent_button(btn) -> None:
    pass


def style_ghost_button(btn) -> None:
    pass


def style_entry(entry) -> None:
    pass
