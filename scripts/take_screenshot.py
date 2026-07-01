#!/usr/bin/env python3
"""Eurest Kantine Regensburg – Schaeffler & Aumovio – 800x600 JPEG.

Scraping-Strategie:
  1. Direktlink zur Mandanten-Instanz oeffnen.
  2. locationSelection auf EUREST_LOCATION_ID setzen.
  3. Privacy-Policy-Checkbox aktivieren + OK klicken.
  4. Sprache 'Deutsch' waehlen + 'weiter' klicken.
  5. Optionales 'persoenlicher Filter'-Modal mit 'nein' schliessen.
  6. Fuer jeden Werktag: Tages-Button klicken, DOM extrahieren.
  7. Render: 800x600 JPEG im gleichen Stil wie siemens-kantine-photoframe.

Umgebungsvariablen:
  EUREST_LOCATION_ID    8949 = Schaeffler, 8950 = Aumovio (default: 8949)
  EUREST_LOCATION_NAME  schaeffler / aumovio (default: schaeffler)
  WEEK_OFFSET           0 = aktuelle Woche, 1 = naechste Woche (default: 1)
"""
import os
import re
import sys
import json
from pathlib import Path
from datetime import date, datetime, timezone, timedelta

from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont

# ── Config ─────────────────────────────────────────────────────────────────────
EUREST_URL      = "https://eurest.webspeiseplan.de/39799C127F748D639984F4CDBEB44846"
LOCATION_ID     = os.environ.get("EUREST_LOCATION_ID", "8949").strip()
LOCATION_NAME   = os.environ.get("EUREST_LOCATION_NAME", "schaeffler").strip().lower()
WEEK_OFFSET     = int(os.environ.get("WEEK_OFFSET", "1"))

LOCATION_LABELS = {
    "8949": "SCHAEFFLER Regensburg",
    "8950": "AUMOVIO Regensburg",
}
LOCATION_LABEL = LOCATION_LABELS.get(LOCATION_ID, f"Kantine {LOCATION_ID}")

OUT_DIR  = Path("docs/images")
OUT_DIR.mkdir(parents=True, exist_ok=True)
MAX_KEEP = 8
W, H     = 800, 600
FOOTER_H = 20

# ── Colours ────────────────────────────────────────────────────────────────────
BLUE      = (0, 57, 107)
LIGHT     = (0, 119, 193)
R_ODD     = (240, 246, 252)
R_EVEN    = (255, 255, 255)
C_VG      = (34, 139, 34)
C_V       = (100, 180, 60)
C_TXT     = (30, 30, 30)
WHITE     = (255, 255, 255)
GRID      = (190, 210, 230)
C_HOL_BG  = (220, 220, 220)
C_HOL_HDR = (140, 140, 140)
C_HOL_TXT = (100, 100, 100)
C_PAST_BG = (235, 235, 235)
C_PAST_TXT= (160, 160, 160)
C_TODAY   = (255, 200, 0)
C_TODAY_HDR=(220, 150, 0)

_FREG = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
_FBOL = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]

def lf(size, bold=False):
    for p in (_FBOL if bold else _FREG) + _FREG:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except OSError: pass
    return ImageFont.load_default()

# ── Bayerische Feiertage ───────────────────────────────────────────────────────
def _easter(y):
    a=y%19;b=y//100;c=y%100;d=b//4;e=b%4;f=(b+8)//25;g=(b-f+1)//3
    h=(19*a+b-d-g+15)%30;i=c//4;k=c%4;l=(32+2*e+2*i-h-k)%7
    m=(a+11*h+22*l)//451;mo=(h+l-7*m+114)//31;dy=((h+l-7*m+114)%31)+1
    return date(y,mo,dy)

