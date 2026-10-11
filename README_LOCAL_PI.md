# Lokale Ausführung auf Raspberry Pi / Linux Mint

Dieses Dokument beschreibt die **vollständige lokale Ausführung** des Kantinen-Photoframe-Projekts auf einem Raspberry Pi oder einem Linux-PC (z. B. Linux Mint).

## Übersicht

Das Projekt unterstützt zwei Betriebsarten:

1. **GitHub Actions + Home Assistant Runner** – vollständig automatisiert über GitHub
2. **Lokale Ausführung auf Raspberry Pi / Linux Mint** – eigenständig, ohne GitHub-Abhängigkeit

---

## Option 1: GitHub Actions + Home Assistant Runner

Diese Option nutzt GitHub Actions für die Verarbeitung und einen Self-hosted Runner auf Home Assistant OS für den bplaced-Upload.

### Workflow-Datei

`.github/workflows/update-menus.yml`

### Runner auf Home Assistant OS

```bash
# Im Home Assistant Terminal Add-on:
docker rm -f github-runner-eurest 2>/dev/null || true

docker run -d \
  --name github-runner-eurest \
  --restart unless-stopped \
  -e REPO_URL="https://github.com/basecore/eurest-kantine-photoframe" \
  -e RUNNER_TOKEN="DEIN_TOKEN_HIER" \
  -e RUNNER_NAME="haos-eurest-1" \
  -e LABELS="self-hosted,haos" \
  -e RUNNER_ALLOW_RUNASROOT="1" \
  myoung34/github-runner:2.335.1
```

### GitHub Secrets

| Secret | Beschreibung |
|---|---|
| `CATERINGPORTAL_SID` | Optional für Siemens |
| `BPLACED_FTP_PASSWORD` | FTP-Passwort für bplaced |
| `MAIL_USERNAME` | Gmail-Adresse für Statusmails |
| `MAIL_PASSWORD` | Gmail App-Passwort |

---

## Option 2: Lokale Ausführung auf Raspberry Pi / Linux Mint

Diese Option läuft vollständig eigenständig – ohne GitHub Actions und ohne Home Assistant.

### Voraussetzungen

- **Raspberry Pi 3/4** mit 64-Bit-Debian/Ubuntu oder **Linux Mint** auf x86_64
- Python 3.11+
- Git
- Internetverbindung
- bplaced-FTP-Zugang
- Gmail-Konto mit App-Passwort (für Statusmails)

### Systemvoraussetzungen prüfen

```bash
# Architektur prüfen (aarch64 = ARM64, x86_64 = Intel/AMD)
uname -m
getconf LONG_BIT

# Betriebssystem prüfen
cat /etc/os-release

# Python-Version
python3 --version

# Arbeitsspeicher
free -h

# Freier Speicherplatz
df -h /
```

### Projekt klonen

```bash
mkdir -p ~/apps
cd ~/apps

git clone https://github.com/basecore/eurest-kantine-photoframe.git
cd eurest-kantine-photoframe
```

### Python-Umgebung einrichten

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install pillow pypdf playwright
```

### Browser installieren

```bash
# Chromium für Playwright
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
python -m playwright install --with-deps chromium
```

### Systempakete installieren

```bash
sudo apt-get update
sudo apt-get install -y \
    ca-certificates \
    fonts-dejavu-core \
    fonts-liberation \
    ncftp \
    curl \
    git
```

### Browser-Test

```bash
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"

python - <<'PY'
from pathlib import Path
from playwright.sync_api import sync_playwright

output = Path.home() / "kantine-browser-test.png"

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--disable-dev-shm-usage"],
    )
    try:
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.set_content(
            '<html><body style="font-family:sans-serif">'
            '<h1>Kantine: Browser-Test OK</h1>'
            '<p>Chromium läuft.</p>'
            '</body></html>'
        )
        page.screenshot(path=str(output))
        assert output.is_file() and output.stat().st_size > 0
        print(f"Browser-Test OK: {output.stat().st_size} Bytes")
    finally:
        browser.close()
