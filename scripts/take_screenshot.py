#!/usr/bin/env python3
"""Eurest Kantine Regensburg – Schaeffler & Aumovio – 800x600 JPEG.

Umgebungsvariablen:
  EUREST_LOCATION_ID    8949 = Schaeffler, 8950 = Aumovio (default: 8949)
  EUREST_LOCATION_NAME  schaeffler / aumovio (default: schaeffler)
  WEEK_OFFSET           0 = aktuelle Woche, 1 = naechste Woche (default: 0, nur fuer week-Modus)
  DISPLAY_MODE          day = Tagesansicht/Matrix (default), week = Wochenansicht
  DISPLAY_DAY           Ziel-Tag manuell: monday|tuesday|wednesday|thursday|friday
                        (leer = Automatik: vor 13:30 heute, ab 13:30 naechster Werktag)

Wichtige Verhaltensaenderungen:
- day-Mode rendert nur genau einen Tag als 2-spaltige Kachelmatrix.
- Vor der Tagesauswahl wird explizit versucht, die richtige KW anzuklicken.
- Wenn der Zieltag im day-Mode nicht wirklich im menuDaySelect vorhanden ist
  oder keine Gerichte extrahiert werden koennen, bricht das Script mit Exit 1 ab.
  Dadurch wird latest_<location>.jpg NICHT mit einem falschen/leeren Bild ueberschrieben.
- Pro Location wird current_<location>.json geschrieben, damit RSS/Workflow
  nicht mehr per Dateinamen-Sortierung das falsche Bild waehlen.
"""

import os
import sys
import json
import math
import shutil
from pathlib import Path
from datetime import date, datetime, timezone, timedelta

from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont

# ── Config ─────────────────────────────────────────────────────────────────────
EUREST_URL = "https://eurest.webspeiseplan.de/39799C127F748D639984F4CDBEB44846"
LOCATION_ID = os.environ.get("EUREST_LOCATION_ID", "8949").strip()
LOCATION_NAME = os.environ.get("EUREST_LOCATION_NAME", "schaeffler").strip().lower()
WEEK_OFFSET = int(os.environ.get("WEEK_OFFSET", "0"))
DISPLAY_MODE = os.environ.get("DISPLAY_MODE", "day").strip().lower()   # day | week
DISPLAY_DAY = os.environ.get("DISPLAY_DAY", "").strip().lower()        # monday..friday | ""

SWITCH_HOUR_LOCAL = 13
SWITCH_MINUTE_LOCAL = 30

LOCATION_LABELS = {
    "8949": "SCHAEFFLER Regensburg",
    "8950": "AUMOVIO Regensburg",
}
LOCATION_LABEL = LOCATION_LABELS.get(LOCATION_ID, f"Kantine {LOCATION_ID}")

CATEGORY_FALLBACK = {
    "8949": ["Suppe", "Ostenviertel", "Kumpfmühl", "Stadtamhof", "Reinhausen", "Salatbar", "Dessert"],
    "8950": ["Suppe", "Ostenviertel", "Weichs", "Brandlberg", "Niederwinzer", "Oberwinzer", "Salatbar", "Dessert"],
}

DAY_NAME_MAP = {
    "monday": 0, "montag": 0, "mo": 0,
    "tuesday": 1, "dienstag": 1, "di": 1,
    "wednesday": 2, "mittwoch": 2, "mi": 2,
    "thursday": 3, "donnerstag": 3, "do": 3,
    "friday": 4, "freitag": 4, "fr": 4,
}

OUT_DIR = Path("docs/images")
OUT_DIR.mkdir(parents=True, exist_ok=True)
MAX_KEEP = 14
W, H = 800, 600
FOOTER_H = 20

# ── Colours ────────────────────────────────────────────────────────────────────
BLUE = (0, 57, 107)
BLUE_LIGHT = (0, 80, 140)
LIGHT = (0, 119, 193)
R_ODD = (240, 246, 252)
R_EVEN = (255, 255, 255)
C_VG = (34, 139, 34)
C_V = (100, 180, 60)
WHITE = (255, 255, 255)
GRID = (190, 210, 230)
C_HOL_BG = (220, 220, 220)
C_HOL_HDR = (140, 140, 140)
C_HOL_TXT = (100, 100, 100)
C_CAT_TXT = (180, 210, 240)
C_TODAY = (255, 200, 0)

_FREG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_FBOL = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def lf(size, bold=False):
    for p in (_FBOL if bold else _FREG) + _FREG:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    return ImageFont.load_default()


# ── Bayerische Feiertage ───────────────────────────────────────────────────────
def _easter(y):
    a = y % 19
    b = y // 100
    c = y % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31
    dy = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, mo, dy)


def bavaria_holidays(y):
    e = _easter(y)
    return {
        date(y, 1, 1): "Neujahr",
        date(y, 1, 6): "Hl. Drei Koenige",
        e - timedelta(2): "Karfreitag",
        e: "Ostersonntag",
        e + timedelta(1): "Ostermontag",
        date(y, 5, 1): "Tag der Arbeit",
        e + timedelta(39): "Christi Himmelfahrt",
        e + timedelta(49): "Pfingstsonntag",
        e + timedelta(50): "Pfingstmontag",
        e + timedelta(60): "Fronleichnam",
        date(y, 8, 15): "Mariae Himmelfahrt",
        date(y, 10, 3): "Tag der Deutschen Einheit",
        date(y, 11, 1): "Allerheiligen",
        date(y, 12, 25): "1. Weihnachtstag",
        date(y, 12, 26): "2. Weihnachtstag",
    }


def week_holiday_map(monday_date):
    y = monday_date.year
    hols = bavaria_holidays(y)
    if (monday_date + timedelta(4)).year != y:
        hols.update(bavaria_holidays(y + 1))
    short = ["Mo", "Di", "Mi", "Do", "Fr"]
    return {
        f"{short[i]} {(monday_date + timedelta(i)).strftime('%d.%m')}": hols.get(monday_date + timedelta(i))
        for i in range(5)
    }


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
        return d2 - timedelta(days=(d2.weekday() + 1) % 7)

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
        dm = day_key_str.split(" ")[1].split(".")
        return date(today_date.year, int(dm[1]), int(dm[0])) < today_date
    except Exception:
        return False


def _is_today(day_key_str, today_date):
    try:
        dm = day_key_str.split(" ")[1].split(".")
        return date(today_date.year, int(dm[1]), int(dm[0])) == today_date
    except Exception:
        return False


