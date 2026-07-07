#!/usr/bin/env python3
"""Generiert RSS-Feeds fuer Philips PhotoFrame.

Einzelfeeds:
- feed_<location>.php  - HTTP URLs via bplaced, .php Extension
- feed_<location>.xml  - HTTPS URLs via github.io

Zusätzlich:
- feed_all.php         - ein Sammelfeed mit allen verfuegbaren Einzelbildern
- feed_all.xml         - ein Sammelfeed mit allen verfuegbaren Einzelbildern
- feed_all_main.php    - ein Feed fuer das kombinierte Hauptgerichte-Bild
- feed_all_main.xml    - ein Feed fuer das kombinierte Hauptgerichte-Bild

Wichtig:
- Die Feed-Auswahl erfolgt NICHT per Dateinamen-Sortierung.
- Stattdessen wird pro Kantine die Manifest-Datei current_<location>.json verwendet.
- Falls ein Bild am selben Tag erneut gerendert wird, sorgt ein Query-Parameter ?v=...
  fuer Cache-Busting auf Bild-URL-Ebene.
"""

from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote
import html
import json

BASE_URL = "https://basecore.github.io/eurest-kantine-photoframe"
BASE_URL_HTTP = "http://basecore.bplaced.net/eurest"
IMAGES_URL = f"{BASE_URL}/images"
IMAGES_URL_HTTP = f"{BASE_URL_HTTP}/images"

DOCS_DIR = Path("docs")
IMAGES_DIR = DOCS_DIR / "images"

FRAME_WIDTH = 800
FRAME_HEIGHT = 600

LOCATIONS = {
    "schaeffler": "SCHAEFFLER Regensburg",
    "aumovio": "AUMOVIO Regensburg",
    "siemens": "SIEMENS Regensburg",
}

SPECIAL_LOCATION = {
    "all_main": "ALLE Hauptgerichte Regensburg",
}

COMBINED_FEED_NAME = "Alle Kantinen Regensburg – Speisepläne"


def _now_utc():
    return datetime.now(timezone.utc)


def _rfc2822(dt):
    return dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _parse_iso_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _load_manifest(location_name):
    manifest_path = IMAGES_DIR / f"current_{location_name}.json"
    if not manifest_path.exists():
        print(f"[{location_name}] Manifest fehlt: {manifest_path}")
        return None

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[{location_name}] Manifest konnte nicht gelesen werden: {exc}")
        return None

    if not isinstance(data, dict):
        print(f"[{location_name}] Manifest hat ungueltiges Format")
        return None

    data["_manifest_path"] = str(manifest_path)
    return data


