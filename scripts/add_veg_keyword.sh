#!/usr/bin/env bash
set -eu

# Parameter: Liste und Begriff
LIST="${1:-}"
TERM="${2:-}"

if [[ -z "$LIST" || -z "$TERM" ]]; then
    echo "Verwendung: $0 <non_veg|veg_likely> <begriff>"
    echo ""
    echo "Beispiele:"
    echo "  $0 non_veg 'hot dog'"
    echo "  $0 non_veg 'wiener'"
    echo "  $0 veg_likely 'quinoa salat'"
    exit 1
fi

# Liste validieren
case "$LIST" in
    non_veg) KEY="non_veg_keywords" ;;
    veg_likely) KEY="veg_likely_keywords" ;;
    *) echo "Fehler: Liste muss 'non_veg' oder 'veg_likely' sein"; exit 1 ;;
esac

cd /home/automation/apps/eurest-kantine-photoframe

python3 - <<PYEOF
import json
from pathlib import Path

path = Path("scripts/veg_keywords.json")
config = json.loads(path.read_text(encoding="utf-8"))

term = "${TERM}".lower().strip()
key = "${KEY}"

if term in config[key]:
    print(f"'{term}' ist bereits in {key}")
else:
    config[key].append(term)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"'{term}' zu {key} hinzugefügt")

print(f"Anzahl Begriffe in {key}: {len(config[key])}")
PYEOF

# Syntax prüfen
.venv/bin/python -m py_compile scripts/take_screenshot_siemens.py
echo "Syntax-Prüfung OK"