PY
```

### Umgebungsdatei erstellen

```bash
sudo tee /etc/kantine-photoframe.env > /dev/null << 'ENV'
# Eurest Kantine PhotoFrame - Umgebungsvariablen

# bplaced FTP
BPLACED_FTP_PASSWORD="DEIN_BPLACED_PASSWORT"

# Gmail für Statusmails
MAIL_USERNAME="DEINE_GMAIL_ADRESSE@gmail.com"
MAIL_PASSWORD="DEIN_GMAIL_APP_PASSWORT"
MAIL_TO="deine@empfaenger.de"

# Projekt
TZ=Europe/Berlin
DISPLAY_MODE=day
DISPLAY_DAY=
WEEK_OFFSET=0
ONLY=
GENERATE_COMBINED=1
PLAYWRIGHT_BROWSERS_PATH=/home/automation/apps/eurest-kantine-photoframe/.playwright-browsers
ENV

sudo chmod 640 /etc/kantine-photoframe.env
sudo chown root:automation /etc/kantine-photoframe.env
```

### Upload-Skript erstellen

```bash
cat > scripts/upload_bplaced.sh << 'SCRIPT'
#!/usr/bin/env bash
set -eu

# Umgebungsdatei einlesen
if [[ -f /etc/kantine-photoframe.env ]]; then
    set -a
    source /etc/kantine-photoframe.env
    set +a
fi

FTP_HOST="basecore.bplaced.net"
FTP_USER="basecore"
FTP_PASS="${BPLACED_FTP_PASSWORD:-}"

if [[ -z "$FTP_PASS" ]]; then
    echo "Fehler: BPLACED_FTP_PASSWORD nicht gesetzt"
    exit 1
fi

MAX_RETRIES=5
RETRY_DELAY=30
CMD_TIMEOUT=90

cd /home/automation/apps/eurest-kantine-photoframe

attempt=1
while [[ $attempt -le $MAX_RETRIES ]]; do
    echo "ncftpput attempt $attempt of $MAX_RETRIES..."

    if timeout "$CMD_TIMEOUT" ncftpput -u "$FTP_USER" -p "$FTP_PASS" -P 21 "$FTP_HOST" /www/ docs/feed_all.php && \
       timeout "$CMD_TIMEOUT" ncftpput -u "$FTP_USER" -p "$FTP_PASS" -P 21 "$FTP_HOST" /www/ docs/feed_all.xml && \
       timeout "$CMD_TIMEOUT" ncftpput -u "$FTP_USER" -p "$FTP_PASS" -P 21 "$FTP_HOST" /www/ docs/feed_all_main.php && \
       timeout "$CMD_TIMEOUT" ncftpput -u "$FTP_USER" -p "$FTP_PASS" -P 21 "$FTP_HOST" /www/ docs/feed_all_main.xml && \
       timeout "$CMD_TIMEOUT" ncftpput -u "$FTP_USER" -p "$FTP_PASS" -m -P 21 "$FTP_HOST" /www/images/ docs/images/kantine_*.jpg; then
        echo "ncftpput success."
        exit 0
    fi

    echo "ncftpput failed. Waiting ${RETRY_DELAY}s..."
    sleep "$RETRY_DELAY"
    attempt=$((attempt + 1))
done

echo "Alle Upload-Versuche fehlgeschlagen."
exit 1
SCRIPT

chmod +x scripts/upload_bplaced.sh
```

### Statusmail-Skript erstellen

```bash
cat > scripts/send_status_email.sh << 'SCRIPT'
#!/usr/bin/env bash
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Umgebungsdatei einlesen
if [[ -f /etc/kantine-photoframe.env ]]; then
    set -a
    source /etc/kantine-photoframe.env
    set +a
fi

# Parameter
SCREENSHOT_OUTCOME="${1:-success}"
RSS_OUTCOME="${2:-success}"
UPLOAD_OUTCOME="${3:-success}"
UPLOAD_METHOD="${4:-ncftp-pi}"
VERIFY_STATUS="${5:-ok}"
HTTP_STATUS="${6:-200}"
DATED_FILE="${7:-}"

