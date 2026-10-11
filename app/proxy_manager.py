"""Автоподбор прокси: сбор из публичных источников, проверка доступности ВК
и база «долгоживущих» прокси (предпочтение тем, что стабильно живут
от обновления к обновлению)."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import requests

TEST_URL = "https://api.vk.com/method/users.get"
TEST_PARAMS = {"v": "5.131"}
TEST_TIMEOUT = 7
MAX_WORKERS = 48
MAX_TESTS = 350
DB_MAX_RECORDS = 500
ALIVE_TTL = 2 * 86400   # прокси считается живым 2 суток после последней удачи
STALE_TTL = 6 * 3600    # фоновое обновление базы не чаще, чем раз в 6 часов

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WaveX/1.0"}


def _fetch_geonode() -> list:
    """GeoNode отдаёт JSON с процентом аптайма — лучший источник
    для отбора долгоживущих прокси."""
    out = []
    try:
        r = requests.get(
            "https://proxylist.geonode.com/api/proxy-list",
            params={"limit": 500, "page": 1,
                    "sort_by": "upTime", "sort_type": "desc"},
            headers=_UA, timeout=25)
        data = r.json().get("data") or []
    except Exception:
        return out
    for item in data:
        ip, port = item.get("ip"), str(item.get("port") or "")
        if not ip or not port:
            continue
        uptime = float(item.get("upTime") or 0)
        protocols = [str(p).lower() for p in (item.get("protocols") or [])]
        if any(p in ("http", "https") for p in protocols):
            out.append(("http", f"{ip}:{port}", uptime))
        if "socks5" in protocols:
            out.append(("socks5", f"{ip}:{port}", uptime))
    return out


def _fetch_proxyscrape(protocol: str) -> list:
    try:
        r = requests.get(
            "https://api.proxyscrape.com/v2/",
            params={"request": "displayproxies", "protocol": protocol,
                    "timeout": 10000, "country": "all",
                    "ssl": "all", "anonymity": "all"},
            headers=_UA, timeout=25)
        text = r.text
    except Exception:
        return []
    return [(protocol, line.strip(), 0.0)
            for line in text.splitlines() if ":" in line.strip()]


def _fetch_proxy_list_download(kind: str) -> list:
    try:
        r = requests.get("https://www.proxy-list.download/api/v1/get",
                         params={"type": kind}, headers=_UA, timeout=25)
        text = r.text
    except Exception:
        return []
    return [(kind, line.strip(), 0.0)
            for line in text.splitlines() if ":" in line.strip()]


def _test_proxy(proxy_url: str) -> Optional[int]:
    """Возвращает задержку в мс, если через прокси достучались до API ВК."""
    start = time.monotonic()
    try:
        r = requests.get(TEST_URL, params=TEST_PARAMS, timeout=TEST_TIMEOUT,
                         proxies={"http": proxy_url, "https": proxy_url})
    except requests.exceptions.RequestException:
        return None
    if r.status_code != 200:
        return None
    return int((time.monotonic() - start) * 1000)


class ProxyManager:
    def __init__(self, config_dir: Path) -> None:
        self._path = Path(config_dir) / "proxies.json"
        self._lock = threading.Lock()
        self._db: dict = {}
        self._last_refresh = 0.0
        self._load()

    # ---- база ----

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._db = data.get("proxies") or {}
                self._last_refresh = float(data.get("last_refresh") or 0)
        except Exception:
            self._db = {}

    def _save(self) -> None:
        try:
            payload = {"last_refresh": self._last_refresh, "proxies": self._db}
            self._path.write_text(json.dumps(payload, ensure_ascii=False),
                                  encoding="utf-8")
        except OSError as e:
            print(f"[proxy] не удалось сохранить базу: {e}")

    @staticmethod
    def _score(rec: dict, now: float) -> float:
        # приоритет — долгоживущим: возраст записи, история удач и аптайм
        # из источника важнее скорости
        age_days = min((now - rec.get("first_seen", now)) / 86400.0, 30.0)
        return (rec.get("successes", 0)
                - rec.get("failures", 0) * 2
                + age_days * 2
                + rec.get("uptime", 0.0) / 10.0
                - rec.get("latency_ms", 8000) / 2000.0)

    # ---- выбор ----

    def best_proxy(self) -> Optional[str]:
        now = time.time()
        with self._lock:
            alive = [(k, r) for k, r in self._db.items()
                     if now - r.get("last_ok", 0) < ALIVE_TTL
                     and r.get("successes", 0) > 0]
            if not alive:
                return None
            key, _ = max(alive, key=lambda kv: self._score(kv[1], now))
            return key

    def report_success(self, proxy_url: str, elapsed_sec: float) -> None:
        with self._lock:
            rec = self._db.get(proxy_url)
            if rec is None:
                return
            rec["successes"] = rec.get("successes", 0) + 1
            rec["last_ok"] = time.time()
            rec["latency_ms"] = int(elapsed_sec * 1000)

    def report_failure(self, proxy_url: str) -> None:
        with self._lock:
            rec = self._db.get(proxy_url)
            if rec is None:
                return
            rec["failures"] = rec.get("failures", 0) + 1
            self._save()

    def alive_count(self) -> int:
        now = time.time()
        with self._lock:
            return sum(1 for r in self._db.values()
                       if now - r.get("last_ok", 0) < ALIVE_TTL
                       and r.get("successes", 0) > 0)

    # ---- обновление ----

    def ensure_fresh(self) -> None:
        """Фоновая проверка: обновляет базу, если живых прокси нет
        или данные устарели."""
        if self.best_proxy() is not None \
                and time.time() - self._last_refresh < STALE_TTL:
            return
        try:
            self.refresh()
        except Exception as e:
            print(f"[proxy] фоновое обновление: {e}")

    def refresh(self, on_progress: Optional[Callable] = None,
                max_tests: int = MAX_TESTS) -> dict:
        def progress(msg: str) -> None:
            if on_progress:
                try:
                    on_progress(msg)
                except Exception:
                    pass

        progress("Загрузка списков прокси…")
        fetched: dict = {}
        for scheme, addr, uptime in (
                _fetch_geonode()
                + _fetch_proxyscrape("http")
                + _fetch_proxyscrape("socks5")
                + _fetch_proxy_list_download("http")
                + _fetch_proxy_list_download("socks5")):
            key = f"{scheme}://{addr}"
            if key not in fetched:
                fetched[key] = uptime

        now = time.time()
        with self._lock:
            # сначала перепроверяем уже известные живые — так база
            # долгоживущих прокси накапливает историю
            known = sorted(
                self._db.items(),
                key=lambda kv: self._score(kv[1], now), reverse=True)
        candidates = [k for k, _ in known[:150]]
        candidates += [k for k in fetched if k not in self._db]
        candidates = candidates[:max_tests]

        total = len(candidates)
        alive = 0
        done = 0
        progress(f"Проверка {total} прокси…")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_test_proxy, key): key
                       for key in candidates}
            for fut in as_completed(futures):
                key = futures[fut]
                try:
                    latency = fut.result()
                except Exception:
                    latency = None
                done += 1
                ts = time.time()
                with self._lock:
                    rec = self._db.setdefault(key, {
                        "first_seen": ts, "last_ok": 0,
                        "successes": 0, "failures": 0,
                        "latency_ms": 0, "uptime": fetched.get(key, 0.0)})
                    if fetched.get(key):
                        rec["uptime"] = max(rec.get("uptime", 0.0),
                                            fetched[key])
                    if latency is not None:
                        rec["successes"] += 1
                        rec["last_ok"] = ts
                        rec["latency_ms"] = latency
                        alive += 1
                    else:
                        rec["failures"] += 1
                if done % 25 == 0 or done == total:
                    progress(f"Проверено {done}/{total}, живых: {alive}")

        with self._lock:
            if len(self._db) > DB_MAX_RECORDS:
                now = time.time()
                keep = sorted(self._db.items(),
                              key=lambda kv: self._score(kv[1], now),
                              reverse=True)[:DB_MAX_RECORDS]
                self._db = dict(keep)
            self._last_refresh = time.time()
            self._save()

        return {"tested": total, "alive": alive, "best": self.best_proxy()}
