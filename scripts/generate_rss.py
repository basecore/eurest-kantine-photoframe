#!/usr/bin/env python3
"""Generiert RSS-Feeds fuer Philips PhotoFrame.

feed_<location>.php  - HTTP URLs via bplaced, .php Extension -> fuer Philips 8FF3WMI PhotoFrame
feed_<location>.xml  - HTTPS URLs via github.io, moderne Reader

Nur das NEUESTE Bild je Kantine wird in den Feed aufgenommen.
"""
from pathlib import Path
from datetime import datetime, timezone
import os

BASE_URL        = "https://basecore.github.io/eurest-kantine-photoframe"
BASE_URL_HTTP   = "http://basecore.bplaced.net/eurest"
IMAGES_URL      = f"{BASE_URL}/images"
IMAGES_URL_HTTP = f"{BASE_URL_HTTP}/images"
DOCS_DIR        = Path("docs")
IMAGES_DIR      = DOCS_DIR / "images"

FRAME_WIDTH  = 800
FRAME_HEIGHT = 600

LOCATIONS = {
    "schaeffler": "SCHAEFFLER Regensburg",
    "aumovio":    "AUMOVIO Regensburg",
}

def build_feed(images, location_name, location_label, use_http=False, php_header=False):
    base    = BASE_URL_HTTP   if use_http else BASE_URL
    imgurl  = IMAGES_URL_HTTP if use_http else IMAGES_URL
    now_rfc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    title   = f"Eurest Kantine {location_label} Regensburg – Wochenspeiseplan"

    lines = []
    if php_header:
        lines.append('<?php')
        lines.append('header("Content-Type: application/rss+xml; charset=utf-8");')
        lines.append('header("Cache-Control: no-cache, no-store, must-revalidate");')
        lines.append('header("Pragma: no-cache");')
        lines.append('header("Expires: 0");')
        lines.append('echo \'<?xml version="1.0" encoding="ISO-8859-1"?>\';')
        lines.append('?>')
    else:
        lines.append('<?xml version="1.0" encoding="ISO-8859-1"?>')

    lines += [
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">',
        '\t<channel>',
        f'\t\t<title>{title}</title>',
        f'\t\t<link>{base}</link>',
        '\t\t<description></description>',
        f'\t\t<pubDate>{now_rfc}</pubDate>',
        f'\t\t<lastBuildDate>{now_rfc}</lastBuildDate>',
        f'\t\t<generator>{base}</generator>',
        '\t\t<image>',
        f'\t\t\t<url>{base}/images/icon.jpg</url>',
        f'\t\t\t<title>{title}</title>',
        f'\t\t\t<link>{base}</link>',
        '\t\t</image>',
    ]

    for img_path in images:
        img_url = f"{imgurl}/{img_path.name}"
        stem = img_path.stem  # e.g. kantine_2026-W27_schaeffler
        parts = stem.split('_')
        week_label = parts[1] if len(parts) >= 2 else stem
        try:
            year, week = week_label.split("-W")
            import datetime as dt_module
            monday = dt_module.datetime.fromisocalendar(int(year), int(week), 1)
            pub_rfc = monday.strftime("%a, %d %b %Y 06:00:00 +0000")
        except (ValueError, AttributeError):
            pub_rfc = now_rfc

        description = (
            f'&lt;img src=&quot;{img_url}&quot; '
            f'alt=&quot;{location_label} Speiseplan {week_label}&quot;/&gt;'
        )
        lines += [
            '\t\t<item>',
            f'\t\t<title>Speiseplan {week_label} – {location_label}</title>',
            f'\t\t<link>{base}</link>',
            f'\t\t<description>{description}</description>',
            f'\t\t<pubDate>{pub_rfc}</pubDate>',
            f'\t\t<media:content url="{img_url}" type="image/jpeg" height="{FRAME_HEIGHT}" width="{FRAME_WIDTH}"/>',
            '\t\t</item>',
        ]

    lines += ['\t</channel>', '</rss>', '']
    return "\n".join(lines)


def main():
    for loc_name, loc_label in LOCATIONS.items():
        pattern = f"kantine_*_{loc_name}.jpg"
        all_images = sorted(IMAGES_DIR.glob(pattern), reverse=True)
        if not all_images:
            print(f"[{loc_name}] Keine Bilder gefunden – ueberspringe")
            continue

        images = [all_images[0]]
        print(f"[{loc_name}] Neuestes Bild: {images[0].name} (gesamt: {len(all_images)})")

        xml_path = DOCS_DIR / f"feed_{loc_name}.xml"
        php_path = DOCS_DIR / f"feed_{loc_name}.php"

        xml_path.write_text(build_feed(images, loc_name, loc_label, use_http=False), encoding="utf-8")
        print(f"  feed_{loc_name}.xml geschrieben")

        php_path.write_text(build_feed(images, loc_name, loc_label, use_http=True, php_header=True), encoding="utf-8")
        print(f"  feed_{loc_name}.php geschrieben")


if __name__ == "__main__":
    main()