def bavaria_holidays(y):
    e=_easter(y)
    return {
        date(y,1,1):"Neujahr", date(y,1,6):"Hl. Drei Koenige",
        e-timedelta(2):"Karfreitag", e:"Ostersonntag", e+timedelta(1):"Ostermontag",
        date(y,5,1):"Tag der Arbeit", e+timedelta(39):"Christi Himmelfahrt",
        e+timedelta(49):"Pfingstsonntag", e+timedelta(50):"Pfingstmontag",
        e+timedelta(60):"Fronleichnam", date(y,8,15):"Mariae Himmelfahrt",
        date(y,10,3):"Tag der Deutschen Einheit", date(y,11,1):"Allerheiligen",
        date(y,12,25):"1. Weihnachtstag", date(y,12,26):"2. Weihnachtstag",
    }

def week_holiday_map(monday_date):
    y=monday_date.year; hols=bavaria_holidays(y)
    if (monday_date+timedelta(4)).year != y: hols.update(bavaria_holidays(y+1))
    short=['Mo','Di','Mi','Do','Fr']
    return {f"{short[i]} {(monday_date+timedelta(i)).strftime('%d.%m')}":
            hols.get(monday_date+timedelta(i)) for i in range(5)}

# ── Zeit-Helfer ────────────────────────────────────────────────────────────────
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
        return d2 - timedelta(days=(d2.weekday()+1) % 7)
    cs = last_sun(yr, 3).replace(hour=1)
    ce = last_sun(yr, 10).replace(hour=1)
    return dt + timedelta(hours=2 if cs <= dt < ce else 1)

def kw_label(dt):
    d = german_time(dt)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}", int(w)

def day_key(dt_obj):
    return f"{'Mo Di Mi Do Fr'.split()[dt_obj.weekday()]} {dt_obj.strftime('%d.%m')}"

def _is_past(day_key_str, today_date):
    try:
        dm = day_key_str.split(' ')[1].split('.')
        return date(today_date.year, int(dm[1]), int(dm[0])) < today_date
    except: return False

def _is_today(day_key_str, today_date):
    try:
        dm = day_key_str.split(' ')[1].split('.')
        return date(today_date.year, int(dm[1]), int(dm[0])) == today_date
    except: return False

# ── Eurest-Scraper ─────────────────────────────────────────────────────────────
def setup_eurest(page):
    """Oeffnet die Eurest-Seite, waehlt Kantine, bestaetigt Privacy, Sprache, Weiter."""
    print(f"[setup] Oeffne {EUREST_URL}")
    page.goto(EUREST_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2000)

    # Kantine auswaehlen
    print(f"[setup] Waehle location {LOCATION_ID} ({LOCATION_LABEL})")
    try:
        page.select_option("select.locationSelection", value=LOCATION_ID, timeout=8000)
        page.wait_for_timeout(1500)
    except Exception as e:
        print(f"[setup] locationSelection Fehler: {e}")

    # Privacy-Policy-Checkbox + OK (erscheint beim ersten Aufruf)
    try:
        page.wait_for_selector("input#featureFilterSelective", timeout=5000)
        cb = page.query_selector("input#featureFilterSelective")
        if cb and not cb.is_checked():
            cb.click()
            print("[setup] Privacy-Checkbox aktiviert")
        page.wait_for_timeout(400)
        # OK-Button klicken
        for sel in ["button.min-w-8rem", "button:has-text('ok')", "button:has-text('OK')"]:
            try:
                page.click(sel, timeout=3000)
                print(f"[setup] OK geklickt via {sel!r}")
                break
            except: pass
        page.wait_for_timeout(1000)
    except Exception:
        print("[setup] Privacy-Modal nicht erschienen (OK)")

    # Sprache Deutsch auswaehlen
    try:
        page.select_option("select.languageSelection", value="1", timeout=5000)
        print("[setup] Sprache Deutsch gewaehlt")
        page.wait_for_timeout(500)
    except Exception as e:
        print(f"[setup] languageSelection Fehler: {e}")

    # Weiter-Button
    for sel in ["button.submit", "button:has-text('weiter')", "button:has-text('Weiter')",
                "button.uppercase", "button:has-text('next')"]:
        try:
            page.click(sel, timeout=3000)
            print(f"[setup] Weiter geklickt via {sel!r}")
            break
        except: pass
    page.wait_for_timeout(2000)

    # Optionales 'persoenlicher Filter'-Modal mit 'Nein' schliessen
    try:
        page.wait_for_selector(".ReactModal__Content", timeout=4000)
        for sel in ["button:has-text('nein')", "button:has-text('Nein')", "button:has-text('no')"]:
            try:
                page.click(sel, timeout=2000)
                print(f"[setup] Persoenlicher-Filter-Modal mit {sel!r} geschlossen")
                break
            except: pass
        page.wait_for_timeout(1000)
    except Exception:
        print("[setup] Kein persoenlicher-Filter-Modal (OK)")

    # Warten bis Speiseplan geladen
    for sel in [".category-name-container", ".meal-wrapper", ".menuListWrapper", ".defaultContainer"]:
        try:
            page.wait_for_selector(sel, timeout=12000)
            print(f"[setup] Speiseplan bereit via {sel!r}")
            break
        except: pass
    print("[setup] Abgeschlossen")


