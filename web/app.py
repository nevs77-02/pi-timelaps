# web/app.py

import os
import json
import subprocess
import shlex
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, send_file, abort, url_for
from uuid import uuid4
from picamera2 import Picamera2

app = Flask(__name__, static_folder='static')
CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config.json'))
IMAGE_ROOT = "/mnt/hdd/timelapse/Bilder"
RAW_ROOT = "/mnt/hdd/timelapse/raw"
TEST_ROOT = "/mnt/hdd/timelapse/tests"
LOG_ROOT = "/mnt/hdd/timelapse/logs"
PRESET_DIR = "/mnt/hdd/timelapse/presets"
SCHEDULES_FILE = "/mnt/hdd/timelapse/schedules.json"
os.makedirs(PRESET_DIR, exist_ok=True)
TLCTL = ["python3", os.path.abspath(os.path.join(os.path.dirname(__file__), "../tlctl.py"))]

THUMB_DIR = os.path.join(os.path.dirname(__file__), "thumbs")
os.makedirs(THUMB_DIR, exist_ok=True)
LUX_CONTROL_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '../lux_control.json'))
LUX_LOG_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '../lux_log.json'))


FIELD_LABELS = {
    # --- Statische Felder ---
    "use_hdr":           "HDR-Modus (volle Auflösung)",
    "resolution":        "Auflösung (B x H)",
    "jpeg_quality":      "JPEG-Qualität (1–100)",
    "shutter":           "Belichtungszeit [µs]",
    "gain":              "Verstärkung (Gain)",
    "ev":                "Belichtungskorrektur (EV)",
    "awb_enable":        "Auto-Weißabgleich aktiv",
    "awb_mode":          "AWB-Modus",
    "focus":             "Fokus (1.0 = Unendlich)",
    "noise_reduction":   "Noise Reduction",
    "save_raw":          "RAW speichern",
    "raw_format":        "RAW-Format",
    "raw_folder":        "RAW-Ordner",
    "test_folder":       "Testbild-Ordner",
    "timelapse_folder":  "Bilder-Ordner",
    "log_folder":        "Log-Ordner",
    "min_interval":      "Mindestintervall (s)",
    "raw_delay":         "RAW-Speicherzeit (s)",
    "duration":          "Session-Dauer (s)",
    "awb_gain_r":        "AWB Gain (Rot)",
    "awb_gain_b":        "AWB Gain (Blau)",
    "saturation":        "Sättigung",
    "contrast":          "Kontrast",
    "brightness":        "Helligkeit",
    "sharpness":         "Schärfe",
    "ae_enable":         "Auto-Belichtung (AE) aktiv",
    "camera_id":         "Kameramodell wählen",

    # --- Spezial-/Systemfelder (falls sie mal auftauchen) ---
    "mode":              "Modus",
    "current_shot":      "Bildzähler",
    "last_file":         "Letztes Bild",
    "last_time":         "Zeitstempel letztes Bild",
    "error":             "Fehlermeldung",
    "running":           "Session läuft",
    "start_time":        "Startzeit",
}

AWB_MODES = [
    {"value": "auto", "name": "Automatisch"},
    {"value": "daylight", "name": "Tageslicht"},
    {"value": "cloudy", "name": "Bewölkt"},
    {"value": "tungsten", "name": "Glühlampe"},
    {"value": "fluorescent", "name": "Leuchtstoffröhre"},
    {"value": "indoor", "name": "Innenraum"},
    {"value": "custom", "name": "Benutzerdefiniert"},  # nur falls gewünscht
]
# Felder, die in den Einstellungen angezeigt und bearbeitet werden
EDITABLE_FIELDS = [
    "resolution", "camera_id", "use_hdr", "jpeg_quality", "shutter", "gain", "ev",
    "awb_enable", "ae_enable", "awb_mode", "focus", "noise_reduction", "save_raw",
    "min_interval", "raw_delay", "duration", "awb_gain_r", "awb_gain_b",
    "saturation", "contrast", "brightness", "sharpness"
]
DISPLAY_ONLY_FIELDS = [
    "timelapse_folder", "raw_folder", "test_folder", "log_folder"
]
SLIDER_OPTIONS = {
    "saturation": {"min": 0, "max": 32, "step": 1},
    "contrast": {"min": 0, "max": 32, "step": 1},
    "brightness": {"min": -1.0, "max": 1.0, "step": 0.01},
    "sharpness": {"min": 0, "max": 16, "step": 1},
}
# DropDown-Auswahloptionen für bestimmte Felder
DROPDOWN_OPTIONS = {
    "awb_mode": [m["value"] for m in AWB_MODES],
    "noise_reduction": ["auto", "off", "fast", "high_quality", "minimal"],
    "raw_format": ["raw", "npy", "dng"],
    "resolution": ["1920x1080", "4056x3040", "4608x2592", "3840x2160"],
    "jpeg_quality": [10, 40, 50, 60, 70, 80, 90, 100],
    "duration": [300, 900, 1800, 3600, 10800, 21600, 43200, 86400, 172800, 604800],
}