# ── Ziel-Tag-Berechnung ────────────────────────────────────────────────────────
def resolve_target_date(local_dt, holidays_all):
    today = local_dt.date()

    if DISPLAY_DAY and DISPLAY_DAY in DAY_NAME_MAP:
        target_weekday = DAY_NAME_MAP[DISPLAY_DAY]
        days_ahead = (target_weekday - today.weekday()) % 7
        candidate = today + timedelta(days=days_ahead)

        for _ in range(14):
            if candidate.weekday() < 5 and candidate not in holidays_all:
                break
            candidate += timedelta(days=1)

        print(f"[target] DISPLAY_DAY={DISPLAY_DAY!r} -> {candidate}")
        return candidate

    after_switch = (
        local_dt.hour > SWITCH_HOUR_LOCAL
        or (local_dt.hour == SWITCH_HOUR_LOCAL and local_dt.minute >= SWITCH_MINUTE_LOCAL)
    )
    candidate = today + timedelta(days=1) if after_switch else today

    for _ in range(14):
        if candidate.weekday() < 5 and candidate not in holidays_all:
            break
        candidate += timedelta(days=1)

    print(f"[target] after_switch={after_switch}, today={today} -> Ziel: {candidate}")
    return candidate


# ── Diagnostik-JS ──────────────────────────────────────────────────────────────
JS_DEBUG_DOM = r"""
(function(){
  var out = [];
  var sel = document.querySelector('select.menuDaySelect');
  if (sel) {
    var opts = Array.from(sel.options).map(function(o){ return o.value + '=' + o.text.trim(); });
    out.push('menuDaySelect options: ' + opts.join(', '));
    out.push('menuDaySelect current: ' + sel.value);
  } else {
    out.push('NO select.menuDaySelect');
  }

  var mlw = document.querySelector('.menuListWrapper');
  out.push('menuListWrapper: ' + (mlw ? 'FOUND' : 'NOT FOUND'));

  var mws = document.querySelectorAll('.meal-wrapper');
  out.push('meal-wrapper count: ' + mws.length);

  var cns = document.querySelectorAll('.categoryName');
  out.push('categoryName: ' + Array.from(cns).map(function(e){ return e.textContent.trim(); }).join(', '));

  var btns = Array.from(document.querySelectorAll('button')).map(function(b){
    return (b.textContent.trim().substring(0,30) || b.className.substring(0,30));
  });
  out.push('buttons: ' + btns.join(' | '));

  var selects = Array.from(document.querySelectorAll('select')).map(function(s){
    return s.className + '[' + s.options.length + ']';
  });
  out.push('selects: ' + selects.join(', '));

  out.push('body snippet: ' + document.body.innerHTML.substring(0, 300).replace(/\s+/g,' '));
  return out.join(' || ');
})()
"""

JS_EXTRACT = r"""
(function(){
  var result = [];
  var mlw = document.querySelector('.menuListWrapper');

  if (mlw) {
    var mealEls = Array.from(mlw.querySelectorAll('.meal-wrapper'));
    for (var mel of mealEls) {
      var catEl = mel.querySelector('.categoryName');
      var cat = catEl ? catEl.textContent.trim() : '';

      var nameNode = mel.querySelector('.mealNameWrapper');
      var mealName = nameNode ? nameNode.textContent.trim().replace(/\u00a0/g,' ') : '';

      var priceRows = Array.from(mel.querySelectorAll('.price-row'));
      var priceRow = priceRows.find(function(r){
        var lbl = r.querySelector('.price-label');
        return lbl && lbl.textContent.trim().toLowerCase() === 'preis';
      });
      var priceNode = priceRow ? priceRow.querySelector('.price-value') : mel.querySelector('.price-value');
      var price = priceNode ? priceNode.textContent.trim().replace(/\u00a0/g,' ') : '';

      var featureImgs = Array.from(mel.querySelectorAll('.image-feature'));
      var features = featureImgs.map(function(img){ return (img.alt || img.src || '').toLowerCase(); });

      if (mealName) {
        result.push({category: cat, name: mealName, price: price, features: features});
      }
    }
    if (result.length > 0) return JSON.stringify(result);
  }

  var catWrappers = Array.from(document.querySelectorAll('.category-wrapper'));
  for (var cw of catWrappers) {
    var nameEl = cw.querySelector('.category-name-container');
    var catName = nameEl ? nameEl.textContent.trim() : '';
    if (!catName) continue;

    var mealEls2 = Array.from(cw.querySelectorAll('.meal-wrapper'));
    for (var mel2 of mealEls2) {
      var nameNode2 = mel2.querySelector('.mealNameWrapper');
      var mealName2 = nameNode2 ? nameNode2.textContent.trim().replace(/\u00a0/g,' ') : '';

      var priceRows2 = Array.from(mel2.querySelectorAll('.price-row'));
      var priceRow2 = priceRows2.find(function(r){
        var lbl = r.querySelector('.price-label');
        return lbl && lbl.textContent.trim().toLowerCase() === 'preis';
      });
      var priceNode2 = priceRow2 ? priceRow2.querySelector('.price-value') : mel2.querySelector('.price-value');
      var price2 = priceNode2 ? priceNode2.textContent.trim().replace(/\u00a0/g,' ') : '';

      var featureImgs2 = Array.from(mel2.querySelectorAll('.image-feature'));
      var features2 = featureImgs2.map(function(img){ return (img.alt || img.src || '').toLowerCase(); });

      if (mealName2) {
        result.push({category: catName, name: mealName2, price: price2, features: features2});
      }
    }
  }

  return JSON.stringify(result);
})()
"""

JS_MENU_SNAPSHOT = r"""
(function(){
  var sel = document.querySelector('select.menuDaySelect');
  var mlw = document.querySelector('.menuListWrapper');

  var mealEls = Array.from(document.querySelectorAll('.meal-wrapper'));
  var meals = mealEls.map(function(mel){
    var catEl = mel.querySelector('.categoryName');
    var cat = catEl ? catEl.textContent.trim() : '';

    var nameNode = mel.querySelector('.mealNameWrapper');
    var mealName = nameNode ? nameNode.textContent.trim().replace(/\u00a0/g,' ') : '';

    var priceRows = Array.from(mel.querySelectorAll('.price-row'));
    var priceRow = priceRows.find(function(r){
      var lbl = r.querySelector('.price-label');
      return lbl && lbl.textContent.trim().toLowerCase() === 'preis';
    });
    var priceNode = priceRow ? priceRow.querySelector('.price-value') : mel.querySelector('.price-value');
    var price = priceNode ? priceNode.textContent.trim().replace(/\u00a0/g,' ') : '';

    return {category: cat, name: mealName, price: price};
  }).filter(function(x){ return x.name; });

  var signature = meals.map(function(x){
    return [x.category, x.name, x.price].join('|');
  }).join(' || ');

  return JSON.stringify({
    selectValue: sel ? sel.value : '',
    mealCount: mealEls.length,
    textLength: mlw ? (mlw.innerText || '').trim().length : 0,
    signature: signature,
    firstMeals: meals.slice(0, 3)
  });
})()
"""