RUN_TIME=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
REPO_URL="https://github.com/basecore/eurest-kantine-photoframe"

# Status-Icons
status_icon() {
    case "$1" in
        success) echo "✅ OK" ;;
        failure) echo "❌ FAILED" ;;
        skipped) echo "⏭ SKIPPED" ;;
        *) echo "❓ UNKNOWN" ;;
    esac
}

SS_I=$(status_icon "$SCREENSHOT_OUTCOME")
RSS_I=$(status_icon "$RSS_OUTCOME")
UP_I=$(status_icon "$UPLOAD_OUTCOME")
VB_I=$(status_icon "$VERIFY_STATUS")

# Upload-Notiz
if [[ "$UPLOAD_OUTCOME" == "success" ]]; then
    UPLOAD_NOTE=" (${UPLOAD_METHOD})"
else
    UPLOAD_NOTE=" (all upload methods failed)"
fi

# Gesamtergebnis
if [[ "$SCREENSHOT_OUTCOME" == "success" && "$RSS_OUTCOME" == "success" && "$UPLOAD_OUTCOME" == "success" ]]; then
    SUBJ="✅ OK: Eurest Kantine PhotoFrame update ($RUN_TIME)"
    HC="#2e7d32"
    OV="All critical tasks successful"
else
    SUBJ="❌ FAILED: Eurest Kantine PhotoFrame update ($RUN_TIME)"
    HC="#b71c1c"
    OV="One or more critical tasks failed"
fi

# Bild-URL
IMAGE_URL=""
if [[ -n "$DATED_FILE" ]]; then
    IMAGE_URL="http://basecore.bplaced.net/images/${DATED_FILE}"
fi

# HTML-Mail erstellen
TEMP_HTML=$(mktemp)

cat > "$TEMP_HTML" << MAILEOF
<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px;color:#333;">
  <div style="background:${HC};color:#fff;padding:16px 20px;border-radius:6px 6px 0 0;">
    <h2 style="margin:0;">Eurest Kantine PhotoFrame - Update Status</h2>
  </div>
  <div style="border:1px solid #ddd;padding:20px;">
    <p><strong>Time:</strong> ${RUN_TIME}</p>
    <p><strong>Status:</strong> ${OV}</p>
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <tr style="background:#f5f5f5;">
        <th style="text-align:left;padding:8px;border:1px solid #ddd;">Task</th>
        <th style="text-align:left;padding:8px;border:1px solid #ddd;">Status</th>
      </tr>
      <tr><td style="padding:8px;border:1px solid #ddd;">Generate menus</td><td style="padding:8px;border:1px solid #ddd;">${SS_I}</td></tr>
      <tr><td style="padding:8px;border:1px solid #ddd;">Generate RSS feeds</td><td style="padding:8px;border:1px solid #ddd;">${RSS_I}</td></tr>
      <tr><td style="padding:8px;border:1px solid #ddd;">Upload to bplaced</td><td style="padding:8px;border:1px solid #ddd;">${UP_I}</td></tr>
      <tr><td style="padding:8px;border:1px solid #ddd;">Upload method</td><td style="padding:8px;border:1px solid #ddd;">${UPLOAD_METHOD}</td></tr>
      <tr><td style="padding:8px;border:1px solid #ddd;">bplaced verify</td><td style="padding:8px;border:1px solid #ddd;">${VB_I}</td></tr>
      <tr><td style="padding:8px;border:1px solid #ddd;">Verify HTTP status</td><td style="padding:8px;border:1px solid #ddd;">${HTTP_STATUS}</td></tr>
    </table>
    <p><strong>Upload note:</strong>${UPLOAD_NOTE}</p>
MAILEOF