JS_EXTRACT = r"""
(function(){
  var result = [];
  var catWrappers = Array.from(document.querySelectorAll('.category-wrapper'));
  for (var cw of catWrappers) {
    var nameEl = cw.querySelector('.category-name-container');
    var catName = nameEl ? nameEl.textContent.trim() : '';
    if (!catName) continue;
    var meals = [];
    var mealEls = Array.from(cw.querySelectorAll('.meal-wrapper'));
    for (var mel of mealEls) {
      var nameNode = mel.querySelector('.mealNameWrapper');
      var mealName = nameNode ? nameNode.textContent.trim().replace(/\u00a0/g,' ') : '';
      var priceNode = mel.querySelector('.price-value');
      var price = priceNode ? priceNode.textContent.trim().replace(/\u00a0/g,' ') : '';
      // Feature-Icons (vegetarisch, vegan, schwein, ...)
      var featureImgs = Array.from(mel.querySelectorAll('.image-feature'));
      var features = featureImgs.map(function(img){ return (img.alt || img.src || '').toLowerCase(); });
      // Bild-URL
      var imgEl = mel.querySelector('.image-meal');
      var imgSrc = imgEl ? imgEl.src : '';
      if (mealName) meals.push({name: mealName, price: price, features: features, img: imgSrc});
    }
    result.push({category: catName, meals: meals});
  }
  return JSON.stringify(result);
})()
"""

def _detect_vv(name, features):
    """Erkennt vegan/vegetarisch aus Gerichtsname und Feature-Bildern."""
    low = name.lower()
    feat_str = ' '.join(features)
    if 'vegan' in low or 'vegan' in feat_str: return 'VG'
    if any(w in low for w in ['vegetarisch','vegetarische','vegetarischer']): return 'V'
    if 'vegetarisch' in feat_str: return 'V'
    return ''

def parse_eurest_dom(raw_json):
    """Parst das JSON aus JS_EXTRACT zu einer flachen Liste von Gerichten."""
    try:
        data = json.loads(raw_json)
    except Exception as e:
        print(f"  [parse] JSON-Fehler: {e}")
        return []
    dishes = []
    for cat_entry in data:
        cat = cat_entry.get('category', '').strip()
        if not cat: continue
        for m in cat_entry.get('meals', []):
            name = m.get('name', '').strip().replace('\n', ' ')
            price = m.get('price', '').strip()
            features = m.get('features', [])
            vv = _detect_vv(name, features)
            dishes.append({
                'kategorie': cat,
                'name': name,
                'preis': price,
                'vv': vv,
            })
            print(f"  [parse] {cat:20s} | vv={vv!r:3s} | {name[:45]!r} | {price!r}")
    return dishes


