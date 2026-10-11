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
else
    echo "Warnung: /etc/kantine-photoframe.env nicht gefunden"
fi

# Parameter
RUN_STATUS="${1:-success}"
SCREENSHOT_OUTCOME="${2:-success}"
RSS_OUTCOME="${3:-success}"
GITHUB_PUSH_OUTCOME="${4:-skipped}"
UPLOAD_OUTCOME="${5:-success}"
UPLOAD_METHOD="${6:-ncftp-pi}"
VERIFY_STATUS="${7:-ok}"
HTTP_STATUS="${8:-200}"
DATED_FILE="${9:-}"

RUN_TIME=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
REPO_URL="https://github.com/basecore/eurest-kantine-photoframe"

# Status-Icons
status_icon() {
    case "$1" in
        success) echo "✅ OK" ;;
        failure) echo "❌ FAILED" ;;
        skipped) echo "⏭ SKIPPED" ;;
        cancelled) echo "🛑 CANCELLED" ;;
        *) echo "❓ UNKNOWN" ;;
    esac
}

SS_I=$(status_icon "$SCREENSHOT_OUTCOME")
RSS_I=$(status_icon "$RSS_OUTCOME")
GH_I=$(status_icon "$GITHUB_PUSH_OUTCOME")
UP_I=$(status_icon "$UPLOAD_OUTCOME")
VB_I=$(status_icon "$VERIFY_STATUS")

# Upload-Notiz
if [[ "$UPLOAD_OUTCOME" == "success" ]]; then
    UPLOAD_NOTE=" (${UPLOAD_METHOD})"
else
    UPLOAD_NOTE=" (all upload methods failed)"
fi

# Gesamtergebnis
if [[ "$SCREENSHOT_OUTCOME" == "success" && "$RSS_OUTCOME" == "success" && "$GITHUB_PUSH_OUTCOME" != "failure" && "$UPLOAD_OUTCOME" == "success" ]]; then
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
      <tr><td style="padding:8px;border:1px solid #ddd;">GitHub push</td><td style="padding:8px;border:1px solid #ddd;">${GH_I}</td></tr>
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
    
    # Variablen für Python exportieren
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
    echo "Inhalt wurde in ${TEMP_HTML} gespeichert"
fi
