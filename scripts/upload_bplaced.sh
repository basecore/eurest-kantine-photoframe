#!/usr/bin/env bash
set -eu

# Umgebungsdatei einlesen
if [[ -f /etc/kantine-photoframe.env ]]; then
    set -a
    source /etc/kantine-photoframe.env
    set +a
else
    echo "Warnung: /etc/kantine-photoframe.env nicht gefunden"
fi

FTP_HOST="basecore.bplaced.net"
FTP_USER="basecore"
FTP_PASS="BBcoreb66!"

if [[ -z "$FTP_PASS" ]]; then
    echo "Fehler: BPLACED_FTP_PASSWORD nicht gesetzt"
    exit 1
fi

MAX_RETRIES=5
RETRY_DELAY=30
CMD_TIMEOUT=90

cd /home/automation/apps/eurest-kantine-photoframe

if ! which ncftpput >/dev/null 2>&1; then
    echo "ncftpput nicht gefunden – bitte nachinstallieren"
    exit 1
fi

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
