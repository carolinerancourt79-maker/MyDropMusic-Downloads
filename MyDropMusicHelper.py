import argparse
import hashlib
import json
import mimetypes
import os
import hmac
import socket
import ssl
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, unquote, unquote_plus
from datetime import datetime, timedelta, timezone
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff",
    ".flac", ".alac", ".mp4", ".caf"
}

CERT_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "MyDropMusic"

def tls_identity():
    """Keep this certificate stable so a paired iPhone can pin its fingerprint."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = CERT_DIR / "sync-cert.pem", CERT_DIR / "sync-key.pem"
    if not (cert_path.exists() and key_path.exists()):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MyDropMusic Sync")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=3650))
                .sign(key, hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.TraditionalOpenSSL,
                              serialization.NoEncryption()))
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    # A copied cert without its matching private key must fail before we show a QR.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert_path), str(key_path))
    fingerprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return context, fingerprint

def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

def quick_id(path: Path, stat):
    raw = f"{path.as_posix()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", "ignore")
    return hashlib.sha1(raw).hexdigest()[:16]

class MusicServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, handler, music_root, pairing_code, tls_context):
        if not pairing_code:
            raise ValueError("Pairing code is required")
        super().__init__(address, handler)
        self.music_root = Path(music_root).resolve()
        self.pairing_code = pairing_code
        self.socket = tls_context.wrap_socket(self.socket, server_side=True)

class Handler(BaseHTTPRequestHandler):
    # Reuse each TLS connection across songs instead of repeating the handshake.
    protocol_version = "HTTP/1.1"
    server_version = "MyDropMusicSync/7.0"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"service": "MyDropMusicHelper", "version": 7, "pairingRequired": True})
            return
        if not hmac.compare_digest(
            self.headers.get("X-MyDrop-Pairing", "").encode("latin-1"),
            self.server.pairing_code.encode("ascii")
        ):
            self.send_json({"error": "pairing_required"}, status=401)
            return
        if parsed.path == "/":
            ip = local_ip()
            body = f"""<!doctype html><meta charset="utf-8">
            <title>My Drop Music Helper</title>
            <style>body{{font-family:system-ui;margin:40px;max-width:760px}}code{{background:#eee;padding:3px 6px;border-radius:6px}}</style>
            <h1>My Drop Music Helper V5</h1>
            <p>Sharing: <b>{self.server.music_root}</b></p>
            <p>In My Drop Music, use:</p>
            <p><code>https://{ip}:{self.server.server_port}</code></p>
            <p>Then tap <b>Connect to PC</b> → <b>Download All Music</b>.</p>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/manifest":
            files = []
            root = self.server.music_root
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                try:
                    stat = path.stat()
                    rel = path.relative_to(root).as_posix()
                    parts = rel.split("/")
                    files.append({
                        "path": rel,
                        "size": stat.st_size,
                        "modifiedNs": stat.st_mtime_ns,
                        "quickID": quick_id(path, stat),
                        "title": path.stem,
                        "filename": path.name,
                        "relativePath": rel,
                        "folderName": parts[0] if len(parts) > 1 else "Music",
                        "modifiedAt": int(stat.st_mtime),
                    })
                except OSError:
                    pass
            self.send_json({"files": files})
            return

        if parsed.path == "/file":
            # URLComponents in iOS percent-encodes spaces as %20, but may leave
            # a literal + in a song name. parse_qs would turn that + into a
            # space, yielding a false 404. Decode the path value exactly once.
            encoded = next((item.partition("=")[2]
                            for item in parsed.query.split("&")
                            if item.partition("=")[0] == "path"), "")
            requested = unquote(encoded).replace("\\", "/").lstrip("/")
            root = self.server.music_root
            candidate = (root / requested).resolve()
            # Also accept legacy clients that send spaces as form-style +.
            if not candidate.is_file() and "+" in encoded:
                legacy = unquote_plus(encoded).replace("\\", "/").lstrip("/")
                alternate = (root / legacy).resolve()
                try:
                    alternate.relative_to(root)
                    if alternate.is_file():
                        candidate = alternate
                except ValueError:
                    pass
            try:
                candidate.relative_to(root)
            except ValueError:
                self.send_error(403, "Invalid path")
                return
            if not candidate.is_file():
                self.log_message("Missing song path: %s", ascii(requested))
                self.send_error(404, "File not found")
                return

            try:
                with candidate.open("rb") as f:
                    size = os.fstat(f.fileno()).st_size
                    mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(size))
                    self.send_header("Content-Disposition", "attachment")
                    self.end_headers()
                    # Respect the advertised length even if a file grows while
                    # reading it. A shortened file must close this connection.
                    remaining = size
                    while remaining:
                        chunk = f.read(min(1024 * 1024, remaining))
                        if not chunk:
                            self.close_connection = True
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.close_connection = True
            return

        self.send_error(404)

def choose_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="Choose your main music folder")
        root.destroy()
        return folder
    except Exception:
        return ""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?", help="Main music folder")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--pairing-code", required=True, help="Private code shown only to the iPhone owner")
    args = parser.parse_args()

    folder = args.folder or choose_folder()
    if not folder:
        print("No folder selected.")
        input("Press Enter to close...")
        return 1

    music_root = Path(folder).resolve()
    if not music_root.is_dir():
        print("Folder not found:", music_root)
        input("Press Enter to close...")
        return 1

    ip = local_ip()
    tls_context, fingerprint = tls_identity()
    server = MusicServer(("0.0.0.0", args.port), Handler, music_root, args.pairing_code, tls_context)

    print("=" * 60)
    print("MY DROP MUSIC HELPER V5")
    print("=" * 60)
    print("Sharing:", music_root)
    print()
    print("On your iPhone, in My Drop Music > Connect my PC, tap Connect to PC.")
    print("The app finds this address automatically on the same Wi-Fi:")
    print(f"https://{ip}:{args.port}  fingerprint: {fingerprint}")
    print()
    print("Then tap Connect to PC, then Download All Music.")
    print("Keep this window OPEN during the transfer.")
    print("PC and iPhone must be on the same Wi-Fi.")
    print("=" * 60)

    try:
        webbrowser.open(f"https://127.0.0.1:{args.port}/")
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping helper...")
    finally:
        server.server_close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