# Checkbox-Felder (boolesche Felder)
CHECKBOX_FIELDS = ["use_hdr","awb_enable", "save_raw", "ae_enable"]


TL_PID_FILE = os.path.join(os.path.dirname(__file__), "timelapse.pid")
STATUS_PATH = os.path.join(os.path.dirname(__file__), "status.json")

FIELD_INFOS = {
    "use_hdr": "Aktiviert HDR-Modus (fixe höchste Auflösung, maximaler Dynamikumfang, benötigt mehr Speicherplatz).",
    "resolution": "Bildgröße der Kamera. Werte: z. B. 1920x1080, 4056x3040 usw.",
    "jpeg_quality": "Qualität des JPEG-Bildes (1–100, typisch: 80–100).",
    "shutter": "Belichtungszeit in Mikrosekunden. Höher = heller, aber längere Aufnahmezeit.",
    "gain": "Verstärkung des Sensors. Höher = heller, aber mehr Bildrauschen.",
    "ev": "Belichtungskorrektur (z. B. 0, 1, -1). Regelt Helligkeit zusätzlich.",
    "awb_enable": "Auto-Weißabgleich aktivieren (empfohlen).",
    "awb_mode": "Voreinstellungen für Weißabgleich. 0=Auto, 1=sonnig, 2=bewölkt ...",
    "focus": "Fokuswert (meist 1.0 = Unendlich, kleinere Werte = näher).",
    "noise_reduction": "Rauschreduzierung: auto, off, fast, high_quality, minimal.",
    "save_raw": "Rohdaten (RAW) zusätzlich speichern (braucht mehr Speicher).",
    "min_interval": "Mindestintervall zwischen zwei Bildern in Sekunden.",
    "raw_delay": "Zeit in Sekunden, die für das Speichern von RAW-Dateien abgewartet wird.",
    "duration": "Gesamtdauer der Session in Sekunden (z. B. 3600 = 1 h).",
    "awb_gain_r": "Manueller Gain für Rot. Typisch 0.7–2.5. Wird bei deaktiviertem AWB genutzt.",
    "awb_gain_b": "Manueller Gain für Blau. Typisch 0.7–2.5. Wird bei deaktiviertem AWB genutzt.",
    "saturation": "Sättigung (0–32), höhere Werte = kräftigere Farben.",
    "contrast": "Kontrast (0–32), höhere Werte = härterer Bildkontrast.",
    "brightness": "Helligkeit (-1.0 bis 1.0), feine Steuerung des Bildlevels.",
    "sharpness": "Schärfe (0–16), höhere Werte = schärferes Bild.",
}
FIELD_GROUPS = [
    ("Kamera-Parameter", [
        "ae_enable", "shutter", "gain", "ev", "focus", "awb_enable", "awb_mode", "awb_gain_r", "awb_gain_b", "noise_reduction"
    ]),
    ("Bild-Look", [
        "saturation", "contrast", "brightness", "sharpness"
    ]),
    ("Automatik & Ablauf", [
        "min_interval", "raw_delay", "duration"
    ]),
    ("RAW & Qualität", [
        "save_raw", "raw_format", "jpeg_quality"
    ]),
    ("Auflösung", [
        "use_hdr", "resolution"
    ]),
]
from picamera2 import Picamera2

def tlctl(args):
    """tlctl.py aufrufen (Start/Stop/Status von main2.py)"""
    try:
        cmd = TLCTL + args + ["--config", CONFIG_PATH]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(__file__))
    except Exception as e:
        class _Res: pass
        r = _Res(); r.returncode = 1; r.stdout = ""; r.stderr = str(e)
        return r

def tl_status():
    cp = tlctl(["status"])
    return cp.returncode == 0

def get_available_camera_models():
    try:
        return [info.get("Model") for info in Picamera2.global_camera_info()]
    except Exception as e:
        print("Kameramodelle konnten nicht ermittelt werden:", e)
        return []