# Bild-Sektion
if [[ -n "$IMAGE_URL" ]]; then
    cat >> "$TEMP_HTML" << IMGEOF
    <div style="margin-top:24px;">
      <h3 style="color:#333;margin-bottom:8px;">📸 Aktuelles Kantinenbild (${DATED_FILE})</h3>
      <p style="font-size:13px;color:#333;">Direktlink: <a href="${IMAGE_URL}">${IMAGE_URL}</a></p>
      <img src="${IMAGE_URL}" alt="Kantine Menü" style="max-width:100%;border:1px solid #ddd;border-radius:4px;display:block;"/>
      <p style="font-size:11px;color:#999;margin-top:4px;">Falls das Bild nicht lädt: <a href="${IMAGE_URL}">direkt öffnen</a></p>
    </div>
IMGEOF
fi

cat >> "$TEMP_HTML" << FOOTEREOF
    <p style="font-size:13px;margin-top:20px;"><a href="${REPO_URL}">🔗 View Repository</a></p>
  </div>
</body>
</html>
FOOTEREOF

# Mail senden
if [[ -n "$MAIL_USERNAME" && -n "$MAIL_PASSWORD" && -n "$MAIL_TO" ]]; then
    echo "Sende Statusmail an ${MAIL_TO}..."
    
    export PY_SMTP_SERVER="smtp.gmail.com"
    export PY_SMTP_PORT="587"
    export PY_USERNAME="$MAIL_USERNAME"
    export PY_PASSWORD="$MAIL_PASSWORD"
    export PY_TO_ADDR="$MAIL_TO"
    export PY_SUBJECT="$SUBJ"
    export PY_HTML_FILE="$TEMP_HTML"
    
    python3 - <<'PYEOF'
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import sys
import os

smtp_server = os.environ.get("PY_SMTP_SERVER", "smtp.gmail.com")
smtp_port = int(os.environ.get("PY_SMTP_PORT", "587"))
username = os.environ.get("PY_USERNAME", "")
password = os.environ.get("PY_PASSWORD", "")
to_addr = os.environ.get("PY_TO_ADDR", "")
from_addr = f"Eurest Kantine PhotoFrame <{os.environ.get('PY_USERNAME', '')}>"
subject = os.environ.get("PY_SUBJECT", "Statusmail")
html_file = os.environ.get("PY_HTML_FILE", "")

if not all([username, password, to_addr, html_file]):
    print("Fehlende Umgebungsvariablen", file=sys.stderr)
    sys.exit(1)

try:
    with open(html_file, "r", encoding="utf-8") as f:
        html_body = f.read()
except Exception as e:
    print(f"Fehler beim Lesen der HTML-Datei: {e}", file=sys.stderr)
    sys.exit(1)

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"] = from_addr
msg["To"] = to_addr

msg.attach(MIMEText("Statusmail - bitte HTML-fähigen Mail-Client verwenden", "plain"))
msg.attach(MIMEText(html_body, "html"))

try:
    with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
        server.starttls()
        server.login(username, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())
    print("Mail erfolgreich gesendet")
