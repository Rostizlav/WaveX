"""Реестр сторонних музыкальных сервисов."""
from . import soundcloud, spotify, yandex, youtube

SERVICES = {
    "youtube": youtube,
    "soundcloud": soundcloud,
    "yandex": yandex,
    "spotify": spotify,
}


def search(service: str, query: str, config, limit: int = 25):
    mod = SERVICES[service]
    return mod.search(query, config, limit)


def resolve_stream_url(track, config) -> str:
    """Прямая ссылка на аудиопоток для трека стороннего сервиса."""
    mod = SERVICES.get(track.service)
    if mod is None:
        raise RuntimeError(f"Неизвестный сервис: {track.service}")
    return mod.resolve(track, config)


def resolve_any_url(track, config) -> str:
    """Ссылка на поток для трека, у которого нет url.

    Сторонние сервисы — обычный resolve. Для ВК-треков без ссылки
    (лицензия, подписка, регион) подбираем такой же трек на
    YouTube/SoundCloud по «исполнитель + название» и длительности.
    """
    if track.service in SERVICES:
        return resolve_stream_url(track, config)
    return _external_fallback(track, config)


def _best_match(track, results):
    if not results:
        return None
    dur = int(track.duration or 0)
    if dur > 0:
        close = [r for r in results if abs(int(r.duration or 0) - dur) <= 15]
        if close:
            return close[0]
    return results[0]


def _external_fallback(track, config) -> str:
    query = f"{track.artist} {track.title}".strip()
    last_exc: Exception = RuntimeError(
        "Трек недоступен: ни ВК, ни запасные источники не дали ссылку")
    for service in ("youtube", "soundcloud"):
        try:
            results = search(service, query, config, limit=5)
        except Exception as e:
            last_exc = e
            continue
        cand = _best_match(track, results)
        if cand is None:
            continue
        try:
            return resolve_stream_url(cand, config)
        except Exception as e:
            last_exc = e
    raise RuntimeError(
        f"ВК не отдал ссылку, запасной источник тоже не сработал: {last_exc}")
