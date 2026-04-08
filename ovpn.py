import json
import os
import signal
import subprocess
import threading
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# Сохраняем конфиг в скрытом файле в домашней директории пользователя
CONFIG_PATH = Path.home() / ".openvpn_gui_config.json"


class OpenVPNApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OpenVPN Launcher (sudo)")

        self.ovpn_path_var = tk.StringVar()
        self.vpn_login_var = tk.StringVar()
        self.vpn_password_var = tk.StringVar()
        self.sudo_password_var = tk.StringVar()

        self.proc = None
        self.reader_thread = None
        self._auth_file_path = None  # временный файл для --auth-user-pass

        self._load_config()
        self._build_ui()

    # ---------- конфиг (~/.openvpn_gui_config.json) ----------

    def _load_config(self):
        if not CONFIG_PATH.is_file():
            return
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        self.ovpn_path_var.set(data.get("ovpn_path", ""))
        self.vpn_login_var.set(data.get("vpn_login", ""))
        self.vpn_password_var.set(data.get("vpn_password", ""))
        # sudo-пароль не сохраняем

    def _save_config(self):
        data = {
            "ovpn_path": self.ovpn_path_var.get().strip(),
            "vpn_login": self.vpn_login_var.get().strip(),
            "vpn_password": self.vpn_password_var.get().strip(),
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(CONFIG_PATH, 0o600)
            except Exception:
                pass
        except Exception:
            pass

    # ---------- UI ----------

    def _build_ui(self):
        frm = tk.Frame(self, padx=10, pady=10)
        frm.pack(fill="both", expand=True)

        tk.Label(frm, text="Путь к .ovpn файлу:").grid(row=0, column=0, sticky="w")
        path_row = tk.Frame(frm)
        path_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 10))
        tk.Entry(path_row, textvariable=self.ovpn_path_var, width=60).pack(side="left", fill="x", expand=True)
        tk.Button(path_row, text="Выбрать...", command=self.choose_ovpn).pack(side="left", padx=(8, 0))

        tk.Label(frm, text="Логин VPN:").grid(row=2, column=0, sticky="w")
        tk.Entry(frm, textvariable=self.vpn_login_var, width=60).grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(0, 10)
        )

        tk.Label(frm, text="Пароль VPN:").grid(row=4, column=0, sticky="w")
        tk.Entry(frm, textvariable=self.vpn_password_var, show="*", width=60).grid(
            row=5, column=0, columnspan=3, sticky="ew", pady=(0, 10)
        )

        tk.Label(frm, text="Пароль sudo (админ):").grid(row=6, column=0, sticky="w")
        tk.Entry(frm, textvariable=self.sudo_password_var, show="*", width=60).grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=(0, 10)
        )

        btn_row = tk.Frame(frm)
        btn_row.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        self.connect_btn = tk.Button(btn_row, text="Подключить", command=self.connect)
        self.connect_btn.pack(side="left")
        self.stop_btn = tk.Button(btn_row, text="Остановить", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        tk.Label(frm, text="Логи:").grid(row=9, column=0, sticky="w")
        self.log = ScrolledText(frm, height=18, width=90)
        self.log.grid(row=10, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        frm.grid_rowconfigure(10, weight=1)
        frm.grid_columnconfigure(0, weight=1)

    # ---------- вспомогательное ----------

    def choose_ovpn(self):
        path = filedialog.askopenfilename(
            title="Выберите .ovpn файл",
            filetypes=[("OpenVPN config", "*.ovpn"), ("All files", "*.*")]
        )
        if path:
            self.ovpn_path_var.set(path)
            self._save_config()

    def _append_log(self, text):
        self.log.insert(tk.END, text)
        self.log.see(tk.END)

    def _make_auth_file(self, login: str, password: str) -> str:
        fd, auth_path = tempfile.mkstemp(prefix="openvpn_auth_", suffix=".txt")
        os.close(fd)

        with open(auth_path, "w", encoding="utf-8") as f:
            f.write(login.strip() + "\n" + password.strip() + "\n")

        try:
            os.chmod(auth_path, 0o600)
        except Exception:
            pass

        return auth_path

    # ---------- подключение ----------

    def connect(self):
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showinfo("Инфо", "Соединение уже запущено.")
            return

        ovpn_path = self.ovpn_path_var.get().strip()
        vpn_login = self.vpn_login_var.get().strip()
        vpn_password = self.vpn_password_var.get().strip()
        sudo_pass = self.sudo_password_var.get()

        if not ovpn_path:
            messagebox.showerror("Ошибка", "Укажите путь к .ovpn файлу.")
            return
        if not os.path.isfile(ovpn_path):
            messagebox.showerror("Ошибка", f"Файл не найден: {ovpn_path}")
            return
        if not vpn_login or not vpn_password:
            messagebox.showerror("Ошибка", "Нужно заполнить логин и пароль VPN.")
            return
        if not sudo_pass:
            messagebox.showerror("Ошибка", "Нужно заполнить пароль sudo.")
            return

        self._save_config()

        try:
            if self._auth_file_path:
                try:
                    os.remove(self._auth_file_path)
                except Exception:
                    pass
            self._auth_file_path = self._make_auth_file(vpn_login, vpn_password)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать auth-файл: {e}")
            return

        cmd = [
            "sudo", "-S", "-p", "",
            "openvpn",
            "--config", ovpn_path,
            "--auth-user-pass", self._auth_file_path
        ]

        try:
            self._append_log(f"\n[INFO] Запуск: {' '.join(cmd)}\n")
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            if self.proc.stdin is None:
                raise RuntimeError("Не удалось открыть stdin процесса sudo.")

            self.proc.stdin.write(sudo_pass + "\n")
            self.proc.stdin.flush()

            self.connect_btn.config(state="disabled")
            self.stop_btn.config(state="normal")

            def reader():
                try:
                    assert self.proc is not None
                    for line in self.proc.stdout:
                        self.after(0, self._append_log, line)
                finally:
                    code = None
                    try:
                        code = self.proc.poll()
                    except Exception:
                        pass

                    def finalize():
                        self._append_log(f"\n[INFO] Процесс завершен (exit_code={code}).\n")
                        self.connect_btn.config(state="normal")
                        self.stop_btn.config(state="disabled")
                        self._cleanup_auth_file()

                    self.after(0, finalize)

            self.reader_thread = threading.Thread(target=reader, daemon=True)
            self.reader_thread.start()

        except Exception as e:
            self.proc = None
            self.connect_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self._cleanup_auth_file()
            messagebox.showerror("Ошибка запуска", str(e))

    def _cleanup_auth_file(self):
        if not self._auth_file_path:
            return
        try:
            os.remove(self._auth_file_path)
        except Exception:
            pass
        finally:
            self._auth_file_path = None

    def stop(self):
        if self.proc is None:
            return
        try:
            self.proc.send_signal(signal.SIGINT)
            self._append_log("\n[INFO] Отправлен SIGINT.\n")
        except Exception:
            try:
                self.proc.terminate()
                self._append_log("\n[INFO] terminate().\n")
            except Exception:
                pass


if __name__ == "__main__":
    app = OpenVPNApp()
    app.mainloop()