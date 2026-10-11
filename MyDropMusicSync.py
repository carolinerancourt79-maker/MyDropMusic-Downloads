"""Small Windows interface for the existing My Drop Music Helper V5 protocol."""

import json

VERSION = "6.22.45"
TRANSLATIONS = {'Choisis ton dossier de musique.': 'Choose your music folder.', 'Garde cette fenêtre ouverte pendant la synchronisation.': 'Keep this window open while syncing.', 'Dossier de musique': 'Music folder', 'Choisir…': 'Choose…', 'Sur l’iPhone : Smart Sync → Scanner le QR': 'On iPhone: Sync with PC → Scan QR', 'Adresse à entrer dans My Drop si la détection échoue :': 'Address to enter if detection fails:', 'Code de jumelage à entrer une fois sur l’iPhone :': 'Pairing code to enter once on iPhone:', 'Démarrer': 'Start', 'Arrêter': 'Stop', 'Actualiser': 'Refresh', 'Copier': 'Copy', 'Choisir le dossier principal de musique': 'Choose your main music folder', 'Recherche des chansons…': 'Looking for songs…', 'Connexion indisponible': 'Connection unavailable', "Choisis d'abord un dossier de musique existant.": 'Choose an existing music folder first.', '● Prêt — ouvre Smart Sync sur ton iPhone': '● Ready — open Sync with PC on iPhone', 'Arrêté': 'Stopped'}
import os
import ipaddress
import secrets
import socket
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import qrcode

from MyDropMusicHelper import Handler, MusicServer, local_ip, AUDIO_EXTENSIONS, tls_identity

PORT = 8765
SETTINGS = Path(os.environ.get("APPDATA", str(Path.home()))) / "MyDropMusic" / "sync.json"


def load_settings():
    try:
        value = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return str(value.get("folder", "")), str(value.get("pairing_code", ""))
    except (OSError, ValueError, TypeError, AttributeError):
        return "", ""


def save_settings(folder, pairing_code, language="fr"):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps({"folder": folder, "pairing_code": pairing_code, "language": language}, ensure_ascii=False), encoding="utf-8")