except Exception as e:
    print(f"Fehler beim Senden: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
else
    echo "Mail-Zugangsdaten nicht gesetzt - Mail wird nicht gesendet"
fi
SCRIPT

chmod +x scripts/send_status_email.sh
```

### Hauptskript erstellen

```bash
cat > scripts/run_kantine_local.sh << 'SCRIPT'
#!/usr/bin/env bash
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Umgebungsdatei einlesen
if [[ -f /etc/kantine-photoframe.env ]]; then
    set -a
    source /etc/kantine-photoframe.env
    set +a
fi

echo "=== Eurest Kantine PhotoFrame - Lokaler Lauf ==="
echo "Start: $(date -Iseconds)"

export TZ=Europe/Berlin
export DISPLAY_MODE=day
export DISPLAY_DAY=
export WEEK_OFFSET=0
export ONLY=
export GENERATE_COMBINED=1
export PLAYWRIGHT_BROWSERS_PATH="$PROJECT_DIR/.playwright-browsers"

# Status-Variablen
SCREENSHOT_OUTCOME="success"
RSS_OUTCOME="success"
UPLOAD_OUTCOME="success"
UPLOAD_METHOD="ncftp-pi"
VERIFY_STATUS="ok"
HTTP_STATUS="200"
DATED_FILE=""

echo "=== 1. Kantinenbilder erzeugen ==="
if .venv/bin/python scripts/generate_all.py; then
    echo "✅ Bilder erfolgreich erzeugt"
    DATED_FILE=$(ls -t docs/images/kantine_*_2026-*.jpg 2>/dev/null | head -1 | xargs basename || true)
else
    echo "❌ Bilder fehlgeschlagen"
    SCREENSHOT_OUTCOME="failure"
fi

echo "=== 2. RSS-Feeds erzeugen ==="
if .venv/bin/python scripts/generate_rss.py; then
    echo "✅ Feeds erfolgreich erzeugt"
else
    echo "❌ Feeds fehlgeschlagen"
    RSS_OUTCOME="failure"
fi

echo "=== 3. Upload zu bplaced ==="
if "$SCRIPT_DIR/upload_bplaced.sh"; then
    echo "✅ Upload erfolgreich"
    UPLOAD_OUTCOME="success"
else
    echo "❌ Upload fehlgeschlagen"
    UPLOAD_OUTCOME="failure"
    UPLOAD_METHOD="failed"
fi

echo "=== 4. Bereinigung alter Bilder (lokal) ==="
CUTOFF=$(date -u -d "5 days ago" +%Y-%m-%d)
for f in docs/images/kantine_*.jpg; do
    [ -e "$f" ] || continue
    FDATE=$(basename "$f" | sed -E 's/^kantine_[0-9]{4}-W[0-9]{2}_([0-9]{4}-[0-9]{2}-[0-9]{2})_.*\.jpg$/\1/')
    if [[ "$FDATE" != "$(basename "$f")" && "$FDATE" < "$CUTOFF" ]]; then
        echo "Deleting old file: $f (date: $FDATE)"
        rm -f "$f" || true
    fi
done

echo "=== 5. bplaced-Verifikation ==="
if [[ -n "$DATED_FILE" ]]; then
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://basecore.bplaced.net/images/${DATED_FILE}" || echo "000")
    if [[ "$HTTP_STATUS" == "200" ]]; then
        echo "✅ Bild auf bplaced verfügbar: ${DATED_FILE}"
        VERIFY_STATUS="ok"
    else
        echo "⚠️ Bild auf bplaced nicht verfügbar (HTTP ${HTTP_STATUS})"
        VERIFY_STATUS="failure"
    fi
else
    echo "⚠️ Kein aktuelles Bild für Verifikation"
    VERIFY_STATUS="skipped"
    HTTP_STATUS="N/A"
fi

echo "=== 6. Statusmail senden ==="
"$SCRIPT_DIR/send_status_email.sh" \
    "$SCREENSHOT_OUTCOME" \
    "$RSS_OUTCOME" \
    "$UPLOAD_OUTCOME" \
    "$UPLOAD_METHOD" \
    "$VERIFY_STATUS" \
    "$HTTP_STATUS" \
    "$DATED_FILE" || true

echo "=== Lauf abgeschlossen: $(date -Iseconds) ==="
SCRIPT

chmod +x scripts/run_kantine_local.sh
```

### systemd-Service und Timer

```bash
sudo tee /etc/systemd/system/kantine-photoframe.service > /dev/null << 'SERVICE'
[Unit]
Description=Eurest Kantine PhotoFrame - Daily menu images and RSS feeds
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=automation
WorkingDirectory=/home/automation/apps/eurest-kantine-photoframe
EnvironmentFile=/etc/kantine-photoframe.env
ExecStart=/home/automation/apps/eurest-kantine-photoframe/scripts/run_kantine_local.sh
TimeoutStartSec=900
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
SERVICE

sudo tee /etc/systemd/system/kantine-photoframe.timer > /dev/null << 'TIMER'
[Unit]
Description=Daily Eurest Kantine PhotoFrame update
Requires=kantine-photoframe.service

[Timer]
OnCalendar=*-*-* 12:30
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
TIMER

sudo systemctl daemon-reload
sudo systemctl enable kantine-photoframe.timer
sudo systemctl start kantine-photoframe.timer

# Status prüfen
systemctl list-timers --all | grep kantine
systemctl is-active kantine-photoframe.timer
```

### Manueller Testlauf

```bash
sudo systemctl start kantine-photoframe.service
systemctl status kantine-photoframe.service --no-pager -l
journalctl -u kantine-photoframe.service --since "10 minutes ago" --no-pager -l
```

---

## Vegetarisch-Keywords verwalten

Für die Siemens-Kantine können vegetarische/nicht-vegetarische Begriffe über eine JSON-Datei verwaltet werden:

### Datei: `scripts/veg_keywords.json`

```json
{
  "non_veg_keywords": [
    "hot dog", "hotdog", "wiener", "frankfurter",
    "fleisch", "hack", "hackfleisch", "rind", "beef",
    "schwein", "wurst", "bratwurst", "currywurst",
    "huhn", "chicken", "fisch", "fish", "lachs"
  ],
  "veg_likely_keywords": [
    "falafel", "tofu", "tempeh", "seitan",
    "quinoa", "linsen", "kichererbsen",
    "gemuesecurry", "ratatouille", "pesto"
  ]
}
```

### Neuen Begriff hinzufügen

```bash
# Hilfsskript verwenden
cd /home/automation/apps/eurest-kantine-photoframe

# Nicht-vegetarischer Begriff
./scripts/add_veg_keyword.sh non_veg "hot dog"

# Wahrscheinlich vegetarischer Begriff
./scripts/add_veg_keyword.sh veg_likely "quinoa salat"

# Alle Begriffe anzeigen
python3 -c "
import json
from pathlib import Path
config = json.loads(Path('scripts/veg_keywords.json').read_text(encoding='utf-8'))
print('NON_VEG:', config['non_veg_keywords'])
print('VEG_LIKELY:', config['veg_likely_keywords'])
"
```

---

## Vergleich der beiden Optionen

| Aspekt | GitHub Actions + HA Runner | Lokal auf Raspberry Pi / Linux Mint |
|---|---|---|
| Zeitsteuerung | GitHub Cron | systemd-Timer |
| Upload zu bplaced | Über HA-Runner | Direkt vom Pi/PC |
| Abhängigkeit von GitHub | Ja (für Auslösung) | Nein (nur für Code-Updates) |
| Statusmail | Über GitHub Action | Lokal über Python/smtplib |
| Wartung | GitHub Secrets | Lokale Umgebungsdatei |
| Sicherheit | Self-hosted Runner beachten | Lokale Zugangsdaten |

---

## Troubleshooting

### Browser startet nicht

```bash
# Playwright-Browser neu installieren
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
.venv/bin/python -m playwright install --with-deps chromium
```

### Upload schlägt fehl

```bash
# ncftpput prüfen
which ncftpput

# Umgebungsdatei prüfen
sudo cat /etc/kantine-photoframe.env | grep BPLACED

# Manueller Upload-Test
cd /home/automation/apps/eurest-kantine-photoframe
./scripts/upload_bplaced.sh
```

### Statusmail kommt nicht an

```bash
# Gmail App-Passwort prüfen
# https://myaccount.google.com/apppasswords

# Manueller Mail-Test
cd /home/automation/apps/eurest-kantine-photoframe
./scripts/send_status_email.sh "success" "success" "success" "ncftp-pi" "ok" "200" "kantine_test.jpg"
```

### Timer läuft nicht

```bash
# Timer-Status prüfen
systemctl status kantine-photoframe.timer

# Timer neu starten
sudo systemctl restart kantine-photoframe.timer

# Nächsten Lauf anzeigen
systemctl list-timers --all | grep kantine
```

---

## Zusammenfassung

Die lokale Ausführung auf Raspberry Pi oder Linux Mint bietet:

- **Vollständige Unabhängigkeit** von GitHub Actions
- **Direkten Upload** zu bplaced
- **Lokale Statusmails** im gleichen HTML-Format
- **Einfache Verwaltung** von vegetarischen Keywords
- **systemd-basierte Zeitsteuerung**
- **Robuste Fehlerbehandlung** mit Wiederholungen
