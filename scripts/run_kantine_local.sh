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
    # Aktuelles Bild ermitteln
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
echo "Parameter: VERIFY_STATUS=${VERIFY_STATUS}, HTTP_STATUS=${HTTP_STATUS}, DATED_FILE=${DATED_FILE}"
"$SCRIPT_DIR/send_status_email.sh" \
    "success" \
    "$SCREENSHOT_OUTCOME" \
    "$RSS_OUTCOME" \
    "skipped" \
    "$UPLOAD_OUTCOME" \
    "$UPLOAD_METHOD" \
    "$VERIFY_STATUS" \
    "$HTTP_STATUS" \
    "$DATED_FILE" || true

echo "=== Lauf abgeschlossen: $(date -Iseconds) ==="