def get_relative_image_path(full_path):
    return os.path.relpath(full_path, IMAGE_ROOT)

def convert_value(key, value):
    if value in ("None", "", None): return None
    # Integer-Felder
    if key in (
        "shutter", "gain", "duration", "jpeg_quality",
        "saturation", "contrast", "sharpness"    # <--- NEU HINZUFÜGEN!
    ):
        try: return int(value)
        except: return None
    # Float-Felder
    if key in (
        "ev", "focus", "awb_gain_r", "awb_gain_b", "raw_delay", "min_interval",
        "brightness"   # <--- NEU HINZUFÜGEN!
    ):
        try: return float(value)
        except: return None
    # Bool-Felder
    if key in ("awb_enable", "save_raw", "ae_enable", "use_hdr"):
        if isinstance(value, bool): return value
        if isinstance(value, str): return value.lower() in ("true", "1", "yes", "on")
    # Array für resolution
    if key == "resolution":
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            if value.startswith("["):
                return [int(x) for x in value.strip("[] ").split(",")]
            if "x" in value:
                return [int(x) for x in value.split("x")]
        return [1920, 1080]
    return value

    
def get_relative_raw_path(full_path):
    return os.path.relpath(full_path, RAW_ROOT)

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save_config(cfg):
    print(">>> save_config CALLED!")
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    print("Wirklich geschrieben wird nach:", CONFIG_PATH)
    print(">>> Config written:", CONFIG_PATH)

def get_status():
    if os.path.exists(STATUS_PATH):
        with open(STATUS_PATH, "r") as f:
            return json.load(f)
    else:
        return {"running": False, "mode": "timelapse", "current_shot": 0, "last_file": "", "last_time": "", "error": ""}

def set_status(new_status):
    with open(STATUS_PATH, "w") as f:
        json.dump(new_status, f, indent=2)

def find_latest_images(n=10):
    result = []
    roots = [IMAGE_ROOT, TEST_ROOT]
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            for file in files:
                if file.lower().endswith(".jpg"):
                    full = os.path.join(dirpath, file)
                    result.append((os.path.getmtime(full), full))
    result.sort(reverse=True)
    return [f for t, f in result[:n]]

def get_thumb_path(image_path):
    base = os.path.basename(image_path)
    thumbdir = os.path.join(os.path.dirname(__file__), "thumbs")
    os.makedirs(thumbdir, exist_ok=True)
    thumbfile = os.path.join(thumbdir, base + ".thumb.jpg")
    if not os.path.exists(thumbfile):
        try:
            from PIL import Image
            img = Image.open(image_path)
            img.thumbnail((160, 90))
            img.save(thumbfile, "JPEG")
        except Exception:
            return None
    return thumbfile

def latest_logfile(pattern="timelapse"):
    today = datetime.now().strftime("%Y-%m-%d")
    fname = f"{pattern}_{today}.log"
    path = os.path.join(LOG_ROOT, fname)
    return path if os.path.exists(path) else None

def get_last_lines(logfile, n=20):
    try:
        with open(logfile, "r") as f:
            lines = f.readlines()[-n:]
        return lines
    except Exception:
        return []

def get_free_disk_space(path):
    st = os.statvfs(path)
    free = st.f_bavail * st.f_frsize
    return free // (1024 * 1024)  # MB

@app.route('/api/cameras')
def api_cameras():
    from picamera2 import Picamera2
    try:
        models = [info.get("Model") for info in Picamera2.global_camera_info()]
    except Exception as e:
        print("Fehler bei Kameraliste:", e)
        models = []
    return jsonify(models)
def load_lux_config():
    if os.path.exists(LUX_CONTROL_FILE):
        with open(LUX_CONTROL_FILE, "r") as f:
            return json.load(f)
    return {"enabled": False, "check_interval_s": 60, "switch_delay_s": 300, "cooldown_s": 900, "mappings": []}