class SyncApp:
    def __init__(self):
        saved_folder, saved_code = load_settings()
        try:
            self.language = json.loads(SETTINGS.read_text(encoding="utf-8")).get("language", "fr")
        except (OSError, ValueError, AttributeError):
            self.language = "fr"
        if self.language not in ("fr", "en"):
            self.language = "fr"
        self.pairing_code = saved_code if len(saved_code) >= 16 and saved_code.isascii() else secrets.token_urlsafe(12)
        try:
            save_settings(saved_folder, self.pairing_code, self.language)
        except OSError:
            pass
        self.root = tk.Tk()
        try:
            self.tls_context, self.fingerprint = tls_identity()
        except Exception as exc:
            messagebox.showerror("MyDropMusic Sync", f"Impossible de préparer la connexion sécurisée : {exc}")
            self.root.destroy()
            raise SystemExit(1)
        self.root.title("MyDropMusic Sync " + VERSION)
        self.root.geometry("550x700")
        self.root.minsize(530, 680)
        self.server = None
        self.thread = None
        self.folder = tk.StringVar(value=saved_folder)
        self.status = tk.StringVar(value=self.tr('Choisis ton dossier de musique.'))
        self.count = tk.StringVar(value="")
        self.address = tk.StringVar(value=f"https://{local_ip()}:{PORT}")
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if Path(self.folder.get()).is_dir():
            self.root.after(150, self.start)

    def tr(self, text):
        return TRANSLATIONS.get(text, text) if self.language == "en" else text

    def change_language(self, _event=None):
        self.language = "en" if self.language_choice.get() == "English" else "fr"
        try:
            save_settings(self.folder.get(), self.pairing_code, self.language)
        except OSError:
            pass
        self.area.destroy()
        self.build_ui()
        self.status.set(self.tr("● Prêt — ouvre Smart Sync sur ton iPhone") if self.server else self.tr("Arrêté"))
        self.start_button.configure(state="disabled" if self.server else "normal")
        self.stop_button.configure(state="normal" if self.server else "disabled")
        self.refresh()

    def build_ui(self):
        area = ttk.Frame(self.root, padding=18)
        self.area = area
        area.pack(fill="both", expand=True)
        self.language_choice = tk.StringVar(value="English" if self.language == "en" else "Français")
        picker = ttk.Combobox(area, textvariable=self.language_choice, values=["Français", "English"], state="readonly", width=12)
        picker.pack(anchor="e")
        picker.bind("<<ComboboxSelected>>", self.change_language)
        ttk.Label(area, text="MyDropMusic Sync", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(area, text=self.tr('Garde cette fenêtre ouverte pendant la synchronisation.')).pack(anchor="w", pady=(3, 15))
        ttk.Label(area, text=self.tr('Dossier de musique')).pack(anchor="w")
        row = ttk.Frame(area)
        row.pack(fill="x", pady=(4, 14))
        ttk.Entry(row, textvariable=self.folder, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(row, text=self.tr('Choisir…'), command=self.choose).pack(side="left", padx=(8, 0))
        ttk.Label(area, textvariable=self.status, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(area, textvariable=self.count).pack(anchor="w", pady=(3, 12))
        ttk.Label(area, text=self.tr('Sur l’iPhone : Smart Sync → Scanner le QR')).pack(anchor="w")
        self.qr_canvas = tk.Canvas(area, width=218, height=218, bg="white", highlightthickness=0)
        self.qr_canvas.pack(anchor="w", pady=(5, 10))
        ttk.Label(area, text=self.tr('Adresse à entrer dans My Drop si la détection échoue :')).pack(anchor="w")
        row2 = ttk.Frame(area)
        row2.pack(fill="x", pady=(4, 14))
        ttk.Entry(row2, textvariable=self.address, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text=self.tr('Copier'), command=self.copy_address).pack(side="left", padx=(8, 0))
        ttk.Label(area, text=self.tr('Code de jumelage à entrer une fois sur l’iPhone :')).pack(anchor="w")
        ttk.Label(area, text=self.pairing_code, font=("Consolas", 15, "bold")).pack(anchor="w", pady=(4, 12))
        buttons = ttk.Frame(area)
        buttons.pack(anchor="w")
        self.start_button = ttk.Button(buttons, text=self.tr('Démarrer'), command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text=self.tr('Arrêter'), command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(buttons, text=self.tr('Actualiser'), command=self.refresh).pack(side="left")
        self.draw_qr()

    def draw_qr(self):
        """Render QR modules directly in Tk, without a Pillow dependency."""
        address = self.address.get()
        host = address.removeprefix("https://").split(":", 1)[0]
        try:
            ip = ipaddress.ip_address(host)
            if ip.version != 4 or not ip.is_private or ip.is_loopback:
                self.qr_canvas.delete("all")
                return
        except ValueError:
            self.qr_canvas.delete("all")
            return
        payload = json.dumps({"format": "mydropmusic-pair-v2", "address": address,
                              "code": self.pairing_code, "fingerprint": self.fingerprint}, separators=(",", ":"))
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
        qr.add_data(payload)
        qr.make(fit=True)
        modules = qr.get_matrix()
        self.qr_canvas.delete("all")
        size = 218
        unit = size / len(modules)
        for y, row in enumerate(modules):
            for x, dark in enumerate(row):
                if dark:
                    self.qr_canvas.create_rectangle(round(x * unit), round(y * unit),
                                                     round((x + 1) * unit), round((y + 1) * unit),
                                                     fill="black", outline="black")

    def choose(self):
        chosen = filedialog.askdirectory(title=self.tr('Choisir le dossier principal de musique'), initialdir=self.folder.get() or str(Path.home()))
        if not chosen:
            return
        self.stop()
        self.folder.set(chosen)
        try:
            save_settings(chosen, self.pairing_code, self.language)
        except OSError as exc:
            messagebox.showwarning("MyDropMusic Sync", f"Dossier choisi, mais préférence non enregistrée : {exc}")
        self.start()

    def refresh(self):
        self.address.set(f"https://{local_ip()}:{PORT}")
        self.draw_qr()
        folder = Path(self.folder.get())
        if not folder.is_dir():
            self.count.set("")
            return
        self.count.set(self.tr('Recherche des chansons…'))

        def scan():
            count = 0
            for base, _, names in os.walk(folder):
                count += sum(Path(name).suffix.lower() in AUDIO_EXTENSIONS for name in names)
            self.root.after(0, lambda: self.count.set(f"{count} songs available on PC" if self.language == "en" else f"{count} chanson(s) disponibles sur le PC"))

        threading.Thread(target=scan, daemon=True).start()

    def start(self):
        if self.server is not None:
            return
        folder = Path(self.folder.get())
        if not folder.is_dir():
            messagebox.showinfo("MyDropMusic Sync", self.tr("Choisis d'abord un dossier de musique existant."))
            return
        try:
            self.server = MusicServer(("0.0.0.0", PORT), Handler, folder,
                                      pairing_code=self.pairing_code, tls_context=self.tls_context)
        except OSError as exc:
            self.status.set(self.tr('Connexion indisponible'))
            messagebox.showerror("MyDropMusic Sync", f"Impossible de démarrer sur le port {PORT}. Ferme l'ancien Helper V5 s'il est ouvert.\n\n{exc}")
            return
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.address.set(f"https://{local_ip()}:{PORT}")
        self.draw_qr()
        self.status.set(self.tr('● Prêt — ouvre Smart Sync sur ton iPhone'))
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.refresh()

    def stop(self):
        if self.server is None:
            return
        server = self.server
        self.server = None
        server.shutdown()
        server.server_close()
        if self.thread:
            self.thread.join(timeout=2)
        self.status.set(self.tr('Arrêté'))
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def copy_address(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.address.get())
        self.root.update()

    def close(self):
        self.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    SyncApp().run()
