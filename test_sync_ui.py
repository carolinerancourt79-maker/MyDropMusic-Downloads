"""Windows GUI smoke test: QR, bilingual controls and retained pairing code."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'WindowsSync'))
import MyDropMusicSync as sync
import MyDropMusicHelper as helper

with tempfile.TemporaryDirectory() as folder:
    sync.SETTINGS = Path(folder) / 'sync.json'
    helper.CERT_DIR = Path(folder) / 'Certificate'
    sync.local_ip = lambda: '192.168.1.10'
    app = sync.SyncApp()
    try:
        app.root.update()
        code = app.pairing_code
        assert app.start_button.cget('text') == 'Démarrer'
        assert len(app.qr_canvas.find_all()) > 100
        app.language_choice.set('English')
        app.change_language()
        app.root.update()
        assert app.start_button.cget('text') == 'Start'
        saved = json.loads(sync.SETTINGS.read_text())
        assert saved['language'] == 'en' and saved['pairing_code'] == code
        app.language_choice.set('Français')
        app.change_language()
        app.root.update()
        assert app.start_button.cget('text') == 'Démarrer'
        assert app.pairing_code == code
    finally:
        app.close()
print('Companion GUI checks passed')