JS_KW_CANDIDATES = r"""
(function(){
  var rx = /^KW\s+\d{1,2}$/i;
  var out = [];
  var seen = new Set();
  var els = Array.from(document.querySelectorAll('button, [role="button"], a, div, span'));
  for (var el of els) {
    if (!el || !el.offsetParent) continue;
    var txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (rx.test(txt) && !seen.has(txt)) {
      seen.add(txt);
      out.push(txt);
    }
  }
  return JSON.stringify(out);
})()
"""


# ── Scraper ────────────────────────────────────────────────────────────────────
def _dump_debug(page, label):
    try:
        dbg = page.evaluate(JS_DEBUG_DOM)
        print(f"[debug:{label}] {dbg}")
    except Exception as e:
        print(f"[debug:{label}] Fehler: {e}")


def _click_weiter(page):
    try:
        btn_info = page.evaluate("""
        Array.from(document.querySelectorAll('button')).map(function(b){
          return JSON.stringify({
            text:b.textContent.trim().substring(0,40),
            cls:b.className.substring(0,60),
            disabled:b.disabled,
            visible:b.offsetParent!==null
          });
        }).join('\\n')
        """)
        print(f"[weiter] Buttons:\n{btn_info}")
    except Exception as e:
        print(f"[weiter] Dump-Fehler: {e}")

    clicked = page.evaluate("""
    (function(){
      var btns = Array.from(document.querySelectorAll('button'));
      var kws = ['weiter','next','submit','ok','bestätigen','bestatigen','los'];
      for (var b of btns) {
        var t = b.textContent.trim().toLowerCase();
        for (var kw of kws) {
          if (t.indexOf(kw)!==-1) {
            b.click();
            return 'clicked:' + b.textContent.trim();
          }
        }
      }
      return 'no-match';
    })()
    """)
    print(f"[weiter] {clicked!r}")

    if "clicked" in clicked:
        return True

    for sel in [
        "button.submit",
        "button[type='submit']",
        "button:has-text('Weiter')",
        "button:has-text('weiter')",
        "button.uppercase",
        "button.btn-primary",
        "button.min-w-8rem",
        ".submit-btn",
        "input[type='submit']",
    ]:
        try:
            page.click(sel, timeout=2000)
            print(f"[weiter] via {sel!r}")
            return True
        except Exception:
            pass

    print("[weiter] WARNUNG: nicht gefunden")
    return False


def setup_eurest(page):
    print(f"[setup] {EUREST_URL}")
    page.goto(EUREST_URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2000)
    _dump_debug(page, "goto")

    try:
        page.wait_for_selector("select.locationSelection", timeout=8000)
        page.select_option("select.locationSelection", value=LOCATION_ID, timeout=8000)
        page.wait_for_timeout(1000)
        print("[setup] location OK")
    except Exception as e:
        print(f"[setup] location Fehler: {e}")

    _dump_debug(page, "location")

    try:
        page.wait_for_selector("input#featureFilterSelective", timeout=5000)
        cb = page.query_selector("input#featureFilterSelective")
        if cb and not cb.is_checked():
            cb.click()
        page.wait_for_timeout(400)

        for sel in [
            "button.min-w-8rem",
            "button:has-text('ok')",
            "button:has-text('OK')",
            "button:has-text('Akzeptieren')",
            "button:has-text('Zustimmen')",
        ]:
            try:
                page.click(sel, timeout=2000)
                break
            except Exception:
                pass

        page.wait_for_timeout(1000)
    except Exception:
        print("[setup] Privacy-Modal nicht erschienen")

    _dump_debug(page, "privacy")

    try:
        page.wait_for_selector("select.languageSelection", timeout=5000)
        page.select_option("select.languageSelection", value="1", timeout=5000)
        page.wait_for_timeout(500)
    except Exception as e:
        print(f"[setup] lang Fehler: {e}")

    _dump_debug(page, "sprache")
    _click_weiter(page)
    page.wait_for_timeout(3000)
    _dump_debug(page, "weiter-1")

    sel_found = False
    try:
        page.wait_for_selector("select.menuDaySelect", timeout=5000)
        sel_found = True
    except Exception:
        pass

    if not sel_found:
        _click_weiter(page)
        page.wait_for_timeout(3000)
        _dump_debug(page, "weiter-2")

    try:
        page.wait_for_selector(".ReactModal__Content", timeout=4000)
        for sel in ["button:has-text('nein')", "button:has-text('Nein')", "button:has-text('no')"]:
            try:
                page.click(sel, timeout=2000)
                print("[setup] Filter-Modal mit 'Nein' geschlossen")
                break
            except Exception:
                pass
        page.wait_for_timeout(1000)
    except Exception:
        print("[setup] kein Filter-Modal")

    loaded = False
    try:
        page.wait_for_selector("select.menuDaySelect", timeout=15000)
        loaded = True
    except Exception:
        print("[setup] menuDaySelect nicht gefunden")

    if not loaded:
        for sel in [".menuListWrapper", ".meal-wrapper", ".mealNameWrapper"]:
            try:
                page.wait_for_selector(sel, timeout=8000)
                loaded = True
                break
            except Exception:
                pass

    _dump_debug(page, "final")
    print("[setup] Abgeschlossen")


def _get_available_dates(page):
    js = r"""(function(){
      var sel=document.querySelector('select.menuDaySelect');
      if(!sel)return'[]';
      return JSON.stringify(Array.from(sel.options).map(function(o){return o.value;}));
    })()"""
    try:
        vals = json.loads(page.evaluate(js))
        print(f"[dates] {vals}")
        return vals
    except Exception as e:
        print(f"[dates] Fehler: {e}")
        return []


def _get_menu_snapshot(page):
    try:
        snap = json.loads(page.evaluate(JS_MENU_SNAPSHOT))
        if not isinstance(snap, dict):
            raise ValueError("Snapshot ist kein Dict")
        return snap
    except Exception as e:
        print(f"[snapshot] Fehler: {e}")
        return {
            "selectValue": "",
            "mealCount": 0,
            "textLength": 0,
            "signature": "",
            "firstMeals": [],
        }


def _get_kw_candidates(page):
    try:
        vals = json.loads(page.evaluate(JS_KW_CANDIDATES))
        print(f"[kw] sichtbar: {vals}")
        return vals if isinstance(vals, list) else []
    except Exception as e:
        print(f"[kw] Kandidaten-Fehler: {e}")
        return []


def _wait_for_any_stable_menu(page, before_snapshot, max_wait_ms=8000, interval_ms=500):
    before_sig = before_snapshot.get("signature", "")
    last_sig = None
    stable = 0
    last_snapshot = before_snapshot
    polls = max_wait_ms // interval_ms

    for idx in range(polls):
        page.wait_for_timeout(interval_ms)
        snap = _get_menu_snapshot(page)
        last_snapshot = snap
        current_sig = snap.get("signature", "")
        meal_count = snap.get("mealCount", 0)
        current_select = snap.get("selectValue", "")

        print(
            f"  [kw-stable] poll={idx+1}/{polls} "
            f"select={current_select!r} meals={meal_count} siglen={len(current_sig)} "
            f"first={snap.get('firstMeals', [])}"
        )

        if meal_count <= 0 or not current_sig:
            stable = 0
            last_sig = current_sig
            continue

        if current_sig == last_sig:
            stable += 1
        else:
            stable = 0

        last_sig = current_sig

        if stable >= 2 and (current_sig != before_sig or meal_count > 0):
            print("  [kw-stable] OK - Inhalte sind stabil")
            return snap

    print("  [kw-stable] WARNUNG - Timeout, nutze letzten Snapshot")
    return last_snapshot


