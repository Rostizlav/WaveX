"""Иконка приложения в системном трее (pystray)."""
import threading

import pystray
from PIL import Image, ImageDraw


def make_icon_image(size: int = 64) -> Image.Image:
    """Простая иконка: синий круг со «звуковой волной»."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=(31, 119, 180, 255))
    bars = (0.34, 0.62, 0.9, 0.66, 0.4)
    bw = max(2, size // 12)
    total_w = bw * (2 * len(bars) - 1)
    x = (size - total_w) // 2
    for h in bars:
        bh = int(size * h * 0.62)
        y0 = (size - bh) // 2
        d.rounded_rectangle((x, y0, x + bw, y0 + bh), radius=bw // 2,
                            fill=(255, 255, 255, 255))
        x += bw * 2
    return img


class TrayIcon:
    """Трей-иконка с меню «Показать» / «Выход». Двойной клик — показать."""

    def __init__(self, app, title: str = "WaveX") -> None:
        self.app = app
        self._icon = pystray.Icon(
            "wavex",
            make_icon_image(),
            title,
            menu=pystray.Menu(
                pystray.MenuItem("Показать", self._show, default=True),
                pystray.MenuItem("Выход", self._quit),
            ),
        )
        self._thread = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            pass

    def notify(self, text: str, title: str = "WaveX") -> None:
        try:
            self._icon.notify(text, title)
        except Exception:
            pass

    def _show(self, icon=None, item=None) -> None:
        self.app.after(0, self.app.restore_from_tray)

    def _quit(self, icon=None, item=None) -> None:
        self.app.after(0, self.app.quit_from_tray)
