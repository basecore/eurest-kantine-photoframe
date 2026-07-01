# eurest-kantine-photoframe

Weekly Eurest Kantine Regensburg (**Schaeffler** & **Aumovio**) menu screenshot → RSS feed for Philips 8FF3WMI PhotoFrame.

Inspired by / same approach as [siemens-kantine-photoframe](https://github.com/basecore/siemens-kantine-photoframe).

## Kantinen

| Name | Location-ID |
|---|---|
| SCHAEFFLER Regensburg (0967) | `8949` |
| AUMOVIO Regensburg (1553) | `8950` |

## Ausgabe

- `docs/images/kantine_YYYY-WXX_<location>.jpg` – 800×600 JPEG (Bilderrahmen-Auflösung)
- `docs/images/latest_schaeffler.jpg` / `latest_aumovio.jpg` – immer aktuellstes Bild
- `docs/feed_schaeffler.xml` / `docs/feed_aumovio.xml` – RSS-Feed (HTTPS, GitHub Pages)
- `docs/feed_schaeffler.php` / `docs/feed_aumovio.php` – RSS-Feed (HTTP, bplaced, kein Cache)

## Umgebungsvariablen / Secrets

| Variable | Bedeutung | Default |
|---|---|---|
| `EUREST_LOCATION_ID` | `8949` = Schaeffler, `8950` = Aumovio | `8949` |
| `EUREST_LOCATION_NAME` | Kurzname für Dateinamen (`schaeffler`/`aumovio`) | `schaeffler` |
| `WEEK_OFFSET` | `0` = aktuelle Woche, `1` = nächste Woche | `1` |

Kein Secret erforderlich – die Eurest-Seite ist öffentlich zugänglich.

## Setup

```bash
pip install playwright Pillow
python -m playwright install chromium
python -m playwright install-deps chromium

# Schaeffler scrapen:
EUREST_LOCATION_ID=8949 EUREST_LOCATION_NAME=schaeffler python scripts/take_screenshot.py

# Aumovio scrapen:
EUREST_LOCATION_ID=8950 EUREST_LOCATION_NAME=aumovio python scripts/take_screenshot.py

# RSS generieren:
python scripts/generate_rss.py
```

## GitHub Actions

Der Workflow läuft automatisch:
- **Mo–Do 00:00 UTC** (= 02:00 CEST) → aktuelle Woche, beide Kantinen
- **Fr 12:00 UTC** (= 14:00 CEST) → nächste Woche vorab
- **Sa + So 08:00 UTC** (= 10:00 CEST) → nächste Woche
- Manuell via `workflow_dispatch` mit Auswahl der Kantine und Week-Offset

## GitHub Pages

In den Repo-Einstellungen unter **Settings → Pages → Source: Deploy from branch `main` → `/docs`** aktivieren.

Dann erreichbar unter:
```
https://basecore.github.io/eurest-kantine-photoframe/
```

## Lizenz

MIT
