# 📸 Timelapse Pi – Automatisiertes Raspberry Pi Timelapse System

![Projekt-Logo oder Titelbild](docs/images/banner.png)

Timelapse Pi ist ein umfassendes **Zeitraffer- und Sensordatenerfassungssystem** für den Raspberry Pi mit moderner Weboberfläche.  
Es kombiniert:

- **Hochauflösende Zeitraffer-Fotografie** (JPEG & optional RAW)
- **Automatische Belichtungs- und Preset-Steuerung** basierend auf Lichtsensoren
- **Live-Konfiguration & Steuerung** über ein responsives Webinterface
- **Datenvisualisierung** (Lux, Farbtemperatur, RGB-Kanäle, AWB-Gains)
- **Videoerstellung** direkt im Browser (FFmpeg)
- **Automatische Neustarts & Überwachung** der Aufnahmeprozesse

> ℹ️ **Hinweis:**  
> Dieses Projekt wurde mit Unterstützung von KI entwickelt.  
> Einige Funktionen befinden sich noch in der Entwicklung und sind möglicherweise nicht vollständig implementiert.  
> Beiträge, Ideen und neue Features sind jederzeit willkommen – Pull Requests und Issues sind sehr gerne gesehen!


---

## ✨ Hauptfunktionen

- 🎥 **Timelapse-Aufnahmen** mit anpassbaren Kamera-Parametern (Shutter, Gain, AWB, etc.)
- 🌅 **Lux-basierte Preset-Umschaltung** (z. B. Tag/Dämmerung/Nacht)
- 📊 **Sensorlogging** mit VEML7700 & TCS34725, inkl. automatischer Diagrammerstellung
- 💻 **Flask-Webinterface** mit Tabs für:
  - Einstellungen & Presets
  - Lux-Automatik
  - Video-Erstellung aus Bildordnern
  - Sensordiagramme
  - Log-Ansicht
  - Galerie letzter Bilder
- 🔄 **Supervisor- und Controller-Skripte** für kontinuierlichen Betrieb
- 📂 **Ordnerstruktur mit Tages- und Session-Unterordnern**
- ⚙️ **Einfache Konfiguration** über `config.json` und Web-GUI
- 📈 **Plotly-Visualisierung** der Sensordaten (`make_charts.py`)

---

## 📷 Screenshots

| Startseite / Status | Sensordiagramme | Lux-Automatik |
|---------------------|-----------------|---------------|
| ![](docs/images/ui-dashboard.png) | ![](docs/images/ui-charts.png) | ![](docs/images/ui-lux.png) |

---

## 🛠️ Hardware-Voraussetzungen

- Raspberry Pi mit **[libcamera](https://www.raspberrypi.com/documentation/computers/camera_software.html)** kompatibler Kamera  
  (z. B. HQ Camera, IMX708, IMX477)
- Lichtsensor **VEML7700**
- Farbsensor **TCS34725**
- Optional: externe Festplatte/SSD für `/mnt/hdd/timelapse`

---

## 📂 Projektstruktur

```text
timelapse2/
├── main.py                  # Kernskript für Timelapse-Aufnahmen
├── timelapse_supervisor.py  # Überwacht main.py Sessions
├── lux_controller.py        # Lux-basierte Presetsteuerung
├── sensor_logger.py         # Loggt Lux- und Farbwerte in CSV
├── make_charts.py           # Erzeugt HTML-Diagramme aus CSV
├── config.json              # Aktuelle Kamera-/Session-Konfiguration
├── lux_control.json         # Lux-Automatik-Konfiguration
├── lux_log.json             # Log der Preset-Wechsel
├── web/                     # Flask-Webinterface
│   ├── app.py               # Flask-Backend
│   ├── static/              # CSS, Charts, Thumbnails
│   ├── templates/index.html # Haupt-UI
│   └── thumbs/              # Automatisch generierte Vorschaubilder
└── presets/                 # JSON-Dateien für Presets

# 1. Repository klonen
git clone https://github.com/<USER>/timelapse-pi.git
cd timelapse-pi

# 2. Python-Umgebung einrichten
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Projektverzeichnisse anlegen
mkdir -p /mnt/hdd/timelapse/{Bilder,raw,logs,tests,presets,videos}

# 4. Flask starten
cd web
python3 app.py

sudo apt install ffmpeg

⚙️ Konfiguration
config.json: Alle Kamera-Parameter und Pfade

lux_control.json: Definiert Lux-Grenzen und zugehörige Presets

Presets: Im Webinterface erstellen/laden/speichern
→ gespeichert in /mnt/hdd/timelapse/presets/NAME.json

📊 Sensordaten & Diagramme
sensor_logger.py loggt kontinuierlich:

Lux (VEML7700 fix/auto)

Weiß-/ALS-Kanäle

RGBC-Werte & Farbtemperatur (TCS34725)

make_charts.py erzeugt Plotly-Diagramme und schreibt sie in web/static/charts.html
🔄 Prozesse & Überwachung
main.py – Kern-Timelapse-Logik

timelapse_supervisor.py – hält main.py dauerhaft am Laufen

lux_controller.py – automatische Umschaltung der Presets

sensor_logger.py – Sensor-Datenlogger

🖥️ Webinterface
Flask-App unter Port 8000

Tabs:

Einstellungen (alle Kamera-Parameter)

Lux-Automatik (Regeln + Log)

Videoerstellung

Sensordaten (interaktive Diagramme)

Logs

Galerie

📽️ Videoerstellung
Wähle im Tab Video einen Bildordner

Stelle FPS, Auflösung, Codec & Qualität ein

Klick auf „Video erstellen“ → MP4 mit FFmpeg generiert

🤝 Mitwirken
Pull Requests willkommen!
Bei größeren Änderungen bitte vorher ein Issue eröffnen.