def _click_kw(page, target_kw_text):
    # 1) role=button exact
    try:
        page.get_by_role("button", name=target_kw_text).click(timeout=2000)
        print(f"[kw] click via role/button: {target_kw_text}")
        return True
    except Exception:
        pass

    # 2) text exact
    try:
        page.get_by_text(target_kw_text, exact=True).click(timeout=2000)
        print(f"[kw] click via text exact: {target_kw_text}")
        return True
    except Exception:
        pass

    # 3) JS-Klick auf passendes sichtbares Element oder naechsten klickbaren Parent
    try:
        clicked = page.evaluate(
            """
            (targetText) => {
              var nodes = Array.from(document.querySelectorAll('button, [role="button"], a, div, span'));
              function isVisible(el){ return !!(el && el.offsetParent); }
              function clickEl(el){
                try { el.click(); return true; } catch(e) {}
                return false;
              }
              for (var el of nodes) {
                if (!isVisible(el)) continue;
                var txt = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                if (txt === targetText) {
                  if (clickEl(el)) return 'clicked-self';
                  var p = el.parentElement;
                  var hops = 0;
                  while (p && hops < 5) {
                    if (isVisible(p) && clickEl(p)) return 'clicked-parent:' + p.tagName;
                    p = p.parentElement;
                    hops++;
                  }
                }
              }
              return 'no-match';
            }
            """,
            target_kw_text
        )
        print(f"[kw] JS-Klick Ergebnis: {clicked!r}")
        return "clicked" in str(clicked)
    except Exception as e:
        print(f"[kw] JS-Klick Fehler: {e}")
        return False


def ensure_target_week(page, expected_date):
    iso_year, iso_week, _ = expected_date.isocalendar()
    target_kw_text = f"KW {iso_week:02d}"
    print(f"[kw] Zielwoche fuer {expected_date}: {target_kw_text} (ISO {iso_year})")

    before_snapshot = _get_menu_snapshot(page)
    before_dates = _get_available_dates(page)
    _get_kw_candidates(page)

    # Falls Ziel-Datum bereits vorhanden ist, keine Umschaltung noetig.
    target_str = expected_date.strftime("%Y-%m-%d")
    if any(v.startswith(target_str) for v in before_dates):
        print(f"[kw] Ziel-Datum {target_str} bereits in Optionen vorhanden")
        return before_dates

    clicked = _click_kw(page, target_kw_text)
    if clicked:
        _wait_for_any_stable_menu(page, before_snapshot, max_wait_ms=8000, interval_ms=500)
        page.wait_for_timeout(1200)
        _dump_debug(page, f"kw-{target_kw_text}")
    else:
        print(f"[kw] WARNUNG: {target_kw_text} konnte nicht aktiv geklickt werden")

    after_dates = _get_available_dates(page)
    print(f"[kw] Optionen nach KW-Pruefung: {after_dates}")
    return after_dates


def click_day(page, date_obj, available_values):
    target = date_obj.strftime("%Y-%m-%d")
    matched = next((v for v in available_values if v.startswith(target)), None)

    if not matched:
        print(f"  [day] kein Wert fuer {target}")
        return None

    try:
        page.select_option("select.menuDaySelect", value=matched, timeout=5000)
        print(f"  [day] gewaehlt: {matched}")
        return matched
    except Exception as e:
        print(f"  [day] Fehler: {e}")
        return None


def _wait_for_day_content(page, matched_value, before_snapshot, max_wait_ms=10000, interval_ms=500):
    before_select = before_snapshot.get("selectValue", "")
    before_sig = before_snapshot.get("signature", "")
    same_day_requested = (before_select == matched_value)

    last_sig = None
    stable = 0
    last_snapshot = before_snapshot

    polls = max_wait_ms // interval_ms
    for idx in range(polls):
        page.wait_for_timeout(interval_ms)
        snap = _get_menu_snapshot(page)
        last_snapshot = snap

        current_select = snap.get("selectValue", "")
        current_sig = snap.get("signature", "")
        meal_count = snap.get("mealCount", 0)
        first_meals = snap.get("firstMeals", [])

        print(
            f"  [stable] poll={idx+1}/{polls} "
            f"select={current_select!r} meals={meal_count} siglen={len(current_sig)} "
            f"first={first_meals}"
        )

        if current_select != matched_value:
            stable = 0
            last_sig = current_sig
            continue

        if meal_count <= 0 or not current_sig:
            stable = 0
            last_sig = current_sig
            continue

        if not same_day_requested and before_sig and current_sig == before_sig:
            print("  [stable] Inhalt noch unveraendert wie vor dem Tageswechsel -> warte weiter")
            stable = 0
            last_sig = current_sig
            continue

        if current_sig == last_sig:
            stable += 1
        else:
            stable = 0

        last_sig = current_sig

        if stable >= 2:
            print("  [stable] OK - Select und Inhalte sind stabil")
            return snap

    print("  [stable] WARNUNG - Timeout, nutze letzten Snapshot")
    return last_snapshot


def _detect_vv(name, features):
    low = name.lower()
    feat_str = " ".join(features)
    if "vegan" in low or "vegan" in feat_str:
        return "VG"
    if any(w in low for w in ["vegetarisch", "vegetarische", "vegetarischer"]):
        return "V"
    if "vegetarisch" in feat_str:
        return "V"
    return ""


def parse_eurest_dom(raw_json):
    try:
        data = json.loads(raw_json)
    except Exception as e:
        print(f"  [parse] JSON-Fehler: {e}")
        return []

    dishes = []
    for entry in data:
        cat = entry.get("category", "").strip()
        name = entry.get("name", "").strip().replace("\n", " ")
        price = entry.get("price", "").strip()
        features = entry.get("features", [])
        vv = _detect_vv(name, features)
        dishes.append({
            "kategorie": cat,
            "name": name,
            "preis": price,
            "vv": vv,
        })
        print(f"  [parse] {cat:20s} | {vv!r:3s} | {name[:60]!r} | {price!r}")

    return dishes