def click_day(page, date_label):
    """Klickt den Tages-Button fuer das angegebene Datum (z.B. '01.07.').

    Die Seite verwendet <p class="dayBtn"> mit einem Kind-<span class="dayDate">
    – kein <button>-Element. Der Klick erfolgt daher via JavaScript-Evaluation.
    """
    js = f"""
    (function(){{
      var btns = Array.from(document.querySelectorAll('p.dayBtn'));
      for (var btn of btns) {{
        var dateSpan = btn.querySelector('span.dayDate');
        if (dateSpan && dateSpan.textContent.trim() === '{date_label}') {{
          btn.click();
          return 'clicked:' + dateSpan.textContent.trim();
        }}
      }}
      // Fallback: alle Elemente mit Klasse dayBtn (falls kein <p>)
      var all = Array.from(document.querySelectorAll('.dayBtn'));
      for (var el of all) {{
        var ds = el.querySelector('.dayDate');
        if (ds && ds.textContent.trim() === '{date_label}') {{
          el.click();
          return 'clicked-fallback:' + ds.textContent.trim();
        }}
      }}
      return 'not found';
    }})()
    """
    result = page.evaluate(js)
    print(f"  [day-click] {date_label!r} -> {result!r}")
    return 'clicked' in result


def scrape_day(page, date_obj):
    """Klickt den Tag im Speiseplan und extrahiert die Gerichte via DOM."""
    date_label = date_obj.strftime('%d.%m.')
    print(f"\n[scrape] Tag {date_label}")

    # Tages-Button klicken
    if not click_day(page, date_label):
        print(f"  [scrape] Tag-Button {date_label!r} nicht gefunden, ueberspringe")
        return []

    page.wait_for_timeout(1200)

    # Warten bis Inhalte geladen
    for sel in [".meal-wrapper", ".mealNameWrapper", ".category-wrapper"]:
        try:
            page.wait_for_selector(sel, timeout=8000)
            break
        except: pass

    try:
        raw_json = page.evaluate(JS_EXTRACT)
        print(f"  [dom] JS_EXTRACT Laenge: {len(raw_json) if raw_json else 0}")
        if raw_json and raw_json != '[]':
            dishes = parse_eurest_dom(raw_json)
            print(f"  [dom] {len(dishes)} Gerichte")
            return dishes
        else:
            print("  [dom] Leer")
            return []
    except Exception as e:
        print(f"  [dom] Fehler: {e}")
        return []


# ── Render-Helfer ──────────────────────────────────────────────────────────────
def _split_long_word(draw, word, font, max_w):
    if '-' in word:
        parts = word.split('-')
        chunks = []; cur = ''
        for i, part in enumerate(parts):
            candidate = (cur + '-' + part) if cur else part
            test = candidate + ('-' if i < len(parts)-1 else '')
            b = draw.textbbox((0,0), test, font=font)
            if b[2]-b[0] <= max_w: cur = candidate
            else:
                if cur: chunks.append(cur + '-')
                cur = part
        if cur: chunks.append(cur)
        result = []
        for chunk in chunks:
            b = draw.textbbox((0,0), chunk.rstrip('-'), font=font)
            if b[2]-b[0] > max_w:
                result.extend(_split_chars(draw, chunk.rstrip('-'), font, max_w))
                if chunk.endswith('-') and result: result[-1] = result[-1].rstrip('-') + '-'
            else: result.append(chunk)
        return result
    return _split_chars(draw, word, font, max_w)

def _split_chars(draw, word, font, max_w):
    parts = []
    while word:
        lo, hi = 1, len(word)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            test = word[:mid] + ('-' if mid < len(word) else '')
            b = draw.textbbox((0,0), test, font=font)
            if b[2]-b[0] <= max_w: lo = mid
            else: hi = mid - 1
        if lo <= 0: lo = 1
        chunk = word[:lo]; word = word[lo:]
        parts.append(chunk + ('-' if word else ''))
    return parts

