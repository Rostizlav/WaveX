from typing import Optional
from urllib.parse import parse_qs

# OAuth-клиенты, у которых открыт implicit flow (проверено запросами 2026-10)
OAUTH_CLIENTS = {
    "VK Me": 6463690,
    "VK.com": 6287487,
    "Kate Mobile": 2685278,
}

# какой User-Agent использовать для API при токене этого клиента
CLIENT_TOKEN_TYPES = {
    2685278: "kate",
    6287487: "official",
    6463690: "official",
}


def build_login_url(client_id: int) -> str:
    return (
        f"https://oauth.vk.com/authorize?client_id={client_id}"
        "&scope=audio,offline"
        "&redirect_uri=https://oauth.vk.com/blank.html"
        "&display=page&response_type=token&v=5.131"
    )


def vk_login(parent=None, client_id: int = 2685278) -> Optional[dict]:
    """Открывает pywebview-окно OAuth и возвращает dict с access_token/user_id
    либо None при отмене. Запускать ДО создания главного окна tkinter."""
    try:
        import webview
    except ImportError:
        print("[login] pywebview не установлен")
        return None

    result: dict = {}
    window = webview.create_window("Вход ВКонтакте", build_login_url(client_id),
                                   width=500, height=700)

    def on_loaded() -> None:
        try:
            url = window.get_current_url() or ""
        except Exception:
            return
        if not (url.startswith("https://oauth.vk.com/blank.html#")
                or url.startswith("https://oauth.vk.ru/blank.html#")):
            return
        params = parse_qs(url.split("#", 1)[1])
        if "access_token" in params:
            result["access_token"] = params["access_token"][0]
            try:
                result["user_id"] = int(params.get("user_id", ["0"])[0])
            except ValueError:
                result["user_id"] = 0
            try:
                result["expires_in"] = int(params.get("expires_in", ["0"])[0])
            except ValueError:
                result["expires_in"] = 0
        # blank.html#error=... — отмена/ошибка: result остаётся пустым
        try:
            window.destroy()
        except Exception:
            pass

    window.events.loaded += on_loaded
    try:
        webview.start(gui="edgechromium", debug=False)
    except Exception:
        try:
            webview.start(debug=False)
        except Exception as e:
            print(f"[login] webview: {e}")
    return result or None