def scrape_day(page, date_obj, available_values):
    date_label = date_obj.strftime("%d.%m.")
    print(f"\n[scrape] {date_label}")

    before_snapshot = _get_menu_snapshot(page)
    print(f"  [before] select={before_snapshot.get('selectValue')!r} first={before_snapshot.get('firstMeals')}")

    matched = click_day(page, date_obj, available_values)
    if not matched:
        return []

    after_snapshot = _wait_for_day_content(page, matched, before_snapshot)

    for sel in [".menuListWrapper", ".meal-wrapper", ".mealNameWrapper"]:
        try:
            page.wait_for_selector(sel, timeout=8000)
            break
        except Exception:
            pass

    _dump_debug(page, f"scrape-{date_label}")

    current_select = _get_menu_snapshot(page).get("selectValue", "")
    print(f"  [verify] menuDaySelect current nach wait: {current_select!r}")

    for attempt in range(1, 3):
        try:
            raw_json = page.evaluate(JS_EXTRACT)
            if raw_json and raw_json not in ("[]", "null", ""):
                dishes = parse_eurest_dom(raw_json)
                print(f"  [dom] {len(dishes)} Gerichte")
                print(f"  [dom] erste Gerichte: {[d['name'] for d in dishes[:3]]}")
                return dishes

            print(f"  [dom] Leer (Versuch {attempt}/2)")
        except Exception as e:
            print(f"  [dom] Fehler (Versuch {attempt}/2): {e}")

        page.wait_for_timeout(1000)
        after_snapshot = _wait_for_day_content(page, matched, after_snapshot, max_wait_ms=3000, interval_ms=500)

    return []


# ── Render-Helfer ──────────────────────────────────────────────────────────────
def _split_chars(draw, word, font, max_w):
    parts = []
    while word:
        lo, hi = 1, len(word)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            b = draw.textbbox((0, 0), word[:mid] + ("-" if mid < len(word) else ""), font=font)
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


def wrap_text(draw, text, f, max_w, max_lines=20):
    tokens = []
    for word in text.split():
        b = draw.textbbox((0, 0), word, font=f)
        if b[2] - b[0] > max_w:
            tokens.extend(_split_long_word(draw, word, f, max_w))
        else:
            tokens.append(word)

    out, cur = [], []
    for tok in tokens:
        t = " ".join(cur + [tok])
        b = draw.textbbox((0, 0), t, font=f)
        if b[2] - b[0] <= max_w:
            cur.append(tok)
        else:
            if cur:
                out.append(" ".join(cur))
            cur = [tok]

    if cur:
        out.append(" ".join(cur))

    return out[:max_lines]


def _line_h(draw, font):
    b = draw.textbbox((0, 0), "Ag", font=font)
    return b[3] - b[1] + 4


def _fit_font(draw, text, max_w, max_h, size_start=19, size_min=10, bold=False):
    for size in range(size_start, size_min - 1, -1):
        f = lf(size, bold)
        lh = _line_h(draw, f)
        lines = wrap_text(draw, text, f, max_w, max_lines=20)
        if not lines:
            return f, lh, lines
        if lh * len(lines) <= max_h:
            return f, lh, lines

    f = lf(size_min, bold)
    return f, _line_h(draw, f), wrap_text(draw, text, f, max_w, max_lines=20)


def _find_uniform_font_size(draw, texts, max_w, max_h, size_start=19, size_min=10, bold=False):
    for size in range(size_start, size_min - 1, -1):
        f = lf(size, bold)
        lh = _line_h(draw, f)
        if all(lh * len(wrap_text(draw, t, f, max_w, max_lines=20)) <= max_h for t in texts if t):
            return size
    return size_min