def _fallback_manifest(location_name, location_label):
    """Fallback fuer Altbestand, falls noch kein Manifest existiert."""
    pattern = f"kantine_*_{location_name}.jpg"
    files = sorted(
        IMAGES_DIR.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    if not files:
        latest = IMAGES_DIR / f"latest_{location_name}.jpg"
        if latest.exists():
            files = [latest]

    if not files:
        print(f"[{location_name}] Keine Bilder gefunden – ueberspringe")
        return None

    chosen = files[0]
    print(f"[{location_name}] Fallback-Bild: {chosen.name}")

    return {
        "location_name": location_name,
        "location_label": location_label,
        "display_mode": "unknown",
        "label": "",
        "kw": None,
        "image": chosen.name,
        "latest": f"latest_{location_name}.jpg",
        "generated_at_utc": _now_utc().isoformat().replace("+00:00", "Z"),
    }


def _resolve_manifest(location_name, location_label):
    manifest = _load_manifest(location_name)
    if manifest:
        image_name = manifest.get("image")
        latest_name = manifest.get("latest")

        image_path = IMAGES_DIR / image_name if image_name else None
        latest_path = IMAGES_DIR / latest_name if latest_name else None

        if image_path and image_path.exists():
            print(f"[{location_name}] Manifest-Bild: {image_name}")
            return manifest

        if latest_path and latest_path.exists():
            print(f"[{location_name}] Manifest-Bild fehlt, nutze latest: {latest_name}")
            manifest["image"] = latest_name
            return manifest

        print(f"[{location_name}] Manifest vorhanden, aber referenzierte Bilder fehlen – nutze Fallback")

    return _fallback_manifest(location_name, location_label)


def _item_pubdate(manifest, now_dt):
    target_date = manifest.get("target_date")
    week_monday = manifest.get("week_monday")
    generated_at_utc = manifest.get("generated_at_utc")

    if target_date:
        try:
            dt = datetime.fromisoformat(f"{target_date}T06:00:00+00:00")
            return _rfc2822(dt)
        except ValueError:
            pass

    if week_monday:
        try:
            dt = datetime.fromisoformat(f"{week_monday}T06:00:00+00:00")
            return _rfc2822(dt)
        except ValueError:
            pass

    parsed = _parse_iso_dt(generated_at_utc)
    if parsed:
        return _rfc2822(parsed)

    return _rfc2822(now_dt)


def _item_title(manifest, location_label, location_name):
    display_mode = manifest.get("display_mode", "")
    label = manifest.get("label", "")
    target_day_label = manifest.get("target_day_label", "")
    target_date = manifest.get("target_date", "")

    if location_name == "all_main":
        suffix = target_day_label or target_date or label or "Tagesübersicht"
        return f"Alle Hauptgerichte {suffix} – Regensburg"

    if display_mode == "day":
        suffix = target_day_label or target_date or label or "Tagesplan"
        return f"Tagesplan {suffix} – {location_label}"

    if label:
        return f"Wochenspeiseplan {label} – {location_label}"

    return f"Speiseplan – {location_label}"


def _channel_title(location_name, location_label):
    if location_name == "all_main":
        return "Kantinenvergleich Regensburg – Alle Hauptgerichte"
    return f"{location_label} – Speiseplan"


def _php_header_lines():
    return [
        "<?php",
        'header("Content-Type: application/rss+xml; charset=utf-8");',
        'header("Cache-Control: no-cache, no-store, must-revalidate");',
        'header("Pragma: no-cache");',
        'header("Expires: 0");',
        'echo \'<?xml version="1.0" encoding="UTF-8"?>\';',
        "?>",
    ]


def _image_url_for_manifest(manifest, use_http=False):
    imgurl = IMAGES_URL_HTTP if use_http else IMAGES_URL
    now_dt = _now_utc()
    image_name = manifest["image"]
    cache_token = manifest.get("generated_at_utc", now_dt.isoformat().replace("+00:00", "Z"))
    cache_token = quote(cache_token, safe="")
    return f"{imgurl}/{quote(image_name)}?v={cache_token}"


def _build_item_lines(manifest, location_name, location_label, use_http=False):
    base = BASE_URL_HTTP if use_http else BASE_URL
    now_dt = _now_utc()

    img_url = _image_url_for_manifest(manifest, use_http=use_http)
    item_title = _item_title(manifest, location_label, location_name)
    item_pub_rfc = _item_pubdate(manifest, now_dt)

    label = manifest.get("label", "")
    description_html = (
        f'<img src="{img_url}" alt="{location_label} Speiseplan {label or manifest["image"]}"/>'
    )
    description_xml = html.escape(description_html, quote=True)

    return [
        "\t\t<item>",
        f"\t\t\t<title>{html.escape(item_title)}</title>",
        f"\t\t\t<link>{base}</link>",
        f"\t\t\t<guid isPermaLink=\"false\">{html.escape(location_name)}::{html.escape(manifest.get('image',''))}::{html.escape(manifest.get('generated_at_utc',''))}</guid>",
        f"\t\t\t<description>{description_xml}</description>",
        f"\t\t\t<pubDate>{item_pub_rfc}</pubDate>",
        f'\t\t\t<media:content url="{html.escape(img_url, quote=True)}" type="image/jpeg" height="{FRAME_HEIGHT}" width="{FRAME_WIDTH}"/>',
        "\t\t</item>",
    ]


def build_feed(manifest, location_name, location_label, use_http=False, php_header=False):
    base = BASE_URL_HTTP if use_http else BASE_URL
    now_dt = _now_utc()
    now_rfc = _rfc2822(now_dt)

    channel_title = _channel_title(location_name, location_label)

    lines = []
    if php_header:
        lines.extend(_php_header_lines())
    else:
        lines.append('<?xml version="1.0" encoding="UTF-8"?>')

    lines += [
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">',
        "\t<channel>",
        f"\t\t<title>{html.escape(channel_title)}</title>",
        f"\t\t<link>{base}</link>",
        "\t\t<description></description>",
        f"\t\t<pubDate>{now_rfc}</pubDate>",
        f"\t\t<lastBuildDate>{now_rfc}</lastBuildDate>",
        f"\t\t<generator>{base}</generator>",
        "\t\t<image>",
        f"\t\t\t<url>{base}/images/icon.jpg</url>",
        f"\t\t\t<title>{html.escape(channel_title)}</title>",
        f"\t\t\t<link>{base}</link>",
        "\t\t</image>",
    ]

    lines += _build_item_lines(manifest, location_name, location_label, use_http=use_http)

    lines += [
        "\t</channel>",
        "</rss>",
        "",
    ]
    return "\n".join(lines)


def build_combined_feed(manifests_by_location, use_http=False, php_header=False):
    base = BASE_URL_HTTP if use_http else BASE_URL
    now_dt = _now_utc()
    now_rfc = _rfc2822(now_dt)

    lines = []
    if php_header:
        lines.extend(_php_header_lines())
    else:
        lines.append('<?xml version="1.0" encoding="UTF-8"?>')

    lines += [
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">',
        "\t<channel>",
        f"\t\t<title>{html.escape(COMBINED_FEED_NAME)}</title>",
        f"\t\t<link>{base}</link>",
        "\t\t<description>Alle aktuellen Kantinen-Speisepläne in einem Feed.</description>",
        f"\t\t<pubDate>{now_rfc}</pubDate>",
        f"\t\t<lastBuildDate>{now_rfc}</lastBuildDate>",
        f"\t\t<generator>{base}</generator>",
        "\t\t<image>",
        f"\t\t\t<url>{base}/images/icon.jpg</url>",
        f"\t\t\t<title>{html.escape(COMBINED_FEED_NAME)}</title>",
        f"\t\t\t<link>{base}</link>",
        "\t\t</image>",
    ]

    for location_name in ["schaeffler", "aumovio", "siemens"]:
        manifest = manifests_by_location.get(location_name)
        if not manifest:
            continue
        location_label = LOCATIONS[location_name]
        lines += _build_item_lines(manifest, location_name, location_label, use_http=use_http)

    lines += [
        "\t</channel>",
        "</rss>",
        "",
    ]
    return "\n".join(lines)


def main():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    resolved_manifests = {}

    for loc_name, loc_label in LOCATIONS.items():
        manifest = _resolve_manifest(loc_name, loc_label)
        if not manifest:
            continue

        resolved_manifests[loc_name] = manifest

        xml_path = DOCS_DIR / f"feed_{loc_name}.xml"
        php_path = DOCS_DIR / f"feed_{loc_name}.php"

        xml_path.write_text(
            build_feed(manifest, loc_name, loc_label, use_http=False, php_header=False),
            encoding="utf-8"
        )
        print(f"[{loc_name}] feed_{loc_name}.xml geschrieben")

        php_path.write_text(
            build_feed(manifest, loc_name, loc_label, use_http=True, php_header=True),
            encoding="utf-8"
        )
        print(f"[{loc_name}] feed_{loc_name}.php geschrieben")

    special_name = "all_main"
    special_label = SPECIAL_LOCATION[special_name]
    special_manifest = _resolve_manifest(special_name, special_label)
    if special_manifest:
        xml_path = DOCS_DIR / "feed_all_main.xml"
        php_path = DOCS_DIR / "feed_all_main.php"

        xml_path.write_text(
            build_feed(special_manifest, special_name, special_label, use_http=False, php_header=False),
            encoding="utf-8"
        )
        print("[all_main] feed_all_main.xml geschrieben")

        php_path.write_text(
            build_feed(special_manifest, special_name, special_label, use_http=True, php_header=True),
            encoding="utf-8"
        )
        print("[all_main] feed_all_main.php geschrieben")
    else:
        print("[all_main] Kein Manifest/Bild vorhanden – Spezialfeed wird nicht erzeugt")

    if resolved_manifests:
        all_xml_path = DOCS_DIR / "feed_all.xml"
        all_php_path = DOCS_DIR / "feed_all.php"

        all_xml_path.write_text(
            build_combined_feed(resolved_manifests, use_http=False, php_header=False),
            encoding="utf-8"
        )
        print("[all] feed_all.xml geschrieben")

        all_php_path.write_text(
            build_combined_feed(resolved_manifests, use_http=True, php_header=True),
            encoding="utf-8"
        )
        print("[all] feed_all.php geschrieben")
    else:
        print("[all] Keine Manifeste vorhanden – Sammelfeed wird nicht erzeugt")


if __name__ == "__main__":
    main()