def save_lux_config(cfg):
    with open(LUX_CONTROL_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

def load_lux_log():
    if os.path.exists(LUX_LOG_FILE):
        with open(LUX_LOG_FILE, "r") as f:
            return json.load(f)
    return []

def save_lux_log(log_entry):
    log = load_lux_log()
    log.insert(0, log_entry) # Neue Einträge oben hinzufügen
    log = log[:20] # Nur die letzten 20 Einträge behalten
    with open(LUX_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)

# --- NEU: tlctl-Status (paralleles System) ---
@app.route('/api/status_tlctl')
def api_status_tlctl():
    st = get_status()
    st["running"] = tl_status()
    return jsonify(st)

# --- NEU: tlctl-Session (paralleles System) ---
@app.route('/api/session_tlctl', methods=['POST'])
def api_session_tlctl():
    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip().lower()
    status = get_status()

    if action == "start":
        cp = tlctl(["start"])
        if cp.returncode != 0:
            return jsonify({"error": cp.stderr or cp.stdout or "Start fehlgeschlagen"}), 400
        # Ownership-Info wie in der Legacy-Route
        with open(os.path.join(os.path.dirname(__file__), "timelapse_origin.txt"), "w", encoding="utf-8") as f:
            f.write("web")
        status["running"] = True
        status["start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    elif action == "stop":
        cp = tlctl(["stop"])
        if cp.returncode != 0:
            return jsonify({"error": cp.stderr or cp.stdout or "Stop fehlgeschlagen"}), 400
        status["running"] = False
        origin_file = os.path.join(os.path.dirname(__file__), "timelapse_origin.txt")
        if os.path.exists(origin_file):
            os.remove(origin_file)

    else:
        return jsonify({"error": "Ungültige Aktion. Erlaubt: start|stop"}), 400

    set_status(status)
    return jsonify({"success": True, "status": status})

# --- NEU: Lux-Force-Preset (Override), reagiert der lux_controller.py drauf ---
@app.route('/api/lux_apply', methods=['POST'])
def api_lux_apply():
    data = request.get_json(force=True) if request.is_json else {}
    preset = data.get("preset")  # z.B. "day" oder None
    cfg = load_lux_config()
    cfg["force_preset"] = preset if preset else None
    save_lux_config(cfg)
    return jsonify({"success": True, "force_preset": cfg["force_preset"]})

# Alle Presets auflisten
@app.route('/api/presets', methods=['GET'])
def api_presets():
    presets = [f[:-5] for f in os.listdir(PRESET_DIR) if f.endswith('.json')]
    return jsonify(presets)

# Ein Preset laden
@app.route('/api/preset/<name>', methods=['GET'])
def api_preset_load(name):
    path = os.path.join(PRESET_DIR, name + '.json')
    if not os.path.exists(path):
        return jsonify({"error": "Preset nicht gefunden"}), 404
    with open(path) as f:
        return jsonify(json.load(f))

# Aktuelle Einstellungen als Preset speichern
@app.route('/api/preset/<name>', methods=['POST'])
def api_preset_save(name):
    data = request.get_json(force=True)
    # Typkorrektur für alle Felder!
    for k in EDITABLE_FIELDS:
        if k in data:
            data[k] = convert_value(k, data[k])
    path = os.path.join(PRESET_DIR, name + '.json')
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    return jsonify({"success": True})

# Preset löschen
@app.route('/api/preset/<name>', methods=['DELETE'])
def api_preset_delete(name):
    path = os.path.join(PRESET_DIR, name + '.json')
    if os.path.exists(path):
        os.remove(path)
        return jsonify({"success": True})
    return jsonify({"error": "Preset nicht gefunden"}), 404

@app.route('/api/video_folders')
def api_video_folders():
    result = []
    for root, dirs, files in os.walk(IMAGE_ROOT):
        # Nur Ordner mit mindestens 1 Bild
        if any(f.lower().endswith('.jpg') for f in files):
            # Relativer Pfad vom IMAGE_ROOT
            rel = os.path.relpath(root, IMAGE_ROOT)
            result.append(rel)
    # Nach Datum sortieren (optional)
    result.sort()
    return jsonify(result)

@app.route('/api/create_video', methods=['POST'])
def api_create_video():
    data = request.json
    folder = data.get("folder")
    fps = int(data.get("fps", 24))
    resolution = data.get("resolution", "1920x1080")
    codec = data.get("codec", "libx264")
    quality = int(data.get("quality", 18))
    # Zielordner für Videos
    VIDEO_ROOT = "/mnt/hdd/timelapse/videos"
    os.makedirs(VIDEO_ROOT, exist_ok=True)
    src_folder = os.path.join(IMAGE_ROOT, folder)
    output_file = os.path.join(VIDEO_ROOT, f"{folder.replace('/','_')}_{fps}fps.mp4")
    pattern = os.path.join(src_folder, "*.jpg")
    # FFMPEG-Aufruf bauen
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate", str(fps),
        "-pattern_type", "glob",
        "-i", pattern,
        "-vf", f"scale={resolution}",
        "-c:v", codec,
        "-crf", str(quality),
        "-pix_fmt", "yuv420p",
        output_file
    ]
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return jsonify({"success": True, "video": os.path.basename(output_file)})
    except subprocess.CalledProcessError as e:
        return jsonify({"success": False, "error": e.output.decode()}), 500

@app.route('/download/video/<filename>')
def download_video(filename):
    VIDEO_ROOT = "/mnt/hdd/timelapse/videos"
    return send_from_directory(VIDEO_ROOT, filename, as_attachment=True)


@app.route('/api/session', methods=['POST'])
def api_session():
    cmd = request.json.get("action")
    status = get_status()
    if cmd == "start":
        if os.path.exists(TL_PID_FILE):
            return jsonify({"error": "Timelapse läuft bereits!"}), 400
        proc = subprocess.Popen(
            ["python3", "../main.py", "timelapse"],
            stdout=None, stderr=None
        )
        with open(TL_PID_FILE, "w") as f:
            f.write(str(proc.pid))
        # <<< NEU: Schreibe die Ownership! >>>
        with open(os.path.join(os.path.dirname(__file__), "timelapse_origin.txt"), "w") as f:
            f.write("web")
        status["running"] = True
        status["start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elif cmd == "stop":
        if os.path.exists(TL_PID_FILE):
            with open(TL_PID_FILE, "r") as f:
                pid = int(f.read())
            try:
                os.kill(pid, 9)
            except Exception:
                pass
            os.remove(TL_PID_FILE)
            # <<< Ownership-File entfernen >>>
            origin_file = os.path.join(os.path.dirname(__file__), "timelapse_origin.txt")
            if os.path.exists(origin_file):
                os.remove(origin_file)
        status["running"] = False
    set_status(status)
    return jsonify({"success": True, "status": status})

@app.route('/api/testshot', methods=['POST'])
def api_testshot():
    subprocess.run(["python3", "../main.py", "single"])
    status = get_status()
    status["last_testshot"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_status(status)
    return jsonify({"success": True})

@app.route('/')
def index():
    config = load_config()
    status = get_status()
    lux_config = load_lux_config()
    presets = [f[:-5] for f in os.listdir(PRESET_DIR) if f.endswith('.json')]

    cfg = {k: config.get(k) for k in EDITABLE_FIELDS + DISPLAY_ONLY_FIELDS}
    return render_template(
        "index.html",
        cfg=cfg,
        dropdown_options=DROPDOWN_OPTIONS,
        awb_modes=AWB_MODES,
        checkbox_fields=CHECKBOX_FIELDS,
        slider_options=SLIDER_OPTIONS,
        field_labels=FIELD_LABELS,
        display_only_fields=DISPLAY_ONLY_FIELDS,
        field_infos=FIELD_INFOS,
        field_groups=FIELD_GROUPS, 
        status=status,
        thisyear=datetime.now().year,
        lux_config=lux_config, # NEU
        presets=presets # NEU
    )

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    config = load_config()
    if request.method == 'POST':
        data = request.get_json(force=True)
        for k in EDITABLE_FIELDS:
            if k in data:
                config[k] = convert_value(k, data[k])
        save_config(config)
        return jsonify({"success": True})
    else:
        return jsonify({k: config.get(k) for k in EDITABLE_FIELDS})

@app.route('/api/status')
def api_status():
    return jsonify(get_status())

@app.route('/api/lastimage')
def api_lastimage():
    imgs = find_latest_images(1)
    if not imgs:
        return jsonify({})
    path = imgs[0]
    thumbfile = get_thumb_path(path)
    rel_img = get_relative_image_path(path)
    dirname = os.path.relpath(os.path.dirname(path), IMAGE_ROOT)
    rawname = os.path.splitext(os.path.basename(path))[0] + ".raw"
    raw_path = os.path.join(RAW_ROOT, dirname, rawname)
    raw_rel = os.path.relpath(raw_path, RAW_ROOT) if os.path.exists(raw_path) else None

    meta = {}
    json_path = os.path.splitext(path)[0] + ".json"
    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    
    meta_preview = {
        "shutter": meta.get("controls", {}).get("ExposureTime") or meta.get("config", {}).get("shutter"),
        "gain": meta.get("controls", {}).get("AnalogueGain") or meta.get("config", {}).get("gain"),
        "awb_r": None,
        "awb_b": None,
    }
    if "ColourGains" in meta.get("controls", {}):
        cg = meta["controls"]["ColourGains"]
        if isinstance(cg, (list, tuple)) and len(cg) == 2:
            meta_preview["awb_r"] = cg[0]
            meta_preview["awb_b"] = cg[1]
    elif meta.get("config", {}).get("awb_gain_r"):
        meta_preview["awb_r"] = meta["config"]["awb_gain_r"]
        meta_preview["awb_b"] = meta["config"]["awb_gain_b"]

    return jsonify({
        "full": url_for('download_image', img=rel_img),
        "thumb": url_for('thumb', filename=os.path.basename(thumbfile)),
        "mtime": os.path.getmtime(path),
        "filename": os.path.basename(path),
        "raw": url_for('download_raw', img=raw_rel) if raw_rel else None,
        "meta": meta_preview
    })

@app.route('/thumbs/<filename>')
def thumb(filename):
    thumbdir = os.path.join(os.path.dirname(__file__), "thumbs")
    return send_from_directory(thumbdir, filename)

@app.route('/api/gallery')
def api_gallery():
    # 1. Die Liste der aktuellsten 10 Bilder abrufen
    files = find_latest_images(10)
    
    # 2. Die Liste der Dateinamen für die benötigten Thumbnails erstellen
    needed_thumbs = {os.path.basename(f) + ".thumb.jpg" for f in files}
    
    # 3. Den Inhalt des Thumbnail-Verzeichnisses prüfen
    for thumb_filename in os.listdir(THUMB_DIR):
        if thumb_filename.endswith(".thumb.jpg") and thumb_filename not in needed_thumbs:
            try:
                # 4. Nicht benötigte Thumbnails löschen
                os.remove(os.path.join(THUMB_DIR, thumb_filename))
            except OSError as e:
                print(f"Fehler beim Löschen des Thumbnails {thumb_filename}: {e}")

    # 5. Die Galerie-Daten mit den aktualisierten Thumbnails erstellen
    result = []
    for f in files:
        thumbfile = get_thumb_path(f)
        rel_img = os.path.relpath(f, IMAGE_ROOT)
        dirname = os.path.relpath(os.path.dirname(f), IMAGE_ROOT)
        rawname = os.path.splitext(os.path.basename(f))[0] + ".raw"
        raw_path = os.path.join(RAW_ROOT, dirname, rawname)
        raw_rel = os.path.relpath(raw_path, RAW_ROOT) if os.path.exists(raw_path) else None

        thumb_filename = os.path.basename(thumbfile) if thumbfile else None
        
        result.append({
            "full": url_for('download_image', img=rel_img),
            "thumb": url_for('thumb', filename=thumb_filename) if thumb_filename else None,
            "filename": os.path.basename(f),
            "mtime": datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S"),
            "raw": url_for('download_raw', img=raw_rel) if raw_rel else None
        })
    return jsonify(result)

@app.route('/api/log', methods=['GET'])
def api_log():
    log = latest_logfile("timelapse")
    lines = get_last_lines(log) if log else []
    return jsonify({"lines": lines})

@app.route('/download/log/<which>')
def download_log(which):
    log = latest_logfile(which)
    if log:
        return send_file(log, as_attachment=True)
    abort(404)

@app.route('/download/image/<path:img>')
def download_image(img):
    img_path = os.path.join(IMAGE_ROOT, img)
    if os.path.exists(img_path):
        return send_file(img_path, as_attachment=True)
    abort(404)

@app.route('/download/raw/<path:img>')
def download_raw(img):
    raw_path = os.path.join(RAW_ROOT, img)
    if os.path.exists(raw_path):
        return send_file(raw_path, as_attachment=True)
    abort(404)

@app.route('/api/sysinfo')
def api_sysinfo():
    freemem = get_free_disk_space(IMAGE_ROOT)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return jsonify({
        "hostname": os.uname().nodename,
        "time": now,
        "disk_mb": freemem,
        "python": os.sys.version,
        "project": "Timelapse Pi"
    })
@app.route('/api/lux_config', methods=['GET', 'POST'])
def api_lux_config():
    if request.method == 'POST':
        data = request.get_json()
        save_lux_config(data)
        return jsonify({"success": True})
    return jsonify(load_lux_config())

@app.route('/api/lux_log')
def api_lux_log():
    return jsonify(load_lux_log())
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)