# ── Render Tagesansicht: 2×N Matrix ───────────────────────────────────────────
def render_day(dishes, target_date, kw, label, local_dt, is_holiday=None):
    img = Image.new("RGB", (W, H), (245, 248, 252))
    d = ImageDraw.Draw(img)

    ftit = lf(20, True)
    fdate = lf(13)
    fftr = lf(10)
    fbdg = lf(11, True)
    fprc = lf(12, True)
    fcat = lf(10, True)

    HDR_H = 58
    LEGEND_H = 22
    GAP = 4
    PAD = 7
    NCOLS = 2

    d.rectangle([(0, 0), (W, HDR_H)], fill=BLUE)
    weekday_names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    day_str = f"{weekday_names[target_date.weekday()]}, {target_date.strftime('%d.%m.%Y')}"
    title_str = f"{LOCATION_LABEL}  |  KW {kw:02d}"

    bt = d.textbbox((0, 0), title_str, font=ftit)
    bd = d.textbbox((0, 0), day_str, font=fdate)
    total_h = (bt[3] - bt[1]) + 3 + (bd[3] - bd[1])
    ty = (HDR_H - total_h) // 2

    d.text(((W - (bt[2] - bt[0])) // 2, ty), title_str, font=ftit, fill=WHITE)
    d.text(((W - (bd[2] - bd[0])) // 2, ty + (bt[3] - bt[1]) + 3), day_str, font=fdate, fill=(180, 210, 240))

    content_top = HDR_H + GAP
    content_bot = H - FOOTER_H - LEGEND_H - 4
    content_h = content_bot - content_top

    if is_holiday:
        d.rectangle([(0, content_top), (W, content_bot)], fill=C_HOL_BG)
        msg = f"Feiertag: {is_holiday}"
        fm = lf(28, True)
        bm = d.textbbox((0, 0), msg, font=fm)
        d.text(
            ((W - (bm[2] - bm[0])) // 2, content_top + (content_h - (bm[3] - bm[1])) // 2),
            msg,
            font=fm,
            fill=C_HOL_TXT,
        )
    elif not dishes:
        d.rectangle([(0, content_top), (W, content_bot)], fill=(248, 248, 248))
        msg = "Keine Speisedaten verfügbar"
        fm = lf(22, True)
        bm = d.textbbox((0, 0), msg, font=fm)
        d.text(
            ((W - (bm[2] - bm[0])) // 2, content_top + (content_h - (bm[3] - bm[1])) // 2),
            msg,
            font=fm,
            fill=(160, 160, 160),
        )
    else:
        cats_ordered = []
        seen = set()
        for dish in dishes:
            cat = dish["kategorie"]
            if cat not in seen:
                cats_ordered.append(cat)
                seen.add(cat)

        n = len(cats_ordered)
        nrows = math.ceil(n / NCOLS)

        cell_w = (W - GAP * (NCOLS + 1)) // NCOLS
        cell_h = (content_h - GAP * (nrows + 1)) // nrows

        name_max_w = cell_w - 2 * PAD
        cat_label_h = _line_h(d, fcat) + 2
        badge_h_est = _line_h(d, fbdg) + 6
        price_h_est = _line_h(d, fprc) + 2
        name_max_h = cell_h - cat_label_h - badge_h_est - price_h_est - 3 * PAD

        all_names = [it["name"] for it in dishes]
        uni_size = _find_uniform_font_size(
            d,
            all_names,
            name_max_w,
            name_max_h,
            size_start=18,
            size_min=9
        )
        fn = lf(uni_size)
        lhn = _line_h(d, fn)

        for idx, cat in enumerate(cats_ordered):
            col = idx % NCOLS
            row = idx // NCOLS

            cx0 = GAP + col * (cell_w + GAP)
            cy0 = content_top + GAP + row * (cell_h + GAP)
            cx1 = cx0 + cell_w
            cy1 = cy0 + cell_h

            bg = R_ODD if idx % 2 == 0 else R_EVEN
            d.rounded_rectangle([(cx0, cy0), (cx1, cy1)], radius=6, fill=bg)

            d.rounded_rectangle([(cx0, cy0), (cx1, cy0 + cat_label_h + 2)], radius=6, fill=BLUE)
            d.rectangle([(cx0, cy0 + cat_label_h - 2), (cx1, cy0 + cat_label_h + 2)], fill=BLUE)
            d.text((cx0 + PAD, cy0 + 2), cat, font=fcat, fill=C_CAT_TXT)

            it = next((x for x in dishes if x["kategorie"] == cat), None)
            if not it:
                continue

            cy = cy0 + cat_label_h + PAD

            if it["vv"]:
                bl = "Vegan" if it["vv"] == "VG" else "Veg."
                bc_col = C_VG if it["vv"] == "VG" else C_V
                bb = d.textbbox((0, 0), bl, font=fbdg)
                bw2 = bb[2] - bb[0] + 6
                bh2 = bb[3] - bb[1] + 3
                d.rounded_rectangle([(cx0 + PAD, cy), (cx0 + PAD + bw2, cy + bh2)], radius=3, fill=bc_col)
                d.text((cx0 + PAD + 3, cy + 1), bl, font=fbdg, fill=WHITE)
                cy += bh2 + 3

            name_lines = wrap_text(d, it["name"], fn, name_max_w, max_lines=20)
            for ln in name_lines:
                if cy + lhn > cy1 - price_h_est - PAD:
                    break
                d.text((cx0 + PAD, cy), ln, font=fn, fill=(30, 30, 30))
                cy += lhn

            if it["preis"]:
                pb = d.textbbox((0, 0), it["preis"], font=fprc)
                d.text(
                    (cx1 - (pb[2] - pb[0]) - PAD, cy1 - (pb[3] - pb[1]) - PAD),
                    it["preis"],
                    font=fprc,
                    fill=LIGHT,
                )

    leg_y = H - FOOTER_H - LEGEND_H - 2
    d.line([(0, leg_y), (W, leg_y)], fill=GRID, width=1)
    leg_y += 1
    d.rectangle([(0, leg_y), (W, leg_y + LEGEND_H)], fill=(245, 249, 253))

    fleg = lf(11)
    lx = 6
    for col, txt in [(C_VG, "Vegan"), (C_V, "Vegetarisch"), (C_HOL_HDR, "Feiertag"), (C_TODAY, "Heute")]:
        d.rectangle([(lx, leg_y + 5), (lx + 12, leg_y + 15)], fill=col)
        b = d.textbbox((0, 0), txt, font=fleg)
        d.text((lx + 15, leg_y + 4), txt, font=fleg, fill=(30, 30, 30))
        lx += 15 + (b[2] - b[0]) + 12

    footer_txt = (
        f"KW {kw:02d} / {label}  –  "
        f"{local_dt.strftime('%d.%m.%Y %H:%M Uhr')}  –  "
        f"eurest.webspeiseplan.de  |  {LOCATION_LABEL}"
    )
    d.rectangle([(0, H - FOOTER_H), (W, H)], fill=BLUE)
    b = d.textbbox((0, 0), footer_txt, font=fftr)
    d.text(
        ((W - (b[2] - b[0])) // 2, H - FOOTER_H + (FOOTER_H - (b[3] - b[1])) // 2),
        footer_txt,
        font=fftr,
        fill=WHITE,
    )

    return img


# ── Render Wochenansicht ───────────────────────────────────────────────────────
def render_week(week_data, kw, label, local_dt, holiday_map, today_date, monday_date):
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    ftit = lf(18, True)
    fdate = lf(12)
    fday = lf(17, True)
    fbdg = lf(12, True)
    fprc = lf(13, True)
    fftr = lf(10)

    HDR_H = 52
    DAY_H = 34
    LEGEND_H = 22
    STUB_W = 72
    TODAY_BW = 3
    PAD = 5

    d.rectangle([(0, 0), (W, HDR_H)], fill=BLUE)
    friday_date = monday_date + timedelta(4)
    date_range = f"{monday_date.strftime('%d.%m.%Y')} – {friday_date.strftime('%d.%m.%Y')}"
    title_str = f"{LOCATION_LABEL}  |  KW {kw:02d}"

    bt = d.textbbox((0, 0), title_str, font=ftit)
    bd = d.textbbox((0, 0), date_range, font=fdate)
    total_h = (bt[3] - bt[1]) + 3 + (bd[3] - bd[1])
    ty = (HDR_H - total_h) // 2

    d.text(((W - (bt[2] - bt[0])) // 2, ty), title_str, font=ftit, fill=WHITE)
    d.text(((W - (bd[2] - bd[0])) // 2, ty + (bt[3] - bt[1]) + 3), date_range, font=fdate, fill=(180, 210, 240))
    y = HDR_H

    all_days = list(holiday_map.keys())
    dw = (W - STUB_W) // len(all_days)

    d.rectangle([(0, y), (STUB_W - 1, y + DAY_H - 1)], fill=BLUE)
    for i, day in enumerate(all_days):
        x = STUB_W + i * dw
        is_hol = holiday_map[day] is not None
        is_past = _is_past(day, today_date)
        is_today = _is_today(day, today_date)
        col = ((220, 150, 0) if is_today else (C_HOL_HDR if is_hol else ((160, 160, 160) if is_past else LIGHT)))
        d.rectangle([(x, y), (x + dw - 1, y + DAY_H - 1)], fill=col)
        b = d.textbbox((0, 0), day, font=fday)
        d.text((x + (dw - (b[2] - b[0])) // 2, y + (DAY_H - (b[3] - b[1])) // 2), day, font=fday, fill=WHITE)
        d.line([(x, y), (x, y + DAY_H)], fill=BLUE, width=1)
        if is_today:
            d.line([(x, y), (x + dw, y)], fill=C_TODAY, width=TODAY_BW)

    y += DAY_H

    cats = []
    seen_c = set()
    for day in all_days:
        for dish in week_data.get(day, []):
            if dish["kategorie"] not in seen_c:
                cats.append(dish["kategorie"])
                seen_c.add(dish["kategorie"])

    if not cats:
        cats = CATEGORY_FALLBACK.get(LOCATION_ID, ["Suppe", "Salatbar", "Dessert"])

    avail = H - y - FOOTER_H - LEGEND_H - 4
    n_cats = len(cats)
    row_h = max(avail // n_cats, 1) if n_cats else max(avail, 1)

    for ri, cat in enumerate(cats):
        rh = row_h if ri < n_cats - 1 else max(H - y - FOOTER_H - LEGEND_H - 4 - row_h * (n_cats - 1), 1)
        d.line([(STUB_W, y), (W, y)], fill=GRID, width=1)
        avw = dw - 2 * PAD

        cands = [
            items[0]["name"]
            for day in all_days
            if holiday_map[day] is None and not _is_past(day, today_date)
            for items in [[it for it in week_data.get(day, []) if it["kategorie"] == cat]]
            if items
        ]

        uni = 17
        if cands:
            uni = _find_uniform_font_size(d, cands, avw, rh - PAD * 2 - 22, size_start=17, size_min=9)

        fn = lf(uni)
        lhn = _line_h(d, fn)

        for i, day in enumerate(all_days):
            x = STUB_W + i * dw
            is_hol = holiday_map[day] is not None
            is_past = _is_past(day, today_date)
            is_today = _is_today(day, today_date)

            bg = (
                (235, 235, 235) if is_past else
                (C_HOL_BG if is_hol else ((255, 253, 230) if is_today else (R_ODD if ri % 2 == 0 else R_EVEN)))
            )

            d.rectangle([(x, y), (x + dw - 1, y + rh - 1)], fill=bg)
            d.line([(x, y), (x, y + rh)], fill=GRID, width=1)

            if is_today:
                d.line([(x, y), (x, y + rh)], fill=C_TODAY, width=TODAY_BW)
                d.line([(x + dw - 1, y), (x + dw - 1, y + rh)], fill=C_TODAY, width=TODAY_BW)

            if is_past:
                if ri == 0:
                    b = d.textbbox((0, 0), "vergangen", font=fprc)
                    d.text((x + (dw - (b[2] - b[0])) // 2, y + rh // 2 - 10), "vergangen", font=fprc, fill=(160, 160, 160))
                continue

            if is_hol:
                if ri == 0:
                    hn = holiday_map[day]
                    b = d.textbbox((0, 0), "Feiertag", font=fbdg)
                    bw = b[2] - b[0] + 8
                    bh2 = b[3] - b[1] + 5
                    bx = x + (dw - bw) // 2
                    by = y + 8
                    d.rounded_rectangle([(bx, by), (bx + bw, by + bh2)], radius=4, fill=(160, 160, 160))
                    d.text((bx + 4, by + 2), "Feiertag", font=fbdg, fill=WHITE)
                    cy2 = by + bh2 + 5
                    fh, lhh, hlines = _fit_font(d, hn, dw - 8, rh - cy2 + y - 4)
                    for ln in hlines:
                        b2 = d.textbbox((0, 0), ln, font=fh)
                        d.text((x + (dw - (b2[2] - b2[0])) // 2, cy2), ln, font=fh, fill=C_HOL_TXT)
                        cy2 += lhh
                continue

            items = [it for it in week_data.get(day, []) if it["kategorie"] == cat]
            if not items:
                b = d.textbbox((0, 0), "–", font=lf(17))
                d.text((x + (dw - (b[2] - b[0])) // 2, y + rh // 2 - 11), "–", font=lf(17), fill=(180, 180, 180))
                continue

            it = items[0]
            cy = y + PAD

            if it["vv"]:
                bl = "Vegan" if it["vv"] == "VG" else "Veg."
                bc = C_VG if it["vv"] == "VG" else C_V
                b = d.textbbox((0, 0), bl, font=fbdg)
                bw2 = b[2] - b[0] + 8
                bh2 = b[3] - b[1] + 4
                d.rounded_rectangle([(x + PAD, cy), (x + PAD + bw2, cy + bh2)], radius=3, fill=bc)
                d.text((x + PAD + 4, cy + 2), bl, font=fbdg, fill=WHITE)
                cy += bh2 + 3

            for ln in wrap_text(d, it["name"], fn, avw, max_lines=20):
                d.text((x + PAD, cy), ln, font=fn, fill=(30, 30, 30))
                cy += lhn

            if it["preis"]:
                b = d.textbbox((0, 0), it["preis"], font=fprc)
                d.text((x + dw - (b[2] - b[0]) - PAD, y + rh - (b[3] - b[1]) - 3), it["preis"], font=fprc, fill=LIGHT)

            if is_today:
                d.line([(x, y + rh - 1), (x + dw, y + rh - 1)], fill=C_TODAY, width=TODAY_BW)

        d.rectangle([(0, y), (STUB_W - 1, y + rh - 1)], fill=BLUE)
        d.line([(STUB_W, y), (STUB_W, y + rh)], fill=GRID, width=1)
        sl = cat[:9] if len(cat) > 9 else cat

        for sz in range(11, 5, -1):
            f2 = lf(sz, True)
            b2 = d.textbbox((0, 0), sl, font=f2)
            if b2[2] - b2[0] <= STUB_W - 4:
                d.text((STUB_W // 2 - (b2[2] - b2[0]) // 2, y + rh // 2 - (b2[3] - b2[1]) // 2), sl, font=f2, fill=WHITE)
                break

        y += rh

    for i, day in enumerate(all_days):
        if _is_today(day, today_date):
            d.line([(STUB_W + i * dw, y), (STUB_W + i * dw + dw, y)], fill=C_TODAY, width=TODAY_BW)

    d.line([(0, y), (W, y)], fill=GRID, width=1)
    y += 1
    d.rectangle([(0, y), (W, y + LEGEND_H)], fill=(245, 249, 253))

    fleg = lf(11)
    lx = 6
    for col, txt in [
        (C_VG, "Vegan"),
        (C_V, "Vegetarisch"),
        (C_HOL_HDR, "Feiertag"),
        ((235, 235, 235), "vergangen"),
        (C_TODAY, "Heute"),
    ]:
        d.rectangle([(lx, y + 5), (lx + 12, y + 15)], fill=col)
        b = d.textbbox((0, 0), txt, font=fleg)
        d.text((lx + 15, y + 4), txt, font=fleg, fill=(30, 30, 30))
        lx += 15 + (b[2] - b[0]) + 12

    footer_txt = (
        f"KW {kw:02d} / {label}  –  "
        f"{local_dt.strftime('%d.%m.%Y %H:%M Uhr')}  –  "
        f"eurest.webspeiseplan.de  |  {LOCATION_LABEL}"
    )
    d.rectangle([(0, H - FOOTER_H), (W, H)], fill=BLUE)
    b = d.textbbox((0, 0), footer_txt, font=fftr)
    d.text(
        ((W - (b[2] - b[0])) // 2, H - FOOTER_H + (FOOTER_H - (b[3] - b[1])) // 2),
        footer_txt,
        font=fftr,
        fill=WHITE,
    )

    return img


# ── Persistenz / Manifest ──────────────────────────────────────────────────────
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
    target_date=None,
    target_day_label=None,
    week_monday=None,
    dish_count=0,
    is_holiday=None,
):
    manifest_path = OUT_DIR / f"current_{location_name}.json"
    data = {
        "location_name": location_name,
        "location_label": location_label,
        "display_mode": display_mode,
        "display_day": DISPLAY_DAY,
        "week_offset": WEEK_OFFSET,
        "label": label,
        "kw": kw,
        "image": out_path.name,
        "latest": latest_path.name,
        "dish_count": dish_count,
        "is_holiday": is_holiday or "",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "generated_at_local": local_dt.isoformat(),
    }

    if target_date is not None:
        data["target_date"] = target_date.isoformat()
    if target_day_label:
        data["target_day_label"] = target_day_label
    if week_monday is not None:
        data["week_monday"] = week_monday.isoformat()

    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)
    local = german_time(now)
    today_date = local.date()

    print(f"Kantine     : {LOCATION_LABEL} (ID: {LOCATION_ID})")
    print(f"DISPLAY_MODE: {DISPLAY_MODE}")
    print(f"DISPLAY_DAY : {DISPLAY_DAY!r}")
    print(f"Today       : {today_date}  {local.strftime('%H:%M')} CEST")

    all_holidays = {}
    for yr in [today_date.year, today_date.year + 1]:
        all_holidays.update(bavaria_holidays(yr))

    if DISPLAY_MODE == "day":
        target_date = resolve_target_date(local, all_holidays)
        target_monday = target_date - timedelta(days=target_date.weekday())
        label, kw = kw_label(datetime.combine(target_monday, datetime.min.time(), tzinfo=timezone.utc))
        holiday_map = week_holiday_map(target_monday)
        dk = day_key(datetime.combine(target_date, datetime.min.time()))
        is_holiday = holiday_map.get(dk)

        out_path = OUT_DIR / f"kantine_{label}_{target_date.strftime('%Y-%m-%d')}_{LOCATION_NAME}.jpg"
        latest_path = OUT_DIR / f"latest_{LOCATION_NAME}.jpg"

        print(f"Ziel-Tag    : {dk}  ({target_date})")
        print(f"Feiertag    : {is_holiday or 'nein'}")

        dishes = []

        if not is_holiday:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(
                    viewport={"width": 1400, "height": 900},
                    extra_http_headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.1"},
                )

                setup_eurest(page)

                # Zuerst versuchen, die passende KW sichtbar/aktiv zu schalten.
                available_values = ensure_target_week(page, target_date)

                target_str = target_date.strftime("%Y-%m-%d")
                if not any(v.startswith(target_str) for v in available_values):
                    _dump_debug(page, "day-target-missing")
                    browser.close()
                    print(f"FEHLER: Ziel-Datum {target_str} ist nach KW-Umschaltung nicht im menuDaySelect vorhanden.")
                    print(f"FEHLER: Verfügbare Werte: {available_values}")
                    sys.exit(1)

                dishes = scrape_day(page, target_date, available_values)
                browser.close()

            if not dishes:
                print("FEHLER: Kein Feiertag, aber keine Gerichte fuer den Ziel-Tag gefunden.")
                print("FEHLER: Abbruch, damit latest-Bild NICHT mit leerem/falschem Inhalt ueberschrieben wird.")
                sys.exit(1)

        print(f"Gerichte    : {len(dishes)}")
        img = render_day(dishes, target_date, kw, label, local, is_holiday)
        img.save(str(out_path), "JPEG", quality=92)
        print(f"Saved: {out_path}")

        shutil.copy(str(out_path), str(latest_path))
        print(f"{latest_path.name} aktualisiert")

        _write_current_manifest(
            location_name=LOCATION_NAME,
            location_label=LOCATION_LABEL,
            display_mode="day",
            label=label,
            kw=kw,
            out_path=out_path,
            latest_path=latest_path,
            local_dt=local,
            target_date=target_date,
            target_day_label=dk,
            week_monday=target_monday,
            dish_count=len(dishes),
            is_holiday=is_holiday,
        )

        _prune_old_images(LOCATION_NAME)

    else:
        target_monday = today_date - timedelta(days=today_date.weekday()) + timedelta(weeks=WEEK_OFFSET)
        label, kw = kw_label(datetime.combine(target_monday, datetime.min.time(), tzinfo=timezone.utc))
        out_path = OUT_DIR / f"kantine_{label}_{LOCATION_NAME}.jpg"
        latest_path = OUT_DIR / f"latest_{LOCATION_NAME}.jpg"

        print(f"Week label  : {label}  (KW {kw:02d})")
        print(f"WEEK_OFFSET : {WEEK_OFFSET}  ->  ab {target_monday}")

        holiday_map = week_holiday_map(target_monday)
        hol_days = [k for k, v in holiday_map.items() if v]
        all_week_dates = [
            datetime.combine(target_monday + timedelta(i), datetime.min.time(), tzinfo=timezone.utc)
            for i in range(5)
        ]
        scrape_dates = [
            dt for dt in all_week_dates
            if (WEEK_OFFSET != 0 or dt.date() >= today_date) and day_key(dt) not in hol_days
        ]

        print(f"Feiertage   : {[(k, holiday_map[k]) for k in hol_days] or 'keine'}")
        print(f"Scraping    : {[day_key(dt) for dt in scrape_dates]}")

        week_data = {}

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1400, "height": 900},
                extra_http_headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.1"},
            )

            setup_eurest(page)

            # Auch im week-Mode zunaechst die Ziel-KW scharf setzen.
            available_values = ensure_target_week(page, target_monday)

            for date_obj in scrape_dates:
                dk = day_key(date_obj)
                target_str = date_obj.strftime("%Y-%m-%d")
                if not any(v.startswith(target_str) for v in available_values):
                    print(f"[week] WARNUNG: {target_str} nicht in menuDaySelect vorhanden -> skip")
                    continue

                d2 = scrape_day(page, date_obj, available_values)
                if d2:
                    week_data[dk] = d2

            browser.close()

        print(f"\nErgebnis    : {list(week_data.keys())}  ({len(week_data)}/{len(scrape_dates)})")
        if not week_data:
            print("Keine Speisedaten – kein Bild.")
            sys.exit(1)

        img = render_week(week_data, kw, label, local, holiday_map, today_date, target_monday)
        img.save(str(out_path), "JPEG", quality=92)
        print(f"Saved: {out_path}")

        shutil.copy(str(out_path), str(latest_path))
        print(f"{latest_path.name} aktualisiert")

        _write_current_manifest(
            location_name=LOCATION_NAME,
            location_label=LOCATION_LABEL,
            display_mode="week",
            label=label,
            kw=kw,
            out_path=out_path,
            latest_path=latest_path,
            local_dt=local,
            week_monday=target_monday,
            dish_count=sum(len(v) for v in week_data.values()),
            is_holiday="",
        )

        _prune_old_images(LOCATION_NAME)


if __name__ == "__main__":
    main()