def wrap_text(draw, text, f, max_w, max_lines=20):
    tokens = []
    for word in text.split():
        b = draw.textbbox((0,0), word, font=f)
        if b[2]-b[0] > max_w: tokens.extend(_split_long_word(draw, word, f, max_w))
        else: tokens.append(word)
    out, cur = [], []
    for tok in tokens:
        t = ' '.join(cur + [tok])
        b = draw.textbbox((0,0), t, font=f)
        if b[2]-b[0] <= max_w: cur.append(tok)
        else:
            if cur: out.append(' '.join(cur))
            cur = [tok]
    if cur: out.append(' '.join(cur))
    return out[:max_lines]

def _line_h(draw, font):
    b = draw.textbbox((0,0), 'Ag', font=font)
    return b[3] - b[1] + 4

def _find_uniform_font_size(draw, texts, max_w, max_h, size_start=19, size_min=10, bold=False):
    for size in range(size_start, size_min - 1, -1):
        f = lf(size, bold)
        lh = _line_h(draw, f)
        all_fit = True
        for text in texts:
            if not text: continue
            lines = wrap_text(draw, text, f, max_w, max_lines=20)
            if not lines: continue
            if lh * len(lines) > max_h: all_fit = False; break
        if all_fit: return size
    return size_min

def _fit_font(draw, text, max_w, max_h, size_start=19, size_min=10, bold=False):
    for size in range(size_start, size_min - 1, -1):
        f = lf(size, bold)
        lh = _line_h(draw, f)
        lines = wrap_text(draw, text, f, max_w, max_lines=20)
        if not lines: return f, lh, lines
        if lh * len(lines) <= max_h: return f, lh, lines
    f = lf(size_min, bold)
    return f, _line_h(draw, f), wrap_text(draw, text, f, max_w, max_lines=20)


