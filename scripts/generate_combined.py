#!/usr/bin/env python3
"""
Generate one combined 800x600 overview image with all main dishes
from Schaeffler, Aumovio and Siemens.

Source files expected in docs/images/:
- menu_schaeffler.json
- menu_aumovio.json
- menu_siemens.json

Output:
- docs/images/kantine_<label>_<date>_all_main.jpg
- docs/images/latest_all_main.jpg
- docs/images/current_all_main.json
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path
from datetime import date, datetime, timezone, timedelta

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path("docs/images")
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = 800, 600
FOOTER_H = 20
MAX_KEEP = 3

LOCATIONS = [
    {
        "name": "schaeffler",
        "label": "SCHAEFFLER",
        "fallback_primary": "#00893D",
        "fallback_secondary": "#404547",
        "fallback_price": "#00893D",
    },
    {
        "name": "aumovio",
        "label": "AUMOVIO",
        "fallback_primary": "#FF4208",
        "fallback_secondary": "#333333",
        "fallback_price": "#FF4208",
    },
    {
        "name": "siemens",
        "label": "SIEMENS",
        "fallback_primary": "#009999",
        "fallback_secondary": "#000000",
        "fallback_price": "#EC6602",
    },
]

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
TEXT = (35, 35, 35)
TEXT_MUTED = (95, 95, 95)
HEADER_BG = (32, 32, 32)
GRID = (215, 215, 215)
BG = (248, 248, 248)
VG_COLOR = (0, 150, 90)
V_COLOR = (230, 140, 0)

GERMAN_DAY_LONG = [
    "Montag", "Dienstag", "Mittwoch", "Donnerstag",
    "Freitag", "Samstag", "Sonntag"
]

_FREG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_FBOL = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1, c2, t=0.5):
    return tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))


def lf(size, bold=False):
    for p in (_FBOL if bold else _FREG) + _FREG:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    return ImageFont.load_default()


def german_time(dt):
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Berlin"))
    except ImportError:
        pass

    import calendar

    yr = dt.year

    def last_sun(y, m):
        ld = calendar.monthrange(y, m)[1]
        d2 = datetime(y, m, ld, tzinfo=timezone.utc)
        return d2 - timedelta(days=(d2.weekday() + 1) % 7)

    cs = last_sun(yr, 3).replace(hour=1)
    ce = last_sun(yr, 10).replace(hour=1)
    return dt + timedelta(hours=2 if cs <= dt < ce else 1)


def _line_h(draw, font):
    b = draw.textbbox((0, 0), "Ag", font=font)
    return b[3] - b[1] + 4


def _split_chars(draw, word, font, max_w):
    parts = []
    while word:
        lo, hi = 1, len(word)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            test = word[:mid] + ("-" if mid < len(word) else "")
            b = draw.textbbox((0, 0), test, font=font)
            if b[2] - b[0] <= max_w:
                lo = mid
            else:
                hi = mid - 1
        if lo <= 0:
            lo = 1
        chunk = word[:lo]
        word = word[lo:]
        parts.append(chunk + ("-" if word else ""))
    return parts


def _split_long_word(draw, word, font, max_w):
    if "-" in word:
        parts = word.split("-")
        chunks = []
        cur = ""
        for i, part in enumerate(parts):
            candidate = (cur + "-" + part) if cur else part
            test = candidate + ("-" if i < len(parts) - 1 else "")
            b = draw.textbbox((0, 0), test, font=font)
            if b[2] - b[0] <= max_w:
                cur = candidate
            else:
                if cur:
                    chunks.append(cur + "-")
                cur = part
        if cur:
            chunks.append(cur)

        result = []
        for chunk in chunks:
            b = draw.textbbox((0, 0), chunk.rstrip("-"), font=font)
            if b[2] - b[0] > max_w:
                result.extend(_split_chars(draw, chunk.rstrip("-"), font, max_w))
                if chunk.endswith("-") and result:
                    result[-1] = result[-1].rstrip("-") + "-"
            else:
                result.append(chunk)
        return result

    return _split_chars(draw, word, font, max_w)


def wrap_text(draw, text, font, max_w, max_lines=20):
    tokens = []
    for word in (text or "").split():
        b = draw.textbbox((0, 0), word, font=font)
        if b[2] - b[0] > max_w:
            tokens.extend(_split_long_word(draw, word, font, max_w))
        else:
            tokens.append(word)

    out = []
    cur = []
    for tok in tokens:
        test = " ".join(cur + [tok])
        b = draw.textbbox((0, 0), test, font=font)
        if b[2] - b[0] <= max_w:
            cur.append(tok)
        else:
            if cur:
                out.append(" ".join(cur))
            cur = [tok]

    if cur:
        out.append(" ".join(cur))

    return out[:max_lines]


def _trim_to_width(draw, text, font, max_w):
    text = (text or "").rstrip()
    if not text:
        return text

    b = draw.textbbox((0, 0), text, font=font)
    if b[2] - b[0] <= max_w:
        return text

    base = text.rstrip(" .,:;-")
    while base:
        candidate = base + "…"
        b = draw.textbbox((0, 0), candidate, font=font)
        if b[2] - b[0] <= max_w:
            return candidate
        base = base[:-1].rstrip()

    return "…"


def _limit_lines(draw, lines, font, max_w, max_lines):
    if len(lines) <= max_lines:
        return lines
    out = lines[:max_lines]
    out[-1] = _trim_to_width(draw, out[-1], font, max_w)
    return out


def _fit_font(draw, text, max_w, max_h, size_start=18, size_min=8):
    for size in range(size_start, size_min - 1, -1):
        font = lf(size)
        lh = _line_h(draw, font)
        lines = wrap_text(draw, text, font, max_w, max_lines=20)
        if not lines:
            return font, lh, lines
        if lh * len(lines) <= max_h:
            return font, lh, lines

    font = lf(size_min)
    return font, _line_h(draw, font), wrap_text(draw, text, font, max_w, max_lines=20)


def _draw_footer_bar(draw, text, font):
    draw.rectangle([(0, H - FOOTER_H), (W, H)], fill=HEADER_BG)
    b = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((W - (b[2] - b[0])) // 2, H - FOOTER_H + (FOOTER_H - (b[3] - b[1])) // 2),
        text,
        font=font,
        fill=WHITE,
    )


def _load_source(location_name):
    path = OUT_DIR / f"menu_{location_name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Source file missing: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not read {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid JSON structure in {path}")

    data["_path"] = str(path)
    return data


def _validate_sources(sources):
    if not sources:
        raise RuntimeError("No sources loaded")

    ref_date = sources[0].get("target_date", "")
    ref_label = sources[0].get("label", "")
    ref_kw = sources[0].get("kw")
    ref_mode = sources[0].get("display_mode", "")

    if ref_mode != "day":
        raise RuntimeError("Combined image only supports display_mode=day")

    for src in sources[1:]:
        if src.get("display_mode") != "day":
            raise RuntimeError(f"{src.get('_path')} is not a day source")
        if src.get("target_date") != ref_date:
            raise RuntimeError(
                f"target_date mismatch: {src.get('_path')} has {src.get('target_date')} != {ref_date}"
            )
        if src.get("label") != ref_label:
            raise RuntimeError(
                f"label mismatch: {src.get('_path')} has {src.get('label')} != {ref_label}"
            )
        if src.get("kw") != ref_kw:
            raise RuntimeError(
                f"kw mismatch: {src.get('_path')} has {src.get('kw')} != {ref_kw}"
            )

    try:
        target_date = date.fromisoformat(ref_date)
    except ValueError as exc:
        raise RuntimeError(f"Invalid target_date: {ref_date}") from exc

    target_day_label = sources[0].get("target_day_label", "")
    return target_date, target_day_label, ref_label, int(ref_kw)


def _theme_for_source(src, loc):
    theme = src.get("theme", {}) or {}
    return {
        "primary": hex_rgb(theme.get("primary", loc["fallback_primary"])),
        "secondary": hex_rgb(theme.get("secondary", loc["fallback_secondary"])),
        "price": hex_rgb(theme.get("price", loc["fallback_price"])),
    }


def _draw_badge(draw, text, color, x, y):
    font = lf(9, bold=True)
    b = draw.textbbox((0, 0), text, font=font)
    bw = b[2] - b[0] + 8
    bh = b[3] - b[1] + 4
    draw.rounded_rectangle([(x, y), (x + bw, y + bh)], radius=3, fill=color)
    draw.text((x + 4, y + 1), text, font=font, fill=WHITE)
    return bw, bh


def _draw_dish_card(draw, dish, x0, y0, x1, y1, theme, *, name_size_boost=0, name_size_min=None):
    pad = 6
    price_font = lf(13, bold=True)
    cat_font = lf(10, bold=True)
    name_max_w = max(20, x1 - x0 - 2 * pad)

    draw.rounded_rectangle(
        [(x0, y0), (x1, y1)],
        radius=7,
        fill=WHITE,
        outline=mix(theme["primary"], WHITE, 0.35),
        width=1,
    )

    cy = y0 + pad

    category = (dish.get("category") or "Hauptgericht").strip()
    category = re.sub(r"\s+", " ", category)
    draw.text((x0 + pad, cy), category, font=cat_font, fill=theme["secondary"])

    badge_text = ""
    badge_color = None
    vv = (dish.get("vv") or "").strip().upper()
    if vv == "VG":
        badge_text = "Vegan"
        badge_color = VG_COLOR
    elif vv == "V":
        badge_text = "Veg."
        badge_color = V_COLOR

    if badge_text:
        bw, _ = _draw_badge(draw, badge_text, badge_color, x1 - pad - 52, cy - 1)
    cy += _line_h(draw, cat_font)

    price = (dish.get("price") or "").strip()
    price_reserved_h = (_line_h(draw, price_font) + 2) if price else 0

    name_bottom = y1 - pad - price_reserved_h
    name_max_h = max(12, name_bottom - cy)

    max_lines = 3 if (y1 - y0) >= 78 else 2
    start_size = (18 if (y1 - y0) >= 76 else 15) + name_size_boost
    size_min = name_size_min if name_size_min is not None else 8
    
    name_font, name_lh, name_lines = _fit_font(
        draw,
        re.sub(r"\s+", " ", (dish.get("name") or "").strip()),
        name_max_w,
        name_max_h,
        size_start=start_size,
        size_min=size_min,
    )
    name_lines = _limit_lines(draw, name_lines, name_font, name_max_w, max_lines)

    for line in name_lines:
        if cy + name_lh > name_bottom:
            break
        draw.text((x0 + pad, cy), line, font=name_font, fill=TEXT)
        cy += name_lh

    if price:
        pb = draw.textbbox((0, 0), price, font=price_font)
        draw.text(
            (x1 - pad - (pb[2] - pb[0]), y1 - pad - (pb[3] - pb[1])),
            price,
            font=price_font,
            fill=theme["price"],
        )

def _filtered_overview_dishes(src):
    dishes = src.get("main_dishes") or []
    filtered_dishes = []
    for dish in dishes:
        category = re.sub(r"\s+", " ", (dish.get("category") or "").strip()).casefold()

        if not category:
            filtered_dishes.append(dish)
            continue
        if "suppe" in category:
            continue
        if "salat" in category:
            continue
        if "dessert" in category:
            continue

        filtered_dishes.append(dish)

    return filtered_dishes


def render_combined(sources_by_name, target_date, label, kw, local_dt):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    ftit = lf(21, True)
    fsub = lf(13)
    fsmall = lf(10)
    fleg = lf(11)

    HEADER_H = 64
    LEGEND_H = 22
    GAP = 6
    COL_HEAD_H = 34
    PAD = 6

    draw.rectangle([(0, 0), (W, HEADER_H)], fill=HEADER_BG)

    title = "Alle Hauptgerichte – Regensburg"
    subtitle = f"{GERMAN_DAY_LONG[target_date.weekday()]}, {target_date.strftime('%d.%m.%Y')}  |  KW {kw:02d}"

    bt = draw.textbbox((0, 0), title, font=ftit)
    bs = draw.textbbox((0, 0), subtitle, font=fsub)
    total_h = (bt[3] - bt[1]) + 3 + (bs[3] - bs[1])
    ty = (HEADER_H - total_h) // 2

    draw.text(((W - (bt[2] - bt[0])) // 2, ty), title, font=ftit, fill=WHITE)
    draw.text(((W - (bs[2] - bs[0])) // 2, ty + (bt[3] - bt[1]) + 3), subtitle, font=fsub, fill=(220, 220, 220))

    content_top = HEADER_H + GAP
    content_bottom = H - FOOTER_H - LEGEND_H - 4
    content_h = content_bottom - content_top

    col_w = (W - GAP * 4) // 3

    for idx, loc in enumerate(LOCATIONS):
        src = sources_by_name[loc["name"]]
        theme = _theme_for_source(src, loc)
        dishes = _filtered_overview_dishes(src)
        
        is_holiday = src.get("is_holiday", "")

        x0 = GAP + idx * (col_w + GAP)
        x1 = x0 + col_w
        y0 = content_top
        y1 = content_bottom

        col_bg = mix(theme["primary"], WHITE, 0.93)
        draw.rounded_rectangle([(x0, y0), (x1, y1)], radius=8, fill=col_bg, outline=GRID)

        draw.rounded_rectangle([(x0, y0), (x1, y0 + COL_HEAD_H)], radius=8, fill=theme["primary"])
        draw.rectangle([(x0, y0 + COL_HEAD_H - 6), (x1, y0 + COL_HEAD_H)], fill=theme["primary"])

        fcol = lf(13, True)
        fcnt = lf(10)

        label_text = loc["label"]
        count_text = (
            is_holiday and "Feiertag"
        ) or (
            f"{len(dishes)} Hauptgerichte" if dishes else "Keine Hauptgerichte"
        )

        bl = draw.textbbox((0, 0), label_text, font=fcol)
        bc = draw.textbbox((0, 0), count_text, font=fcnt)
        total_text_h = (bl[3] - bl[1]) + 1 + (bc[3] - bc[1])
        tyy = y0 + (COL_HEAD_H - total_text_h) // 2

        draw.text((x0 + PAD, tyy), label_text, font=fcol, fill=WHITE)
        draw.text((x0 + PAD, tyy + (bl[3] - bl[1]) + 1), count_text, font=fcnt, fill=(235, 235, 235))

        inner_top = y0 + COL_HEAD_H + 6
        inner_bottom = y1 - 6
        inner_h = inner_bottom - inner_top

        if is_holiday:
            msg = f"Feiertag:\n{is_holiday}"
            fmsg, lhm, lines = _fit_font(draw, msg, col_w - 20, inner_h - 10, size_start=20, size_min=11)
            lines = _limit_lines(draw, lines, fmsg, col_w - 20, 3)
            block_h = lhm * len(lines)
            cy = inner_top + (inner_h - block_h) // 2
            for line in lines:
                bb = draw.textbbox((0, 0), line, font=fmsg)
                draw.text((x0 + (col_w - (bb[2] - bb[0])) // 2, cy), line, font=fmsg, fill=TEXT_MUTED)
                cy += lhm
            continue

        if not dishes:
            msg = "Keine Hauptgerichte\nverfügbar"
            fmsg, lhm, lines = _fit_font(draw, msg, col_w - 20, inner_h - 10, size_start=18, size_min=10)
            lines = _limit_lines(draw, lines, fmsg, col_w - 20, 3)
            block_h = lhm * len(lines)
            cy = inner_top + (inner_h - block_h) // 2
            for line in lines:
                bb = draw.textbbox((0, 0), line, font=fmsg)
                draw.text((x0 + (col_w - (bb[2] - bb[0])) // 2, cy), line, font=fmsg, fill=TEXT_MUTED)
                cy += lhm
            continue

        row_gap = 5
        row_h = max(40, (inner_h - row_gap * (len(dishes) - 1)) // max(len(dishes), 1))

        for i, dish in enumerate(dishes):
            ry0 = inner_top + i * (row_h + row_gap)
            ry1 = ry0 + row_h
            if ry1 > inner_bottom:
                break
            if loc["name"] == "aumovio":
                _draw_dish_card(
                    draw,
                    dish,
                    x0 + 5,
                    ry0,
                    x1 - 5,
                    ry1,
                    theme,
                    name_size_boost=6,
                    name_size_min=12,
                )
            else:
                _draw_dish_card(draw, dish, x0 + 5, ry0, x1 - 5, ry1, theme)

    leg_y = H - FOOTER_H - LEGEND_H - 2
    draw.line([(0, leg_y), (W, leg_y)], fill=GRID, width=1)
    leg_y += 1
    draw.rectangle([(0, leg_y), (W, leg_y + LEGEND_H)], fill=WHITE)

    lx = 6
    for col, txt in [
        (VG_COLOR, "Vegan"),
        (V_COLOR, "Vegetarisch"),
    ]:
        draw.rectangle([(lx, leg_y + 5), (lx + 12, leg_y + 15)], fill=col)
        b = draw.textbbox((0, 0), txt, font=fleg)
        draw.text((lx + 15, leg_y + 4), txt, font=fleg, fill=TEXT)
        lx += 15 + (b[2] - b[0]) + 14

    footer_txt = (
        f"KW {kw:02d} / {label}  –  "
        f"erstellt am {local_dt.strftime('%d.%m.%Y %H:%M Uhr')}  –  "
        f"nur Hauptgerichte (ohne Suppe, Salatbar, Dessert)"
    )
    _draw_footer_bar(draw, footer_txt, fsmall)

    return img


def _prune_old_images(location_name, keep=MAX_KEEP):
    pattern = f"kantine_*_{location_name}.jpg"
    files = sorted(OUT_DIR.glob(pattern), key=lambda p: p.stat().st_mtime)
    while len(files) > keep:
        old = files.pop(0)
        try:
            old.unlink()
            print(f"Removed: {old}")
        except FileNotFoundError:
            pass


def _write_current_manifest(
    *,
    location_name,
    location_label,
    display_mode,
    label,
    kw,
    out_path,
    latest_path,
    local_dt,
    target_date,
    target_day_label,
    dish_count,
):
    manifest_path = OUT_DIR / f"current_{location_name}.json"
    week_monday = target_date - timedelta(days=target_date.weekday())

    data = {
        "location_name": location_name,
        "location_label": location_label,
        "display_mode": display_mode,
        "label": label,
        "kw": kw,
        "image": out_path.name,
        "latest": latest_path.name,
        "dish_count": dish_count,
        "is_holiday": "",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "generated_at_local": local_dt.isoformat(),
        "target_date": target_date.isoformat(),
        "target_day_label": target_day_label,
        "week_monday": week_monday.isoformat(),
    }

    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")


def main():
    now = datetime.now(timezone.utc)
    local = german_time(now)

    loaded = []
    sources_by_name = {}

    for loc in LOCATIONS:
        src = _load_source(loc["name"])
        loaded.append(src)
        sources_by_name[loc["name"]] = src
        print(f"[load] {loc['name']}: {src.get('_path')}")

    target_date, target_day_label, label, kw = _validate_sources(loaded)
    print(f"[meta] target_date={target_date} target_day_label={target_day_label!r} label={label} kw={kw}")

    img = render_combined(sources_by_name, target_date, label, kw, local)

    out_path = OUT_DIR / f"kantine_{label}_{target_date.strftime('%Y-%m-%d')}_all_main.jpg"
    latest_path = OUT_DIR / "latest_all_main.jpg"

    img.save(str(out_path), "JPEG", quality=92)
    print(f"Saved: {out_path}")

    shutil.copy(str(out_path), str(latest_path))
    print(f"{latest_path.name} aktualisiert")

    # in main()
    dish_count = sum(
        len(_filtered_overview_dishes(sources_by_name[loc["name"]]))
        for loc in LOCATIONS
    )

    _write_current_manifest(
        location_name="all_main",
        location_label="ALLE Hauptgerichte Regensburg",
        display_mode="day",
        label=label,
        kw=kw,
        out_path=out_path,
        latest_path=latest_path,
        local_dt=local,
        target_date=target_date,
        target_day_label=target_day_label or "",
        dish_count=dish_count,
    )

    _prune_old_images("all_main")


if __name__ == "__main__":
    main()
