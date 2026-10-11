"""Эквалайзер: рендер трека с ffmpeg-фильтром equalizer в отдельный файл кеша.

pygame не умеет обрабатывать звук в реальном времени, поэтому при включённом
эквалайзере трек один раз перерендеривается через ffmpeg (цепочка фильтров
equalizer) в cache_dir/eq/ и играется уже этот вариант. Смена настроек EQ
перерендеривает текущий трек и продолжает с той же позиции.
"""
import hashlib
import os
import subprocess
from pathlib import Path

BANDS = [60, 230, 910, 3600, 14000]
BAND_LABELS = ["60 Гц", "230 Гц", "910 Гц", "3.6 кГц", "14 кГц"]

PRESETS = {
    "Плоский": [0.0, 0.0, 0.0, 0.0, 0.0],
    "Басс": [7.0, 5.0, 2.0, 0.0, 0.0],
    "Поп": [-1.0, 2.0, 4.0, 3.0, -1.0],
    "Рок": [4.0, 2.0, -1.0, 2.0, 5.0],
    "Электроника": [6.0, 3.0, -1.0, 3.0, 5.0],
    "Вокал": [-2.0, -1.0, 2.0, 5.0, 4.0],
    "Классика": [3.0, 1.0, -1.0, 2.0, 3.0],
}


def normalize_gains(gains) -> list:
    try:
        values = [max(-12.0, min(12.0, float(g))) for g in (gains or [])]
    except (TypeError, ValueError):
        values = []
    if len(values) != len(BANDS):
        values = [0.0] * len(BANDS)
    return values


def normalize_tempo(tempo) -> float:
    try:
        return max(0.5, min(2.0, float(tempo)))
    except (TypeError, ValueError):
        return 1.0


def normalize_pitch(semitones) -> float:
    try:
        return max(-12.0, min(12.0, float(semitones)))
    except (TypeError, ValueError):
        return 0.0


def is_flat(gains) -> bool:
    return all(abs(g) < 0.5 for g in normalize_gains(gains))


def needs_render(gains, tempo=1.0, semitones=0.0) -> bool:
    return (not is_flat(gains)
            or abs(normalize_tempo(tempo) - 1.0) >= 0.01
            or abs(normalize_pitch(semitones)) >= 0.05)


def build_filter(gains, tempo: float = 1.0, semitones: float = 0.0) -> str:
    parts = []
    for freq, gain in zip(BANDS, normalize_gains(gains)):
        if abs(gain) >= 0.5:
            parts.append(f"equalizer=f={freq}:width_type=q:w=1.2:g={gain:.1f}")
    tempo = normalize_tempo(tempo)
    semitones = normalize_pitch(semitones)
    if abs(semitones) >= 0.05:
        # asetrate меняет и тон, и темп (как пластинка) — темп
        # компенсируем atempo ниже, тон остаётся
        pitch = 2.0 ** (semitones / 12.0)
        parts.append(f"asetrate=44100*{pitch:.6f}")
        parts.append("aresample=44100")
        tempo = tempo / pitch
    if abs(tempo - 1.0) >= 0.01:
        # atempo принимает 0.5..100 — при необходимости цепочкой
        while tempo < 0.5:
            parts.append("atempo=0.5")
            tempo /= 0.5
        while tempo > 100.0:
            parts.append("atempo=100.0")
            tempo /= 100.0
        parts.append(f"atempo={tempo:.4f}")
    return ",".join(parts)


def rendered_path(cache_dir, src: Path, gains, tempo: float = 1.0,
                  semitones: float = 0.0) -> Path:
    try:
        mtime = src.stat().st_mtime_ns
    except OSError:
        mtime = 0
    key = (f"{src}|{mtime}|{','.join(map(str, normalize_gains(gains)))}"
           f"|{normalize_tempo(tempo)}|{normalize_pitch(semitones)}")
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
    d = Path(cache_dir) / "eq"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{digest}.mp3"


def render(ffmpeg: str, src: Path, gains, dest: Path,
           tempo: float = 1.0, semitones: float = 0.0) -> Path:
    """Рендерит src с EQ/скоростью/тоном в dest (переиспользует готовый файл)."""
    chain = build_filter(gains, tempo, semitones)
    if not chain:
        return src
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    cmd = [ffmpeg, "-y", "-i", str(src), "-af", chain,
           "-c:a", "libmp3lame", "-q:a", "2", str(dest)]
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "timeout": 300}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(cmd, **kwargs)
    if proc.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg не смог применить эквалайзер")
    return dest