def _draw_stub_label(d, label, x0, y0, stub_w, row_h):
    for size in range(11, 6, -1):
        f = lf(size, bold=True)
        b = d.textbbox((0, 0), label, font=f)
        tw = b[2]-b[0]; th = b[3]-b[1]
        if tw <= stub_w - 4:
            d.text((x0+(stub_w-tw)//2, y0+(row_h-th)//2), label, font=f, fill=WHITE)
            return
    f = lf(6, bold=True)
    b = d.textbbox((0, 0), label, font=f)
    tw = b[2]-b[0]; th = b[3]-b[1]
    d.text((x0+(stub_w-tw)//2, y0+(row_h-th)//2), label, font=f, fill=WHITE)


# ── Render ─────────────────────────────────────────────────────────────────────
def render(week_data, kw, label, local_dt, holiday_map, today_date, monday_date):
    """Rendert das 800x600 JPEG fuer die Woche."""
    img = Image.new('RGB', (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    ftit   = lf(18, True)
    fdate  = lf(12)
    fday   = lf(17, True)
    fbdg   = lf(12, True)
    fprc   = lf(13, True)
    fftr   = lf(10)

    HDR_H    = 52
    DAY_H    = 34
    LEGEND_H = 22
    STUB_W   = 72
    TODAY_BW = 3
    PAD      = 5

    # ── Header ─────────────────────────────────────────────────────────────────
    d.rectangle([(0,0),(W,HDR_H)], fill=BLUE)
    friday_date = monday_date + timedelta(4)
    date_range  = f"{monday_date.strftime('%d.%m.%Y')} – {friday_date.strftime('%d.%m.%Y')}"
    title_str   = f"{LOCATION_LABEL}  |  KW {kw:02d}"
    bt = d.textbbox((0,0), title_str, font=ftit)
    bd = d.textbbox((0,0), date_range, font=fdate)
    total_h = (bt[3]-bt[1]) + 3 + (bd[3]-bd[1])
    ty = (HDR_H - total_h) // 2
    d.text(((W-(bt[2]-bt[0]))//2, ty), title_str, font=ftit, fill=WHITE)
    d.text(((W-(bd[2]-bd[0]))//2, ty+(bt[3]-bt[1])+3), date_range, font=fdate, fill=(180,210,240))
    y = HDR_H

    # ── Tages-Header ───────────────────────────────────────────────────────────
    all_days = list(holiday_map.keys())
    dw = (W - STUB_W) // len(all_days)
    d.rectangle([(0,y),(STUB_W-1,y+DAY_H-1)], fill=BLUE)
    for i, day in enumerate(all_days):
        x = STUB_W + i * dw
        is_hol   = holiday_map[day] is not None
        is_past  = _is_past(day, today_date)
        is_today = _is_today(day, today_date)
        if is_today:   col = C_TODAY_HDR
        elif is_hol:   col = C_HOL_HDR
        elif is_past:  col = C_PAST_TXT
        else:          col = LIGHT
        d.rectangle([(x,y),(x+dw-1,y+DAY_H-1)], fill=col)
        b = d.textbbox((0,0), day, font=fday)
        d.text((x+(dw-(b[2]-b[0]))//2, y+(DAY_H-(b[3]-b[1]))//2), day, font=fday, fill=WHITE)
        d.line([(x,y),(x,y+DAY_H)], fill=BLUE, width=1)
        if is_today:
            d.line([(x,y),(x+dw,y)], fill=C_TODAY, width=TODAY_BW)
    y += DAY_H

    # ── Kategorien bestimmen (alle vorkommenden, sortiert nach erstem Auftreten) ──
    all_cats_ordered = []
    seen_cats = set()
    for day in all_days:
        for dish in week_data.get(day, []):
            cat = dish['kategorie']
            if cat not in seen_cats:
                all_cats_ordered.append(cat)
                seen_cats.add(cat)
    if not all_cats_ordered:
        all_cats_ordered = ['Suppe', 'Ostenviertel', 'Kumpfmuehl', 'Stadtamhof', 'Salatbar', 'Dessert']

    avail  = H - y - FOOTER_H - LEGEND_H - 4
    n_cats = len(all_cats_ordered)
    row_h  = max(avail // n_cats, 1) if n_cats else max(avail, 1)

    # ── Zeilen ─────────────────────────────────────────────────────────────────
    for ri, cat in enumerate(all_cats_ordered):
        rh = row_h if ri < n_cats - 1 else max(H - y - FOOTER_H - LEGEND_H - 4 - row_h * (n_cats - 1), 1)
        d.line([(STUB_W, y),(W, y)], fill=GRID, width=1)

        avw   = dw - 2*PAD
        f_sm  = lf(10)
        lh_sm = _line_h(d, f_sm)

        # Einheitliche Schriftgroesse fuer alle Tage in dieser Zeile
        candidate_texts = []
        for day in all_days:
            if holiday_map[day] is not None: continue
            if _is_past(day, today_date): continue
            items = [it for it in week_data.get(day, []) if it['kategorie'] == cat]
            if not items: continue
            candidate_texts.append(items[0]['name'])

        uniform_size = 17
        if candidate_texts:
            uniform_size = _find_uniform_font_size(d, candidate_texts, avw, rh - PAD * 2 - 22,
                                                    size_start=17, size_min=9)
        fn = lf(uniform_size)
        lhn = _line_h(d, fn)

        for i, day in enumerate(all_days):
            x = STUB_W + i * dw
            is_hol   = holiday_map[day] is not None
            is_past  = _is_past(day, today_date)
            is_today = _is_today(day, today_date)

            if is_past:    bg = C_PAST_BG
            elif is_hol:   bg = C_HOL_BG
            elif is_today: bg = (255, 253, 230)
            else:          bg = R_ODD if ri % 2 == 0 else R_EVEN

            d.rectangle([(x,y),(x+dw-1,y+rh-1)], fill=bg)
            d.line([(x,y),(x,y+rh)], fill=GRID, width=1)
            if is_today:
                d.line([(x,y),(x,y+rh)], fill=C_TODAY, width=TODAY_BW)
                d.line([(x+dw-1,y),(x+dw-1,y+rh)], fill=C_TODAY, width=TODAY_BW)

            if is_past:
                if ri == 0:
                    b = d.textbbox((0,0), 'vergangen', font=fprc)
                    d.text((x+(dw-(b[2]-b[0]))//2, y+rh//2-10), 'vergangen', font=fprc, fill=C_PAST_TXT)
                continue
            if is_hol:
                if ri == 0:
                    hn = holiday_map[day]
                    b = d.textbbox((0,0), 'Feiertag', font=fbdg)
                    bw = b[2]-b[0]+8; bh2 = b[3]-b[1]+5
                    bx = x+(dw-bw)//2; by = y+8
                    d.rounded_rectangle([(bx,by),(bx+bw,by+bh2)], radius=4, fill=(160,160,160))
                    d.text((bx+4, by+2), 'Feiertag', font=fbdg, fill=WHITE)
                    cy2 = by + bh2 + 5
                    fh, lhh, hlines = _fit_font(d, hn, dw-8, rh-cy2+y-4)
                    for ln in hlines:
                        b2 = d.textbbox((0,0), ln, font=fh)
                        d.text((x+(dw-(b2[2]-b2[0]))//2, cy2), ln, font=fh, fill=C_HOL_TXT)
                        cy2 += lhh
                continue

            items = [it for it in week_data.get(day, []) if it['kategorie'] == cat]
            if not items:
                b = d.textbbox((0,0), '–', font=lf(17))
                d.text((x+(dw-(b[2]-b[0]))//2, y+rh//2-11), '–', font=lf(17), fill=(180,180,180))
                continue

            it = items[0]
            cy = y + PAD

            # VG/V-Badge
            if it['vv']:
                bl = 'Vegan' if it['vv'] == 'VG' else 'Veg.'
                bc = C_VG if it['vv'] == 'VG' else C_V
                b = d.textbbox((0,0), bl, font=fbdg)
                bw2 = b[2]-b[0]+8; bh2 = b[3]-b[1]+4
                d.rounded_rectangle([(x+PAD,cy),(x+PAD+bw2,cy+bh2)], radius=3, fill=bc)
                d.text((x+PAD+4, cy+2), bl, font=fbdg, fill=WHITE)
                cy += bh2 + 3

            name_lines = wrap_text(d, it['name'], fn, avw, max_lines=20)
            for ln in name_lines:
                d.text((x+PAD, cy), ln, font=fn, fill=C_TXT)
                cy += lhn

            # Preis unten rechts
            if it['preis']:
                b = d.textbbox((0,0), it['preis'], font=fprc)
                d.text((x+dw-(b[2]-b[0])-PAD, y+rh-(b[3]-b[1])-3), it['preis'], font=fprc, fill=LIGHT)

            if is_today:
                d.line([(x,y+rh-1),(x+dw,y+rh-1)], fill=C_TODAY, width=TODAY_BW)

        # Stub-Label
        d.rectangle([(0,y),(STUB_W-1,y+rh-1)], fill=BLUE)
        d.line([(STUB_W,y),(STUB_W,y+rh)], fill=GRID, width=1)
        short_label = cat[:9] if len(cat) > 9 else cat
        _draw_stub_label(d, short_label, 0, y, STUB_W, rh)

        y += rh

    # Heute-Unterkante
    for i, day in enumerate(all_days):
        if _is_today(day, today_date):
            x = STUB_W + i * dw
            d.line([(x,y),(x+dw,y)], fill=C_TODAY, width=TODAY_BW)

    # ── Legende ────────────────────────────────────────────────────────────────
    d.line([(0,y),(W,y)], fill=GRID, width=1); y += 1
    d.rectangle([(0,y),(W,y+LEGEND_H)], fill=(245,249,253))
    fleg = lf(11)
    lx = 6
    for col, txt in [(C_VG,'Vegan'),(C_V,'Vegetarisch'),(C_HOL_HDR,'Feiertag'),
                     (C_PAST_BG,'vergangen'),(C_TODAY,'Heute')]:
        d.rectangle([(lx,y+5),(lx+12,y+15)], fill=col)
        b = d.textbbox((0,0), txt, font=fleg)
        d.text((lx+15, y+4), txt, font=fleg, fill=C_TXT)
        lx += 15 + (b[2]-b[0]) + 12

    # ── Footer ─────────────────────────────────────────────────────────────────
    footer_txt = (f'KW {kw:02d} / {label}  –  '
                  f"{local_dt.strftime('%d.%m.%Y %H:%M Uhr')}  –  "
                  f"eurest.webspeiseplan.de  |  {LOCATION_LABEL}")
    d.rectangle([(0,H-FOOTER_H),(W,H)], fill=BLUE)
    b = d.textbbox((0,0), footer_txt, font=fftr)
    d.text(((W-(b[2]-b[0]))//2, H-FOOTER_H+(FOOTER_H-(b[3]-b[1]))//2), footer_txt, font=fftr, fill=WHITE)

    return img


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    now        = datetime.now(timezone.utc)
    local      = german_time(now)
    today_date = local.date()
    target_monday = today_date - timedelta(days=today_date.weekday()) + timedelta(weeks=WEEK_OFFSET)
    label, kw = kw_label(datetime.combine(target_monday, datetime.min.time(), tzinfo=timezone.utc))
    out_path  = OUT_DIR / f'kantine_{label}_{LOCATION_NAME}.jpg'

    print(f'Kantine    : {LOCATION_LABEL} (ID: {LOCATION_ID})')
    print(f'Week label : {label}  (KW {kw:02d})')
    print(f'Today      : {today_date}')
    print(f'WEEK_OFFSET: {WEEK_OFFSET}  ->  Woche ab {target_monday}')

    holiday_map = week_holiday_map(target_monday)
    hol_days    = [k for k, v in holiday_map.items() if v]
    all_week_dates = [
        datetime.combine(target_monday + timedelta(i), datetime.min.time(), tzinfo=timezone.utc)
        for i in range(5)
    ]
    if WEEK_OFFSET == 0:
        scrape_dates = [dt for dt in all_week_dates
                        if dt.date() >= today_date and day_key(dt) not in hol_days]
    else:
        scrape_dates = [dt for dt in all_week_dates if day_key(dt) not in hol_days]

    print(f'Feiertage  : {[(k, holiday_map[k]) for k in hol_days] or "keine"}')
    print(f'Scraping   : {[day_key(dt) for dt in scrape_dates]}')

    week_data = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1400, "height": 900},
            extra_http_headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.1"},
        )
        setup_eurest(page)

        for date_obj in scrape_dates:
            dk    = day_key(date_obj)
            dishes = scrape_day(page, date_obj)
            if dishes:
                week_data[dk] = dishes

        browser.close()

    days_filled = len(week_data)
    days_avail  = len(scrape_dates)
    print(f'\nErgebnis   : {list(week_data.keys())}  ({days_filled}/{days_avail})')

    if not week_data:
        print("Keine Speisedaten gefunden – kein Bild wird erzeugt. Pruefe Scraping-Log oben.")
        sys.exit(0)

    img = render(week_data, kw, label, local, holiday_map, today_date, target_monday)
    img.save(str(out_path), 'JPEG', quality=92)
    print(f'Saved: {out_path}  ({img.size[0]}x{img.size[1]})')

    # latest.jpg aktualisieren
    latest_path = OUT_DIR / f'latest_{LOCATION_NAME}.jpg'
    import shutil
    shutil.copy(str(out_path), str(latest_path))
    print(f'latest_{LOCATION_NAME}.jpg aktualisiert')

    # Alte Bilder aufraeumen
    pattern = f'kantine_*_{LOCATION_NAME}.jpg'
    for old in sorted(OUT_DIR.glob(pattern))[:-MAX_KEEP]:
        old.unlink()
        print(f'Removed: {old}')


if __name__ == '__main__':
    main()
