import json
import os
import subprocess
import sys
import tempfile
import traceback


def _error_log_path():
    try:
        from app.config import Config
        return Config().config_dir / "error.log"
    except Exception:
        return os.path.join(os.path.expanduser("~"), "wavex_error.log")


def _login_window_mode(result_path: str, client_id: int) -> None:
    """Режим дочернего процесса: окно pywebview с OAuth, результат в JSON-файл."""
    from app.ui.login import vk_login
    result = vk_login(client_id=client_id) or {}
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f)
    except OSError:
        pass


def run_login(client_id: int = 2685278) -> dict:
    """Запускает окно входа ВК в отдельном процессе (pywebview не конфликтует
    с tkinter и может вызываться повторно). Возвращает dict с access_token/user_id."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="wavex_login_")
    os.close(fd)
    try:
        args = ["--login-window", path, str(client_id)]
        if getattr(sys, "frozen", False):
            cmd = [sys.executable] + args
        else:
            cmd = [sys.executable, os.path.abspath(__file__)] + args
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(cmd, timeout=900, capture_output=True, **kwargs)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("access_token"):
            return data
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        return {"_error": err[-400:]} if err else {}
    except Exception as e:
        print(f"[main] вход: {e}")
        return {"_error": str(e)}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def show_login_splash(config) -> str:
    """Окно-заставка с кнопками входа.
    Возвращает "ok" (токен получен), "guest" (гостевой режим) или "" (отмена)."""
    import threading

    import customtkinter as ctk

    from app.ui import glass

    # Экран входа использует тему из настроек
    glass.set_theme(getattr(config, "theme", "dark"))
    ctk.set_appearance_mode(
        config.theme if getattr(config, "theme", "dark") in ("dark", "light")
        else "dark")

    splash = ctk.CTk()
    splash.title("WaveX — вход")
    splash.geometry("420x540")
    splash.resizable(False, False)
    state = {"mode": "", "busy": False}

    ctk.CTkLabel(splash, text="WaveX", font=ctk.CTkFont(size=30, weight="bold"),
                 text_color=glass.GLASS.text).pack(pady=(20, 4))
    ctk.CTkLabel(splash, text="Музыка ВКонтакте без рекламы и ограничений",
                 text_color=glass.GLASS.text_secondary).pack()
    status = ctk.CTkLabel(splash, text="", text_color=glass.GLASS.text_secondary,
                          wraplength=380, justify="center")
    status.pack(pady=(6, 0))

    from app.ui.login import CLIENT_TOKEN_TYPES, OAUTH_CLIENTS, build_login_url

    client_names = list(OAUTH_CLIENTS)
    name_by_id = {v: k for k, v in OAUTH_CLIENTS.items()}
    client_var = ctk.StringVar(
        value=name_by_id.get(config.oauth_client_id, client_names[0]))
    client_row = ctk.CTkFrame(splash, fg_color="transparent")
    client_row.pack(pady=(4, 0))
    ctk.CTkLabel(client_row, text="Клиент ВК:",
                 text_color=glass.GLASS.text_secondary,
                 font=ctk.CTkFont(size=11)).pack(side="left", padx=(0, 6))
    client_menu = ctk.CTkOptionMenu(client_row, values=client_names, variable=client_var,
                                    width=170, height=28)
    client_menu.pack(side="left")

    def selected_client_id() -> int:
        return OAUTH_CLIENTS.get(client_var.get(), 6463690)

    btn = ctk.CTkButton(splash, text="Войти через ВК (окно)", width=260, height=34)
    btn.pack(pady=(8, 3))
    chrome_btn = ctk.CTkButton(splash, text="Войти через ВК (Chrome)", width=260, height=34)
    chrome_btn.pack(pady=3)
    google_btn = ctk.CTkButton(splash, text="Войти через Google", width=260, height=32)
    google_btn.pack(pady=3)
    pass_btn = ctk.CTkButton(splash, text="Вход по логину и паролю", width=260, height=32,
                             fg_color="transparent", border_width=1,
                             text_color=("gray10", "gray90"))
    pass_btn.pack(pady=3)
    token_btn = ctk.CTkButton(splash, text="Ввести токен вручную", width=260, height=32,
                              fg_color="transparent", border_width=1,
                              text_color=("gray10", "gray90"))
    token_btn.pack(pady=3)
    guest_btn = ctk.CTkButton(splash, text="Продолжить как гость", width=260, height=32,
                              fg_color="transparent", border_width=1,
                              text_color=("gray10", "gray90"))
    guest_btn.pack(pady=3)
    proxy_btn = ctk.CTkButton(splash, text="Подобрать прокси (обход блокировки)",
                              width=260, height=32,
                              fg_color="transparent", border_width=1,
                              text_color=("gray10", "gray90"))
    proxy_btn.pack(pady=3)
    ctk.CTkLabel(splash, text="В гостевом режиме доступны YouTube, SoundCloud,\n"
                              "Яндекс Музыка, Spotify и локальные файлы",
                 text_color=glass.GLASS.text_secondary,
                 font=ctk.CTkFont(size=11), justify="center").pack(pady=(4, 6))

    glass.style_accent_button(btn)
    glass.style_accent_button(chrome_btn)
    glass.style_ghost_button(google_btn)
    glass.style_ghost_button(pass_btn)
    glass.style_ghost_button(token_btn)
    glass.style_ghost_button(guest_btn)
    glass.style_ghost_button(proxy_btn)

    # ---- автоподбор прокси ----

    def do_auto_proxy() -> None:
        if state["busy"]:
            return
        state["busy"] = True
        proxy_btn.configure(state="disabled")
        status.configure(text="Подбираю прокси: загрузка списков…\n"
                              "Это может занять 1–2 минуты.")

        def progress(msg: str) -> None:
            splash.after(0, lambda m=msg: status.configure(
                text="Подбираю прокси: " + m))

        def job() -> None:
            try:
                summary = config.proxy_manager.refresh(on_progress=progress)
                err = None
            except Exception as e:
                summary, err = None, e

            def finish() -> None:
                state["busy"] = False
                proxy_btn.configure(state="normal")
                if err is not None:
                    status.configure(text=f"Ошибка подбора прокси: {err}")
                    return
                config.auto_proxy = True
                config.save()
                best = summary.get("best")
                if best:
                    status.configure(
                        text=f"Прокси подобран: {best}\n"
                             f"(живых: {summary.get('alive', 0)} из "
                             f"{summary.get('tested', 0)}) — теперь входите.")
                else:
                    status.configure(
                        text="Живых прокси не найдено, попробуйте позже.")
            splash.after(0, finish)

        threading.Thread(target=job, daemon=True).start()

    # ---- OAuth через pywebview ----

    def _save_oauth_result(result: dict) -> bool:
        if not result.get("access_token"):
            return False
        cid = selected_client_id()
        config.token = result["access_token"]
        config.user_id = int(result.get("user_id") or 0)
        config.token_type = CLIENT_TOKEN_TYPES.get(cid, "kate")
        config.oauth_client_id = cid
        config.save()
        state["mode"] = "ok"
        return True

    def do_login(hint: str = "Открыто окно авторизации ВК…") -> None:
        if state["busy"]:
            return
        state["busy"] = True
        btn.configure(state="disabled")
        chrome_btn.configure(state="disabled")
        google_btn.configure(state="disabled")
        status.configure(text=hint)

        def job() -> None:
            result = run_login(selected_client_id())

            def finish() -> None:
                if _save_oauth_result(result):
                    splash.destroy()
                    return
                btn.configure(state="normal")
                chrome_btn.configure(state="normal")
                google_btn.configure(state="normal")
                state["busy"] = False
                if result.get("_error"):
                    status.configure(
                        text="Окно входа не открылось. Попробуйте кнопку "
                             "«…(Chrome)» ниже.\n" + result["_error"][:150])
                else:
                    status.configure(text="Вход отменён или не завершён")
            splash.after(0, finish)

        threading.Thread(target=job, daemon=True).start()

    def do_chrome_login() -> None:
        import webbrowser
        webbrowser.open(build_login_url(selected_client_id()))
        status.configure(
            text="Войдите в открывшейся вкладке Chrome. В конце страница станет\n"
                 "пустой (oauth.vk.ru/blank.html#access_token=...) — скопируйте её\n"
                 "адрес и вставьте через «Ввести токен вручную» ниже.")

    def do_google_login() -> None:
        # на странице VK ID есть кнопка «Войти через Google» — она не задействует
        # парольный endpoint, поэтому flood-бан на неё не распространяется
        do_login("На странице ВК выберите вход через Google…")

    def do_guest() -> None:
        state["mode"] = "guest"
        splash.destroy()

    # ---- ручной ввод токена / адреса ----

    def do_manual_token() -> None:
        dialog = ctk.CTkInputDialog(
            text="Вставьте access_token ИЛИ адрес страницы целиком\n"
                 "(вида oauth.vk.ru/blank.html#access_token=...):",
            title="Вход по токену")
        raw = (dialog.get_input() or "").strip()
        if not raw:
            return
        token = raw
        uid_from_url = 0
        if "access_token=" in raw:
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(raw)
            params = parse_qs(parsed.fragment) or parse_qs(parsed.query)
            token = (params.get("access_token") or [""])[0]
            try:
                uid_from_url = int((params.get("user_id") or ["0"])[0])
            except ValueError:
                uid_from_url = 0
        if not token:
            status.configure(text="Токен не найден в введённой строке")
            return
        status.configure(text="Проверка токена…")
        token_btn.configure(state="disabled")

        def job() -> None:
            from app.vk_api import VKApi
            try:
                user = VKApi(token, config).users_get()
                uid = int(user.get("id") or 0) or uid_from_url
                if not uid:
                    raise RuntimeError("пустой ответ users.get")
                err = None
            except Exception as e:
                uid, err = 0, e

            def finish() -> None:
                token_btn.configure(state="normal")
                if err is None:
                    cid = selected_client_id()
                    config.token = token
                    config.user_id = uid
                    config.token_type = CLIENT_TOKEN_TYPES.get(cid, "kate")
                    config.oauth_client_id = cid
                    config.save()
                    state["mode"] = "ok"
                    splash.destroy()
                else:
                    status.configure(text=f"Токен не подошёл: {err}")
            splash.after(0, finish)

        threading.Thread(target=job, daemon=True).start()

    # ---- логин/пароль через flow официального клиента ----

    def do_password_login() -> None:
        from app.vk_api import Need2FA, official_login

        form = ctk.CTkToplevel(splash)
        form.title("Вход в ВК")
        form.geometry("440x400")
        form.resizable(False, False)
        form.transient(splash)
        form.grab_set()
        ctk.CTkLabel(form, text="Данные отправляются только на серверы ВК\n"
                                "(flow официального приложения)",
                     text_color=glass.GLASS.text_secondary,
                     font=ctk.CTkFont(size=11)).pack(pady=(12, 6))
        login_entry = ctk.CTkEntry(form, width=320, height=34,
                                   placeholder_text="Телефон или e-mail")
        login_entry.pack(pady=5)
        pass_entry = ctk.CTkEntry(form, width=320, height=34,
                                  placeholder_text="Пароль", show="•")
        pass_entry.pack(pady=5)
        code_entry = ctk.CTkEntry(form, width=320, height=34,
                                  placeholder_text="Код подтверждения (2FA)")
        code_visible = {"v": False}
        proxy_entry = ctk.CTkEntry(
            form, width=320, height=34,
            placeholder_text="Прокси (необязательно): socks5://ip:port")
        proxy_entry.pack(pady=5)
        if config.proxy:
            proxy_entry.insert(0, config.proxy)
        for e in (login_entry, pass_entry, code_entry, proxy_entry):
            glass.style_entry(e)
        form_status = ctk.CTkLabel(form, text="", text_color="#FF9B9B",
                                   font=ctk.CTkFont(size=12), wraplength=400,
                                   justify="center")
        form_status.pack(fill="x", padx=16, pady=(6, 2))
        submit = ctk.CTkButton(form, text="Войти", width=140, height=34)
        glass.style_accent_button(submit)
        submit.pack(pady=10)

        def run() -> None:
            login = login_entry.get().strip()
            password = pass_entry.get()
            code = code_entry.get().strip() if code_visible["v"] else ""
            if not login or not password:
                form_status.configure(text="Введите логин и пароль")
                return
            new_proxy = proxy_entry.get().strip()
            if new_proxy != (config.proxy or ""):
                config.proxy = new_proxy
                config.save()
            submit.configure(state="disabled")
            form_status.configure(text="Вход…", text_color="gray")

            def job() -> None:
                try:
                    result = official_login(login, password, config, code=code or None)
                    err, need2fa = None, None
                except Need2FA as e:
                    result, err, need2fa = None, None, e
                except Exception as e:
                    result, err, need2fa = None, e, None

                def finish() -> None:
                    submit.configure(state="normal")
                    if result:
                        config.token = result["access_token"]
                        config.user_id = int(result.get("user_id") or 0)
                        config.token_type = "official"
                        config.save()
                        state["mode"] = "ok"
                        form.destroy()
                        splash.destroy()
                    elif need2fa is not None:
                        form_status.configure(
                            text=f"Код отправлен ({need2fa.phone_mask}) — введите его выше",
                            text_color="gray")
                        if not code_visible["v"]:
                            code_entry.pack(pady=4, before=form_status)
                            code_visible["v"] = True
                        code_entry.focus_set()
                    else:
                        form_status.configure(text=str(err), text_color="#d9534f")
                splash.after(0, finish)

            threading.Thread(target=job, daemon=True).start()

        submit.configure(command=run)
        pass_entry.bind("<Return>", lambda e: run())
        code_entry.bind("<Return>", lambda e: run())

    btn.configure(command=do_login)
    chrome_btn.configure(command=do_chrome_login)
    google_btn.configure(command=do_google_login)
    pass_btn.configure(command=do_password_login)
    token_btn.configure(command=do_manual_token)
    guest_btn.configure(command=do_guest)
    proxy_btn.configure(command=do_auto_proxy)
    splash.mainloop()
    return state["mode"]


def main() -> None:
    import threading

    # отдельный AppUserModelID — своё окно/иконка в панели задач Windows
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WaveX.Player")
    except Exception:
        pass

    from app.config import Config
    from app.vk_api import VKApi

    config = Config()
    if config.auto_proxy and not (config.proxy or "").strip():
        # фоновое обновление базы прокси, чтобы не задерживать запуск
        threading.Thread(target=config.proxy_manager.ensure_fresh,
                         daemon=True).start()
    while True:
        if not config.token:
            mode = show_login_splash(config)
            if mode not in ("ok", "guest"):
                return
        from app.ui.app import MainApp
        api = VKApi(config.token, config) if config.token else None
        app = MainApp(config, api)
        app.mainloop()
        if not getattr(app, "want_relogin", False):
            break


if __name__ == "__main__":
    if "--login-window" in sys.argv:
        idx = sys.argv.index("--login-window")
        _client_id = int(sys.argv[idx + 2]) if len(sys.argv) > idx + 2 else 2685278
        _login_window_mode(sys.argv[idx + 1], _client_id)
        sys.exit(0)
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        try:
            with open(_error_log_path(), "a", encoding="utf-8") as f:
                f.write(tb + "\n")
        except OSError:
            pass
        try:
            from tkinter import Tk, messagebox
            root = Tk()
            root.withdraw()
            messagebox.showerror(
                "WaveX — ошибка",
                "Приложение аварийно завершилось.\nПодробности: " + str(_error_log_path()))
            root.destroy()
        except Exception:
            print(tb)
