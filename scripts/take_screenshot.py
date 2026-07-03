#!/usr/bin/env python3
"""Eurest Kantine Regensburg – Schaeffler & Aumovio – 800x600 JPEG.

Umgebungsvariablen:
  EUREST_LOCATION_ID    8949 = Schaeffler, 8950 = Aumovio (default: 8949)
  EUREST_LOCATION_NAME  schaeffler / aumovio (default: schaeffler)
  WEEK_OFFSET           0 = aktuelle Woche, 1 = naechste Woche (default: 0, nur fuer week-Modus)
  DISPLAY_MODE          day = Tagesansicht/Matrix (default), week = Wochenansicht
  DISPLAY_DAY           Ziel-Tag manuell: monday|tuesday|wednesday|thursday|friday
                        (leer = Automatik: vor 13:30 heute, ab 13:30 naechster Werktag)

v5:
- DOM-Scraping ueber dayBtn/dayName/dayDate/Textkandidaten
- Netzwerk-/JSON-Scraping als zusaetzlicher Haupt-Fallback
- HTML-Extraktion aus Response-Bodies
- PDF-Fallback als letzter Rettungsanker
- Debug-Dumps fuer HTML, PNG, Netzwerk und PDF-Text
"""

import io
import os
import re
import sys
import zlib
import json
import math
import shutil
import tempfile
import subprocess
from html import unescape
from pathlib import Path
from datetime import date, datetime, timezone, timedelta
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont

# ── Config ─────────────────────────────────────────────────────────────────────
EUREST_URL = "https://eurest.webspeiseplan.de/39799C127F748D639984F4CDBEB44846"
LOCATION_ID = os.environ.get("EUREST_LOCATION_ID", "8949").strip()
LOCATION_NAME = os.environ.get("EUREST_LOCATION_NAME", "schaeffler").strip().lower()
WEEK_OFFSET = int(os.environ.get("WEEK_OFFSET", "0"))
DISPLAY_MODE = os.environ.get("DISPLAY_MODE", "day").strip().lower()
DISPLAY_DAY = os.environ.get("DISPLAY_DAY", "").strip().lower()

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

EXTRA_CATEGORY_CANDIDATES = [
    "EssBar Lunch 11:00-13:30",
    "EssBar Lunch",
    "Dessert 2",
    "Dessert",
]

DAY_NAME_MAP = {
    "monday": 0, "montag": 0, "mo": 0,
    "tuesday": 1, "dienstag": 1, "di": 1,
    "wednesday": 2, "mittwoch": 2, "mi": 2,
    "thursday": 3, "donnerstag": 3, "do": 3,
    "friday": 4, "freitag": 4, "fr": 4,
}

GERMAN_DAY_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
GERMAN_DAY_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

OUT_DIR = Path("docs/images")
OUT_DIR.mkdir(parents=True, exist_ok=True)
MAX_KEEP = 14
W, H = 800, 600
FOOTER_H = 20

# ── Colours ────────────────────────────────────────────────────────────────────
BLUE = (0, 57, 107)
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
    return f"{GERMAN_DAY_SHORT[dt_obj.weekday()]} {dt_obj.strftime('%d.%m')}"


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


# ── Allgemeine Helfer ──────────────────────────────────────────────────────────
def _normalize_day_date(s):
    return (s or "").strip().rstrip(".")


def _date_token_for_button(date_obj):
    return date_obj.strftime("%d.%m.")


def _weekday_token_for_button(date_obj):
    return GERMAN_DAY_SHORT[date_obj.weekday()]


def _sanitize_label(s):
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    return s[:120]


def _save_text_dump(label, text):
    path = OUT_DIR / f"debug_{_sanitize_label(label)}.txt"
    try:
        path.write_text(text, encoding="utf-8")
        print(f"[debug-dump] TXT geschrieben: {path}")
    except Exception as e:
        print(f"[debug-dump] TXT-Fehler: {e}")


def _save_debug_dump(page, label):
    safe = _sanitize_label(f"{LOCATION_NAME}_{label}")
    html_path = OUT_DIR / f"debug_{safe}.html"
    png_path = OUT_DIR / f"debug_{safe}.png"
    try:
        html = page.content()
        html_path.write_text(html, encoding="utf-8")
        print(f"[debug-dump] HTML geschrieben: {html_path}")
    except Exception as e:
        print(f"[debug-dump] HTML-Fehler: {e}")
    try:
        page.screenshot(path=str(png_path), full_page=True)
        print(f"[debug-dump] Screenshot geschrieben: {png_path}")
    except Exception as e:
        print(f"[debug-dump] PNG-Fehler: {e}")


def _save_json_dump(label, data):
    path = OUT_DIR / f"debug_{_sanitize_label(label)}.json"
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[debug-dump] JSON geschrieben: {path}")
    except Exception as e:
        print(f"[debug-dump] JSON-Fehler: {e}")


def _unique_preserve(items):
    out = []
    seen = set()
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _category_candidates():
    return _unique_preserve(CATEGORY_FALLBACK.get(LOCATION_ID, []) + EXTRA_CATEGORY_CANDIDATES)


def _strip_tags(html):
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</div>|</p>|</section>|</li>|</tr>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = unescape(html)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{2,}", "\n", html)
    return html.strip()


# ── Netzwerk-Capture ───────────────────────────────────────────────────────────
class ResponseCapture:
    def __init__(self, target_date):
        self.target_date = target_date
        self.entries = []
        self.counter = 0

    def attach(self, page):
        def on_response(response):
            try:
                self._handle_response(response)
            except Exception as e:
                print(f"[net] response-capture Fehler: {e}")

        page.on("response", on_response)

    def _score_text(self, url, content_type, text):
        if not text:
            return 0

        score = 0
        target_iso = self.target_date.strftime("%Y-%m-%d")
        target_dm = self.target_date.strftime("%d.%m.")
        target_dm2 = self.target_date.strftime("%d.%m")
        day_short = GERMAN_DAY_SHORT[self.target_date.weekday()]
        day_long = GERMAN_DAY_LONG[self.target_date.weekday()]

        low = text.lower()
        low_url = url.lower()
        low_ct = content_type.lower()

        if target_iso in text:
            score += 20
        if target_dm in text or target_dm2 in text:
            score += 12
        if day_short.lower() in low and (target_dm in text or target_dm2 in text):
            score += 8
        if day_long.lower() in low and (target_dm in text or target_dm2 in text):
            score += 8

        for cat in _category_candidates():
            if cat.lower() in low:
                score += 1

        for token in ["meal-wrapper", "mealnamewrapper", "categoryname", "menuListWrapper".lower(), "menuDaySelect".lower()]:
            if token in low:
                score += 3

        for token in ["json", "api", "menu", "speise", "outlet", "week", "tag", "day"]:
            if token in low_url or token in low_ct:
                score += 1

        return score

    def _handle_response(self, response):
        url = response.url
        status = response.status
        req_type = getattr(response.request, "resource_type", "")
        headers = response.headers or {}
        content_type = headers.get("content-type", "")

        capture_text = False
        if any(x in content_type.lower() for x in ["json", "javascript", "text", "html"]):
            capture_text = True
        if any(x in url.lower() for x in ["menu", "speise", "outlet", "api", "json", "week", "day"]):
            capture_text = True
        if req_type in ["xhr", "fetch", "document"]:
            capture_text = True

        body = ""
        score = 0

        if capture_text:
            try:
                body = response.text()
            except Exception:
                body = ""

            if len(body) > 2_500_000:
                body = body[:2_500_000]

            score = self._score_text(url, content_type, body)

        entry = {
            "idx": self.counter,
            "url": url,
            "status": status,
            "resource_type": req_type,
            "content_type": content_type,
            "score": score,
            "body": body,
            "body_excerpt": body[:3000] if body else "",
        }
        self.entries.append(entry)
        self.counter += 1

    def dump(self, label):
        serializable = [
            {
                "idx": e["idx"],
                "url": e["url"],
                "status": e["status"],
                "resource_type": e["resource_type"],
                "content_type": e["content_type"],
                "score": e["score"],
                "body_excerpt": e["body_excerpt"],
            }
            for e in self.entries
        ]
        _save_json_dump(label, serializable)

    def candidate_entries(self):
        sorted_entries = sorted(
            self.entries,
            key=lambda e: (e.get("score", 0), e.get("idx", 0)),
            reverse=True,
        )
        return [e for e in sorted_entries if e.get("body")]


# ── JS / DOM Helfer ────────────────────────────────────────────────────────────
JS_DEBUG_DOM = r"""
(function(){
  function uniqueElements(arr){
    var out = [];
    var seen = new Set();
    for (var i = 0; i < arr.length; i++) {
      var el = arr[i];
      if (!el) continue;
      if (seen.has(el)) continue;
      seen.add(el);
      out.push(el);
    }
    return out;
  }

  function isVisible(el){
    if (!el) return false;
    if (el.offsetParent !== null) return true;
    var st = window.getComputedStyle(el);
    return st && st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
  }

  function collapseText(s){
    return (s || '').replace(/\s+/g, ' ').trim();
  }

  function collectDayRoots(){
    var roots = Array.from(document.querySelectorAll('.dayBtn, p.dayBtn, [class*="dayBtn"]'));

    if (roots.length === 0) {
      var nameNodes = Array.from(document.querySelectorAll('.dayName'));
      var fallback = [];
      for (var i = 0; i < nameNodes.length; i++) {
        var n = nameNodes[i];
        var p = n.closest('p,button,div,a,span') || n.parentElement;
        if (p && p.querySelector && p.querySelector('.dayDate')) fallback.push(p);
      }
      roots = uniqueElements(fallback);
    }

    if (roots.length === 0) {
      var toolbar = document.querySelector('.menuToolBar, .menuToolBarWrapper, .menuSplitContent') || document.body;
      var nodes = Array.from(toolbar.querySelectorAll('p,button,div,a,span'));
      var textFallback = [];
      for (var j = 0; j < nodes.length; j++) {
        var el = nodes[j];
        if (!isVisible(el)) continue;
        var txt = collapseText(el.textContent);
        if (!txt || txt.length > 40) continue;
        var hasDay = /\b(Mo|Di|Mi|Do|Fr)\b/.test(txt);
        var hasDate = /\b\d{2}\.\d{2}\.?\b/.test(txt);
        if (hasDay && hasDate) textFallback.push(el);
      }
      roots = uniqueElements(textFallback);
    }

    return roots;
  }

  function parseCandidate(el){
    var n = el.querySelector ? el.querySelector('.dayName') : null;
    var d = el.querySelector ? el.querySelector('.dayDate') : null;
    var txt = collapseText(el.textContent);
    var day = n ? collapseText(n.textContent) : '';
    var dat = d ? collapseText(d.textContent) : '';

    if (!day) {
      var m1 = txt.match(/\b(Mo|Di|Mi|Do|Fr)\b/);
      if (m1) day = m1[1];
    }
    if (!dat) {
      var m2 = txt.match(/\b(\d{2}\.\d{2}\.?)\b/);
      if (m2) dat = m2[1];
    }

    var cls = el.className || '';
    var active = cls.indexOf('background-lightGrey') !== -1 ||
                 cls.indexOf('text-ci') !== -1 ||
                 cls.indexOf('active') !== -1 ||
                 cls.indexOf('selected') !== -1 ||
                 el.getAttribute('aria-selected') === 'true';

    return {
      tag: (el.tagName || '').toLowerCase(),
      day: day,
      date: dat,
      text: txt,
      className: cls,
      active: active
    };
  }

  var roots = collectDayRoots();
  var out = [];

  var sel = document.querySelector('select.menuDaySelect');
  if (sel) {
    var opts = Array.from(sel.options).map(function(o){ return o.value + '=' + o.text.trim(); });
    out.push('menuDaySelect options: ' + opts.join(', '));
    out.push('menuDaySelect current: ' + sel.value);
  } else {
    out.push('NO select.menuDaySelect');
  }

  out.push('dayBtns: ' + roots.map(function(el){
    var c = parseCandidate(el);
    return (c.day + ' ' + c.date + ' {' + c.tag + '} [' + c.className + ']');
  }).join(' | '));

  var weekBtns = Array.from(document.querySelectorAll('.menuWeekSelection button')).map(function(el){
    var wn = el.querySelector('.weekDate');
    return ((wn ? wn.textContent.trim() : '?') + ' value=' + (el.value || '') + ' [' + el.className + ']');
  });
  out.push('weekBtns: ' + weekBtns.join(' | '));

  var toolbar = document.querySelector('.menuToolBar, .menuToolBarWrapper');
  out.push('toolbar-text: ' + collapseText(toolbar ? toolbar.textContent : '').substring(0, 400));

  var pdfLink = '';
  var a = document.querySelector('.menuPdfLink a[href]');
  if (a && a.href) pdfLink = a.href;
  if (!pdfLink) {
    var all = Array.from(document.querySelectorAll('a[href]')).map(function(x){ return x.href; });
    var hit = all.find(function(h){ return /\/exportfiles\/.*\.pdf(\?|$)/i.test(h); });
    if (hit) pdfLink = hit;
  }
  out.push('pdfLink: ' + pdfLink);

  out.push('html-has-dayBtn-token: ' + (document.body.innerHTML.indexOf('dayBtn') !== -1));

  var mlw = document.querySelector('.menuListWrapper');
  out.push('menuListWrapper: ' + (mlw ? 'FOUND' : 'NOT FOUND'));

  var mws = document.querySelectorAll('.meal-wrapper');
  out.push('meal-wrapper count: ' + mws.length);

  var cns = document.querySelectorAll('.categoryName');
  out.push('categoryName: ' + Array.from(cns).map(function(e){ return e.textContent.trim(); }).join(', '));

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

      if (mealName) result.push({category: cat, name: mealName, price: price, features: features});
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

      if (mealName2) result.push({category: catName, name: mealName2, price: price2, features: features2});
    }
  }

  return JSON.stringify(result);
})()
"""

JS_DAY_CANDIDATES = r"""
(function(){
  function uniqueElements(arr){
    var out = [];
    var seen = new Set();
    for (var i = 0; i < arr.length; i++) {
      var el = arr[i];
      if (!el) continue;
      if (seen.has(el)) continue;
      seen.add(el);
      out.push(el);
    }
    return out;
  }

  function isVisible(el){
    if (!el) return false;
    if (el.offsetParent !== null) return true;
    var st = window.getComputedStyle(el);
    return st && st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
  }

  function collapseText(s){
    return (s || '').replace(/\s+/g, ' ').trim();
  }

  function collectDayRoots(){
    var roots = Array.from(document.querySelectorAll('.dayBtn, p.dayBtn, [class*="dayBtn"]'));

    if (roots.length === 0) {
      var nameNodes = Array.from(document.querySelectorAll('.dayName'));
      var fallback = [];
      for (var i = 0; i < nameNodes.length; i++) {
        var n = nameNodes[i];
        var p = n.closest('p,button,div,a,span') || n.parentElement;
        if (p && p.querySelector && p.querySelector('.dayDate')) fallback.push(p);
      }
      roots = uniqueElements(fallback);
    }

    if (roots.length === 0) {
      var toolbar = document.querySelector('.menuToolBar, .menuToolBarWrapper, .menuSplitContent') || document.body;
      var nodes = Array.from(toolbar.querySelectorAll('p,button,div,a,span'));
      var textFallback = [];
      for (var j = 0; j < nodes.length; j++) {
        var el = nodes[j];
        if (!isVisible(el)) continue;
        var txt = collapseText(el.textContent);
        if (!txt || txt.length > 40) continue;
        var hasDay = /\b(Mo|Di|Mi|Do|Fr)\b/.test(txt);
        var hasDate = /\b\d{2}\.\d{2}\.?\b/.test(txt);
        if (hasDay && hasDate) textFallback.push(el);
      }
      roots = uniqueElements(textFallback);
    }

    return roots;
  }

  function parseCandidate(el){
    var n = el.querySelector ? el.querySelector('.dayName') : null;
    var d = el.querySelector ? el.querySelector('.dayDate') : null;
    var txt = collapseText(el.textContent);
    var day = n ? collapseText(n.textContent) : '';
    var dat = d ? collapseText(d.textContent) : '';

    if (!day) {
      var m1 = txt.match(/\b(Mo|Di|Mi|Do|Fr)\b/);
      if (m1) day = m1[1];
    }
    if (!dat) {
      var m2 = txt.match(/\b(\d{2}\.\d{2}\.?)\b/);
      if (m2) dat = m2[1];
    }

    var cls = el.className || '';
    var active = cls.indexOf('background-lightGrey') !== -1 ||
                 cls.indexOf('text-ci') !== -1 ||
                 cls.indexOf('active') !== -1 ||
                 cls.indexOf('selected') !== -1 ||
                 el.getAttribute('aria-selected') === 'true';

    return {
      tag: (el.tagName || '').toLowerCase(),
      day: day,
      date: dat,
      text: txt,
      className: cls,
      active: active
    };
  }

  return JSON.stringify(collectDayRoots().map(parseCandidate));
})()
"""

JS_WEEK_BUTTONS = r"""
(function(){
  var out = Array.from(document.querySelectorAll('.menuWeekSelection button')).map(function(el){
    var wn = el.querySelector('.weekDate');
    var cls = el.className || '';
    return {
      value: el.value || '',
      week: wn ? wn.textContent.trim() : '',
      className: cls,
      active: cls.indexOf('background-ci') !== -1
    };
  });
  return JSON.stringify(out);
})()
"""

JS_MENU_SNAPSHOT = r"""
(function(){
  function uniqueElements(arr){
    var out = [];
    var seen = new Set();
    for (var i = 0; i < arr.length; i++) {
      var el = arr[i];
      if (!el) continue;
      if (seen.has(el)) continue;
      seen.add(el);
      out.push(el);
    }
    return out;
  }

  function isVisible(el){
    if (!el) return false;
    if (el.offsetParent !== null) return true;
    var st = window.getComputedStyle(el);
    return st && st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
  }

  function collapseText(s){
    return (s || '').replace(/\s+/g, ' ').trim();
  }

  function collectDayRoots(){
    var roots = Array.from(document.querySelectorAll('.dayBtn, p.dayBtn, [class*="dayBtn"]'));

    if (roots.length === 0) {
      var nameNodes = Array.from(document.querySelectorAll('.dayName'));
      var fallback = [];
      for (var i = 0; i < nameNodes.length; i++) {
        var n = nameNodes[i];
        var p = n.closest('p,button,div,a,span') || n.parentElement;
        if (p && p.querySelector && p.querySelector('.dayDate')) fallback.push(p);
      }
      roots = uniqueElements(fallback);
    }

    if (roots.length === 0) {
      var toolbar = document.querySelector('.menuToolBar, .menuToolBarWrapper, .menuSplitContent') || document.body;
      var nodes = Array.from(toolbar.querySelectorAll('p,button,div,a,span'));
      var textFallback = [];
      for (var j = 0; j < nodes.length; j++) {
        var el = nodes[j];
        if (!isVisible(el)) continue;
        var txt = collapseText(el.textContent);
        if (!txt || txt.length > 40) continue;
        var hasDay = /\b(Mo|Di|Mi|Do|Fr)\b/.test(txt);
        var hasDate = /\b\d{2}\.\d{2}\.?\b/.test(txt);
        if (hasDay && hasDate) textFallback.push(el);
      }
      roots = uniqueElements(textFallback);
    }

    return roots;
  }

  function parseCandidate(el){
    var n = el.querySelector ? el.querySelector('.dayName') : null;
    var d = el.querySelector ? el.querySelector('.dayDate') : null;
    var txt = collapseText(el.textContent);
    var day = n ? collapseText(n.textContent) : '';
    var dat = d ? collapseText(d.textContent) : '';

    if (!day) {
      var m1 = txt.match(/\b(Mo|Di|Mi|Do|Fr)\b/);
      if (m1) day = m1[1];
    }
    if (!dat) {
      var m2 = txt.match(/\b(\d{2}\.\d{2}\.?)\b/);
      if (m2) dat = m2[1];
    }

    var cls = el.className || '';
    var active = cls.indexOf('background-lightGrey') !== -1 ||
                 cls.indexOf('text-ci') !== -1 ||
                 cls.indexOf('active') !== -1 ||
                 cls.indexOf('selected') !== -1 ||
                 el.getAttribute('aria-selected') === 'true';

    return {
      day: day,
      date: dat,
      text: txt,
      className: cls,
      active: active
    };
  }

  function activeDayInfo(){
    var nodes = collectDayRoots().map(parseCandidate);
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].active) return {name: nodes[i].day || '', date: nodes[i].date || ''};
    }
    return {name:'', date:''};
  }

  function activeWeekInfo(){
    var nodes = Array.from(document.querySelectorAll('.menuWeekSelection button'));
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var cls = el.className || '';
      if (cls.indexOf('background-ci') !== -1) {
        var wn = el.querySelector('.weekDate');
        return {value: el.value || '', week: wn ? wn.textContent.trim() : ''};
      }
    }
    return {value:'', week:''};
  }

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

  var ad = activeDayInfo();
  var aw = activeWeekInfo();

  return JSON.stringify({
    selectValue: sel ? sel.value : '',
    mealCount: mealEls.length,
    textLength: mlw ? (mlw.innerText || '').trim().length : 0,
    signature: signature,
    firstMeals: meals.slice(0, 3),
    activeDayName: ad.name,
    activeDayDate: ad.date,
    activeWeekValue: aw.value,
    activeWeekNumber: aw.week
  });
})()
"""

JS_PDF_LINK = r"""
(function(){
  var a = document.querySelector('.menuPdfLink a[href]');
  if (a && a.href) return a.href;

  var all = Array.from(document.querySelectorAll('a[href]')).map(function(x){ return x.href; });
  var hit = all.find(function(h){ return /\/exportfiles\/.*\.pdf(\?|$)/i.test(h); });
  return hit || '';
})()
"""


# ── DOM / Klick-Helfer ─────────────────────────────────────────────────────────
def _dump_debug(page, label):
    try:
        dbg = page.evaluate(JS_DEBUG_DOM)
        print(f"[debug:{label}] {dbg}")
    except Exception as e:
        print(f"[debug:{label}] Fehler: {e}")


def _get_day_candidates(page):
    try:
        vals = json.loads(page.evaluate(JS_DAY_CANDIDATES))
        if isinstance(vals, list):
            print(f"[day-candidates] {vals}")
            return vals
    except Exception as e:
        print(f"[day-candidates] Fehler: {e}")
    return []


def _get_week_buttons(page):
    try:
        vals = json.loads(page.evaluate(JS_WEEK_BUTTONS))
        if isinstance(vals, list):
            print(f"[week-buttons] {vals}")
            return vals
    except Exception as e:
        print(f"[week-buttons] Fehler: {e}")
    return []


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
            "activeDayName": "",
            "activeDayDate": "",
            "activeWeekValue": "",
            "activeWeekNumber": "",
        }


def _extract_pdf_link(page):
    try:
        url = page.evaluate(JS_PDF_LINK)
        if isinstance(url, str) and url.strip():
            print(f"[pdf] Link gefunden: {url}")
            return url.strip()
    except Exception as e:
        print(f"[pdf] DOM-Link-Fehler: {e}")
    print("[pdf] Kein PDF-Link im DOM gefunden")
    return ""


def _current_view_matches_target(page, date_obj):
    snap = _get_menu_snapshot(page)
    target_day = _weekday_token_for_button(date_obj)
    target_date = _normalize_day_date(_date_token_for_button(date_obj))
    target_prefix = date_obj.strftime("%Y-%m-%d")

    if snap.get("activeDayName") == target_day and _normalize_day_date(snap.get("activeDayDate")) == target_date:
        return True

    if (snap.get("selectValue") or "").startswith(target_prefix):
        return True

    return False


def _click_weiter(page):
    try:
        btn_info = page.evaluate(r"""
        Array.from(document.querySelectorAll('button')).map(function(b){
          return JSON.stringify({
            text:b.textContent.trim().substring(0,40),
            cls:b.className.substring(0,60),
            disabled:b.disabled,
            visible:b.offsetParent!==null
          });
        }).join('\n')
        """)
        print(f"[weiter] Buttons:\n{btn_info}")
    except Exception as e:
        print(f"[weiter] Dump-Fehler: {e}")

    clicked = page.evaluate(r"""
    (function(){
      var btns = Array.from(document.querySelectorAll('button'));
      var kws = ['weiter','next','submit','ok','bestätigen','bestatigen','los'];
      for (var b of btns) {
        var t = b.textContent.trim().toLowerCase();
        for (var kw of kws) {
          if (t.indexOf(kw)!==-1) {
            try { b.click(); } catch(e) {}
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
    for sel in [
        ".dayBtn",
        ".dayName",
        ".menuWeekSelection button",
        ".menuListWrapper",
        "select.menuDaySelect",
        ".meal-wrapper",
        ".mealNameWrapper",
    ]:
        try:
            page.wait_for_selector(sel, timeout=12000)
            loaded = True
            print(f"[setup] Selector geladen: {sel}")
            break
        except Exception:
            pass

    if not loaded:
        print("[setup] WARNUNG: Weder Tagesnavigation noch menuListWrapper rechtzeitig gefunden")

    _dump_debug(page, "final")
    print("[setup] Abgeschlossen")


def _current_week_value_candidates(expected_date):
    iso_year, iso_week, _ = expected_date.isocalendar()
    vals = [f"{iso_year}-{iso_week}", f"{iso_year}-{iso_week:02d}"]
    return _unique_preserve(vals)


def _target_day_present_in_candidates(page, date_obj):
    target_day = _weekday_token_for_button(date_obj)
    target_date = _normalize_day_date(_date_token_for_button(date_obj))
    candidates = _get_day_candidates(page)

    for c in candidates:
        c_day = (c.get("day") or "").strip()
        c_date = _normalize_day_date(c.get("date") or "")
        c_text = c.get("text") or ""

        if c_day == target_day and c_date == target_date:
            return True

        norm_text = _normalize_day_date(c_text)
        if target_day in c_text and target_date in norm_text:
            return True

    return False


def click_week_button(page, expected_date):
    candidates = _current_week_value_candidates(expected_date)
    _, iso_week, _ = expected_date.isocalendar()
    target_week_str = str(iso_week)

    print(f"[kw] click target values={candidates} week={target_week_str}")

    try:
        result = page.evaluate(r"""
            (params) => {
              function fire(el){
                var cur = el;
                for (var depth = 0; depth < 4 && cur; depth++) {
                  try { cur.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                  var types = ['pointerdown','mousedown','mouseup','click'];
                  for (var i = 0; i < types.length; i++) {
                    try {
                      cur.dispatchEvent(new MouseEvent(types[i], {
                        bubbles: true,
                        cancelable: true,
                        view: window
                      }));
                    } catch(e) {}
                  }
                  try { cur.click(); } catch(e) {}
                  cur = cur.parentElement;
                }
              }

              var buttons = Array.from(document.querySelectorAll('.menuWeekSelection button'));
              for (var i = 0; i < buttons.length; i++) {
                var el = buttons[i];
                var val = (el.value || '').trim();
                var wn = el.querySelector('.weekDate');
                var weekText = wn ? wn.textContent.trim() : '';
                if (params.values.indexOf(val) !== -1 || weekText === params.week) {
                  fire(el);
                  return 'clicked:' + (val || weekText);
                }
              }
              return 'no-match';
            }
        """, {"values": candidates, "week": target_week_str})
        print(f"[kw] Ergebnis: {result!r}")
        return "clicked:" in str(result)
    except Exception as e:
        print(f"[kw] Klick-Fehler: {e}")
        return False


def click_day_candidate(page, date_obj):
    target_day = _weekday_token_for_button(date_obj)
    target_date = _date_token_for_button(date_obj)

    print(f"[day] Ziel: {target_day} {target_date}")

    try:
        result = page.evaluate(r"""
            (params) => {
              function uniqueElements(arr){
                var out = [];
                var seen = new Set();
                for (var i = 0; i < arr.length; i++) {
                  var el = arr[i];
                  if (!el) continue;
                  if (seen.has(el)) continue;
                  seen.add(el);
                  out.push(el);
                }
                return out;
              }

              function isVisible(el){
                if (!el) return false;
                if (el.offsetParent !== null) return true;
                var st = window.getComputedStyle(el);
                return st && st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
              }

              function collapseText(s){
                return (s || '').replace(/\s+/g, ' ').trim();
              }

              function normDate(s){
                return (s || '').trim().replace(/\.$/, '');
              }

              function collectDayRoots(){
                var roots = Array.from(document.querySelectorAll('.dayBtn, p.dayBtn, [class*="dayBtn"]'));

                if (roots.length === 0) {
                  var nameNodes = Array.from(document.querySelectorAll('.dayName'));
                  var fallback = [];
                  for (var i = 0; i < nameNodes.length; i++) {
                    var n = nameNodes[i];
                    var p = n.closest('p,button,div,a,span') || n.parentElement;
                    if (p && p.querySelector && p.querySelector('.dayDate')) fallback.push(p);
                  }
                  roots = uniqueElements(fallback);
                }

                if (roots.length === 0) {
                  var toolbar = document.querySelector('.menuToolBar, .menuToolBarWrapper, .menuSplitContent') || document.body;
                  var nodes = Array.from(toolbar.querySelectorAll('p,button,div,a,span'));
                  var textFallback = [];
                  for (var j = 0; j < nodes.length; j++) {
                    var el = nodes[j];
                    if (!isVisible(el)) continue;
                    var txt = collapseText(el.textContent);
                    if (!txt || txt.length > 40) continue;
                    var hasDay = /\b(Mo|Di|Mi|Do|Fr)\b/.test(txt);
                    var hasDate = /\b\d{2}\.\d{2}\.?\b/.test(txt);
                    if (hasDay && hasDate) textFallback.push(el);
                  }
                  roots = uniqueElements(textFallback);
                }

                return roots;
              }

              function fire(el){
                var cur = el;
                for (var depth = 0; depth < 6 && cur; depth++) {
                  try { cur.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                  var types = ['pointerdown','mousedown','mouseup','click'];
                  for (var i = 0; i < types.length; i++) {
                    try {
                      cur.dispatchEvent(new MouseEvent(types[i], {
                        bubbles: true,
                        cancelable: true,
                        view: window
                      }));
                    } catch(e) {}
                  }
                  try { cur.click(); } catch(e) {}
                  cur = cur.parentElement;
                }
              }

              var nodes = collectDayRoots();
              for (var i = 0; i < nodes.length; i++) {
                var el = nodes[i];
                var n = el.querySelector ? el.querySelector('.dayName') : null;
                var d = el.querySelector ? el.querySelector('.dayDate') : null;

                var dayName = n ? collapseText(n.textContent) : '';
                var dayDate = d ? collapseText(d.textContent) : '';
                var txt = collapseText(el.textContent);

                if (!dayName) {
                  var m1 = txt.match(/\b(Mo|Di|Mi|Do|Fr)\b/);
                  if (m1) dayName = m1[1];
                }
                if (!dayDate) {
                  var m2 = txt.match(/\b(\d{2}\.\d{2}\.?)\b/);
                  if (m2) dayDate = m2[1];
                }

                var matchByParts =
                  dayName === params.day &&
                  normDate(dayDate) === normDate(params.date);

                var matchByText =
                  txt.indexOf(params.day) !== -1 &&
                  txt.indexOf(normDate(params.date)) !== -1;

                if (matchByParts || matchByText) {
                  fire(el);
                  return 'clicked:' + dayName + ' ' + dayDate + ' text=' + txt;
                }
              }

              return 'no-match';
            }
        """, {"day": target_day, "date": target_date})
        print(f"[day] Ergebnis: {result!r}")
        return "clicked:" in str(result)
    except Exception as e:
        print(f"[day] Klick-Fehler: {e}")
        return False


def click_day_select(page, date_obj):
    target = date_obj.strftime("%Y-%m-%d")
    available_values = _get_available_dates(page)
    matched = next((v for v in available_values if v.startswith(target)), None)

    if not matched:
        print(f"[select] kein Wert fuer {target}")
        return False

    try:
        page.select_option("select.menuDaySelect", value=matched, timeout=5000)
        print(f"[select] gewaehlt: {matched}")
        return True
    except Exception as e:
        print(f"[select] Fehler: {e}")
        return False


def _wait_for_stable_menu(
    page,
    *,
    expected_week_values=None,
    expected_day_date=None,
    expected_select_prefix=None,
    require_change=False,
    before_snapshot=None,
    max_wait_ms=10000,
    interval_ms=500,
):
    before_snapshot = before_snapshot or {}
    before_sig = before_snapshot.get("signature", "")
    last_sig = None
    stable = 0
    polls = max_wait_ms // interval_ms
    last_snapshot = before_snapshot

    exp_day_norm = _normalize_day_date(expected_day_date) if expected_day_date else None
    exp_week_values = set(expected_week_values or [])

    for idx in range(polls):
        page.wait_for_timeout(interval_ms)
        snap = _get_menu_snapshot(page)
        last_snapshot = snap

        sig = snap.get("signature", "")
        meal_count = snap.get("mealCount", 0)
        active_day = snap.get("activeDayDate", "")
        active_week = snap.get("activeWeekValue", "")
        current_select = snap.get("selectValue", "")

        print(
            f"[stable] poll={idx+1}/{polls} "
            f"week={active_week!r} day={active_day!r} select={current_select!r} "
            f"meals={meal_count} siglen={len(sig)} first={snap.get('firstMeals', [])}"
        )

        if exp_week_values and active_week and active_week not in exp_week_values:
            stable = 0
            last_sig = sig
            continue

        if exp_day_norm and active_day and _normalize_day_date(active_day) != exp_day_norm:
            stable = 0
            last_sig = sig
            continue

        if expected_select_prefix and current_select and not current_select.startswith(expected_select_prefix):
            stable = 0
            last_sig = sig
            continue

        if meal_count <= 0 or not sig:
            stable = 0
            last_sig = sig
            continue

        if require_change and before_sig and sig == before_sig:
            stable = 0
            last_sig = sig
            continue

        if sig == last_sig:
            stable += 1
        else:
            stable = 0

        last_sig = sig

        if stable >= 2:
            print("[stable] OK - Inhalte sind stabil")
            return snap

    print("[stable] WARNUNG - Timeout, nutze letzten Snapshot")
    return last_snapshot


def ensure_target_week(page, expected_date):
    candidates = _current_week_value_candidates(expected_date)
    print(f"[kw] Zielwoche fuer {expected_date}: {candidates}")

    before = _get_menu_snapshot(page)
    week_buttons = _get_week_buttons(page)

    active = before.get("activeWeekValue", "")
    if active in candidates:
        print(f"[kw] Zielwoche bereits aktiv: {active}")
        return True

    if not week_buttons:
        print("[kw] Keine sichtbaren Wochenbuttons gefunden -> kein harter Fehler, Fallback moeglich")
        return False

    if not click_week_button(page, expected_date):
        print("[kw] WARNUNG: KW-Button konnte nicht geklickt werden")
        return False

    _wait_for_stable_menu(
        page,
        expected_week_values=candidates,
        before_snapshot=before,
        require_change=False,
        max_wait_ms=10000,
        interval_ms=500,
    )
    _dump_debug(page, f"kw-{expected_date.isocalendar()[1]:02d}")
    return True


# ── Netzwerk-/JSON-/HTML-Fallback ─────────────────────────────────────────────
MEAL_NAME_KEYS = [
    "name", "mealName", "meal_name", "title", "description", "desc", "label",
    "text", "bezeichnung", "gericht", "speise"
]
CATEGORY_KEYS = [
    "category", "categoryName", "category_name", "line", "station",
    "group", "type", "bereich", "kategorie"
]
PRICE_KEYS = [
    "price", "preis", "amount", "employeePrice", "employee_price",
    "priceEmployee", "price_value", "value"
]
FEATURE_KEYS = [
    "features", "icons", "tags", "attributes"
]


def _node_mentions_target(node, target_date):
    target_iso = target_date.strftime("%Y-%m-%d")
    target_dm = target_date.strftime("%d.%m.")
    target_dm2 = target_date.strftime("%d.%m")
    day_short = GERMAN_DAY_SHORT[target_date.weekday()].lower()
    day_long = GERMAN_DAY_LONG[target_date.weekday()].lower()

    def check_val(v):
        if v is None:
            return False
        s = str(v)
        low = s.lower()
        if target_iso in s:
            return True
        if target_dm in s or target_dm2 in s:
            return True
        if day_short in low and (target_dm in s or target_dm2 in s):
            return True
        if day_long in low and (target_dm in s or target_dm2 in s):
            return True
        return False

    if isinstance(node, dict):
        return any(check_val(v) for v in node.values())
    if isinstance(node, list):
        return any(check_val(v) for v in node[:20])
    return check_val(node)


def _first_key_value(d, keys):
    for k in keys:
        if k in d and d[k] not in [None, ""]:
            return d[k]
    for key, value in d.items():
        if isinstance(key, str):
            low = key.lower()
            for k in keys:
                if k.lower() == low and value not in [None, ""]:
                    return value
    return None


def _stringify_price(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        if value > 0:
            return f"{value:.2f}".replace(".", ",") + " €"
        return ""
    s = str(value).strip()
    if not s:
        return ""
    m = re.search(r"(\d{1,2},\d{2}\s*€)", s)
    if m:
        return m.group(1)
    m = re.search(r"(\d{1,2}\.\d{2})", s)
    if m:
        return m.group(1).replace(".", ",") + " €"
    return s


def _extract_meal_from_node(node):
    if not isinstance(node, dict):
        return None

    name = _first_key_value(node, MEAL_NAME_KEYS)
    cat = _first_key_value(node, CATEGORY_KEYS)
    price = _first_key_value(node, PRICE_KEYS)
    features = _first_key_value(node, FEATURE_KEYS)

    if not name:
        return None

    name_s = str(name).strip()
    if len(name_s) < 3:
        return None

    cat_s = str(cat).strip() if cat else ""
    price_s = _stringify_price(price)

    feat_list = []
    if isinstance(features, list):
        feat_list = [str(x).lower() for x in features]
    elif isinstance(features, str):
        feat_list = [features.lower()]

    vv = _detect_vv(name_s, feat_list)

    return {
        "kategorie": cat_s or "",
        "name": name_s,
        "preis": price_s,
        "vv": vv,
    }


def _dedupe_dishes(dishes):
    out = []
    seen = set()
    for d in dishes:
        key = (d.get("kategorie", "").strip(), d.get("name", "").strip(), d.get("preis", "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _extract_dishes_from_json_payload(text, target_date):
    try:
        data = json.loads(text)
    except Exception:
        return []

    dishes = []

    def walk(node, ancestor_target=False):
        current_target = ancestor_target or _node_mentions_target(node, target_date)

        if isinstance(node, dict):
            meal = _extract_meal_from_node(node)
            if meal and (current_target or meal["kategorie"] in _category_candidates()):
                dishes.append(meal)
            for v in node.values():
                walk(v, current_target)
        elif isinstance(node, list):
            for item in node:
                walk(item, current_target)

    walk(data, False)
    dishes = _dedupe_dishes(dishes)

    # Wenn kaum Daten und Kategorien fehlen, unbrauchbar
    useful = [d for d in dishes if d.get("name")]
    if len(useful) >= 3:
        print(f"[net-json] {len(useful)} Gerichte aus JSON extrahiert")
        return useful
    return []


def _extract_dishes_from_html_text(text, target_date, strict=True):
    target_iso = target_date.strftime("%Y-%m-%d")
    target_dm = target_date.strftime("%d.%m.")
    target_dm2 = target_date.strftime("%d.%m")
    target_day = _weekday_token_for_button(target_date)

    if strict:
        low = text.lower()
        has_target = (
            target_iso in text or
            target_dm in text or
            target_dm2 in text or
            (target_day.lower() in low and (target_dm in text or target_dm2 in text))
        )
        if not has_target:
            return []

    pattern = re.compile(
        r'<div class="meal-wrapper">.*?<div class="categoryName">(.*?)</div>.*?<div class="mealNameWrapper[^"]*">(.*?)</div>.*?(?:<div class="price-value[^"]*">(.*?)</div>)?',
        re.S | re.I,
    )

    dishes = []
    for cat, name, price in pattern.findall(text):
        cat_s = _strip_tags(cat)
        name_s = _strip_tags(name)
        price_s = _strip_tags(price)

        if not name_s:
            continue

        vv = _detect_vv(name_s, [])
        dishes.append({
            "kategorie": cat_s,
            "name": name_s,
            "preis": price_s,
            "vv": vv,
        })

    dishes = _dedupe_dishes(dishes)
    if len(dishes) >= 3:
        print(f"[net-html] {len(dishes)} Gerichte aus HTML extrahiert")
        return dishes
    return []


def _network_fallback(page, capture, target_date):
    capture.dump(f"{LOCATION_NAME}_responses")
    candidates = capture.candidate_entries()

    print(f"[net] Kandidatenanzahl: {len(candidates)}")
    for entry in candidates[:15]:
        print(f"[net] score={entry['score']:02d} type={entry['resource_type']!r} ct={entry['content_type']!r} url={entry['url'][:140]}")

    for entry in candidates:
        body = entry.get("body", "")
        if not body:
            continue

        dishes = _extract_dishes_from_json_payload(body, target_date)
        if dishes:
            _save_text_dump(f"{LOCATION_NAME}_net_hit_url", entry["url"])
            return dishes

        dishes = _extract_dishes_from_html_text(body, target_date, strict=True)
        if dishes:
            _save_text_dump(f"{LOCATION_NAME}_net_hit_url", entry["url"])
            return dishes

    # lockerer zweiter Durchlauf
    for entry in candidates[:10]:
        body = entry.get("body", "")
        if not body:
            continue
        dishes = _extract_dishes_from_html_text(body, target_date, strict=False)
        if dishes:
            print("[net] HTML-Fallback im lockeren Modus erfolgreich")
            _save_text_dump(f"{LOCATION_NAME}_net_hit_url_relaxed", entry["url"])
            return dishes

    # Als letzte Netz-Hilfe aktuelle page.content() untersuchen
    try:
        html = page.content()
        dishes = _extract_dishes_from_html_text(html, target_date, strict=True)
        if dishes:
            print("[net] page.content()-Extraktion erfolgreich")
            return dishes
    except Exception as e:
        print(f"[net] page.content()-Extraktion Fehler: {e}")

    return []


# ── PDF-Fallback ───────────────────────────────────────────────────────────────
def _http_get_bytes(url):
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def _decode_pdf_literal_string(s):
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue

        i += 1
        if i >= len(s):
            break
        esc = s[i]

        if esc == "n":
            out.append("\n")
        elif esc == "r":
            out.append("\r")
        elif esc == "t":
            out.append("\t")
        elif esc == "b":
            out.append("\b")
        elif esc == "f":
            out.append("\f")
        elif esc in ["\\", "(", ")"]:
            out.append(esc)
        elif esc in "\n\r":
            pass
        elif esc.isdigit():
            oct_digits = esc
            for _ in range(2):
                if i + 1 < len(s) and s[i + 1].isdigit():
                    i += 1
                    oct_digits += s[i]
                else:
                    break
            try:
                out.append(chr(int(oct_digits, 8)))
            except Exception:
                out.append(oct_digits)
        else:
            out.append(esc)
        i += 1
    return "".join(out)


def _extract_pdf_text_with_library(pdf_bytes):
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            print("[pdf] Text via pypdf extrahiert")
            return text
    except Exception as e:
        print(f"[pdf] pypdf nicht nutzbar: {e}")

    try:
        from PyPDF2 import PdfReader  # type: ignore
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            print("[pdf] Text via PyPDF2 extrahiert")
            return text
    except Exception as e:
        print(f"[pdf] PyPDF2 nicht nutzbar: {e}")

    return ""


def _extract_pdf_text_with_pdftotext(pdf_bytes):
    exe = shutil.which("pdftotext")
    if not exe:
        print("[pdf] pdftotext nicht installiert")
        return ""

    try:
        with tempfile.TemporaryDirectory() as td:
            pdf_path = Path(td) / "week.pdf"
            txt_path = Path(td) / "week.txt"
            pdf_path.write_bytes(pdf_bytes)

            cmd = [exe, "-layout", str(pdf_path), str(txt_path)]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if res.returncode != 0:
                print(f"[pdf] pdftotext Fehler rc={res.returncode}: {res.stderr[:400]}")
                return ""

            if txt_path.exists():
                text = txt_path.read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    print("[pdf] Text via pdftotext extrahiert")
                    return text
    except Exception as e:
        print(f"[pdf] pdftotext Ausnahme: {e}")

    return ""


def _iter_pdf_stream_texts(pdf_bytes):
    streams = re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, flags=re.S)
    for raw in streams:
        tried = []

        for wbits in [zlib.MAX_WBITS, -zlib.MAX_WBITS, zlib.MAX_WBITS | 32]:
            try:
                data = zlib.decompress(raw, wbits)
                tried.append(data)
            except Exception:
                pass

        if not tried:
            continue

        for data in tried:
            for enc in ["utf-8", "latin1"]:
                try:
                    yield data.decode(enc, errors="ignore")
                    break
                except Exception:
                    continue


def _extract_pdf_text_basic(pdf_bytes):
    chunks = list(_iter_pdf_stream_texts(pdf_bytes))
    if not chunks:
        return ""

    texts = []
    string_token_re = re.compile(r"\((?:\\.|[^\\()])*\)")
    bt_block_re = re.compile(r"BT(.*?)ET", re.S)

    for chunk in chunks:
        blocks = bt_block_re.findall(chunk) or [chunk]
        for block in blocks:
            parts = []
            for token in string_token_re.findall(block):
                literal = token[1:-1]
                decoded = _decode_pdf_literal_string(literal)
                if decoded and decoded.strip():
                    parts.append(decoded)
            if parts:
                texts.append("\n".join(parts))

    if texts:
        print("[pdf] Text via eingebautem Stream-Extractor extrahiert")
        return "\n".join(texts)

    printable = []
    for chunk in chunks:
        cleaned = re.sub(r"[^ -~\n\r\täöüÄÖÜß€]", " ", chunk)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        if cleaned.strip():
            printable.append(cleaned)

    if printable:
        print("[pdf] Text via eingebautem Printable-Fallback extrahiert")
        return "\n".join(printable)

    return ""


def _normalize_pdf_text(text):
    text = text.replace("\x00", " ")
    text = unescape(text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)

    for token in GERMAN_DAY_LONG + GERMAN_DAY_SHORT[:5]:
        text = re.sub(
            rf"(?<!\n)\b({re.escape(token)}\s+\d{{2}}\.\d{{2}}\.?)",
            r"\n\1",
            text,
            flags=re.I,
        )

    for cat in sorted(_category_candidates(), key=len, reverse=True):
        text = re.sub(
            rf"(?<!\n)({re.escape(cat)})",
            r"\n\1",
            text,
            flags=re.I,
        )

    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _pdf_day_section(pdf_text, target_date):
    text = _normalize_pdf_text(pdf_text)
    target_day_short = _weekday_token_for_button(target_date)
    target_day_long = GERMAN_DAY_LONG[target_date.weekday()]
    target_date_token = _normalize_day_date(_date_token_for_button(target_date))
    monday = target_date - timedelta(days=target_date.weekday())
    week_dates = [_normalize_day_date((monday + timedelta(i)).strftime("%d.%m.")) for i in range(5)]

    marker_re = re.compile(
        r"\b(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Mo|Di|Mi|Do|Fr)\s+(\d{2}\.\d{2}\.?)",
        re.I,
    )
    matches = list(marker_re.finditer(text))
    if matches:
        target_idx = None
        for i, m in enumerate(matches):
            day = m.group(1)
            dat = _normalize_day_date(m.group(2))
            if dat == target_date_token and day.lower() in [target_day_short.lower(), target_day_long.lower()]:
                target_idx = i
                break
        if target_idx is None:
            for i, m in enumerate(matches):
                dat = _normalize_day_date(m.group(2))
                if dat == target_date_token:
                    target_idx = i
                    break
        if target_idx is not None:
            start = matches[target_idx].start()
            end = matches[target_idx + 1].start() if target_idx + 1 < len(matches) else len(text)
            section = text[start:end].strip()
            print(f"[pdf] Tagessektion via Marker gefunden: {section[:300]!r}")
            return section

    date_only_re = re.compile(r"\b(\d{2}\.\d{2}\.?)\b")
    date_matches = list(date_only_re.finditer(text))
    target_pos = None
    next_pos = None

    for i, m in enumerate(date_matches):
        dat = _normalize_day_date(m.group(1))
        if dat == target_date_token:
            target_pos = m.start()
            for j in range(i + 1, len(date_matches)):
                nxt = _normalize_day_date(date_matches[j].group(1))
                if nxt in week_dates:
                    next_pos = date_matches[j].start()
                    break
            break

    if target_pos is not None:
        section = text[target_pos:next_pos].strip() if next_pos else text[target_pos:].strip()
        print(f"[pdf] Tagessektion via Datumsmarker gefunden: {section[:300]!r}")
        return section

    print("[pdf] Keine Tagesmarker im PDF-Text gefunden")
    return text


def _extract_name_price_from_segment(seg):
    seg = unescape(seg)
    seg = seg.replace("\n", " ")
    seg = re.sub(r"[ \t]+", " ", seg).strip(" -|:")

    explicit_price = re.findall(r"Preis\s*[: ]*\s*(\d{1,2},\d{2}\s*€)", seg, flags=re.I)
    if explicit_price:
        price = explicit_price[-1]
        idx = seg.rfind(price)
    else:
        all_prices = re.findall(r"(\d{1,2},\d{2}\s*€)", seg)
        price = all_prices[-1] if all_prices else ""
        idx = seg.rfind(price) if price else -1

    if price and idx >= 0:
        name = seg[:idx].strip(" -|:")
    else:
        name = seg

    name = re.sub(r"\bPreis\b.*$", "", name, flags=re.I).strip(" -|:")
    name = re.sub(r"\bExtern\b.*$", "", name, flags=re.I).strip(" -|:")
    name = re.sub(r"[ \t]+", " ", name).strip()
    return name, price


def _parse_pdf_day_section_by_categories(section_text):
    text = _normalize_pdf_text(section_text)
    cats = _category_candidates()
    pattern = re.compile("|".join(re.escape(c) for c in sorted(cats, key=len, reverse=True)))
    matches = list(pattern.finditer(text))

    dishes = []
    if not matches:
        return dishes

    for i, m in enumerate(matches):
        cat = m.group(0)
        seg_start = m.end()
        seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        seg = text[seg_start:seg_end].strip()
        if not seg:
            continue

        name, price = _extract_name_price_from_segment(seg)
        if not name:
            continue

        vv = _detect_vv(name, [])
        dishes.append({
            "kategorie": cat,
            "name": name,
            "preis": price,
            "vv": vv,
        })

    return dishes


def _parse_pdf_day_section_linewise(section_text):
    text = _normalize_pdf_text(section_text)
    lines = [re.sub(r"[ \t]+", " ", x).strip() for x in text.splitlines()]
    lines = [x for x in lines if x]

    categories = sorted(_category_candidates(), key=len, reverse=True)
    dishes = []
    current_cat = None
    current_parts = []

    def flush():
        nonlocal current_cat, current_parts, dishes
        if current_cat and current_parts:
            seg = " ".join(current_parts).strip()
            name, price = _extract_name_price_from_segment(seg)
            if name:
                vv = _detect_vv(name, [])
                dishes.append({
                    "kategorie": current_cat,
                    "name": name,
                    "preis": price,
                    "vv": vv,
                })
        current_cat = None
        current_parts = []

    day_marker_re = re.compile(r"\b(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Mo|Di|Mi|Do|Fr)\s+\d{2}\.\d{2}\.?", re.I)

    for line in lines:
        if day_marker_re.search(line):
            continue
        if re.search(r"\b(KW\s*\d+|Woche|Filter)\b", line, flags=re.I):
            continue

        cat_match = next((c for c in categories if line.startswith(c)), None)
        if cat_match:
            flush()
            current_cat = cat_match
            rest = line[len(cat_match):].strip(" -|:")
            if rest:
                current_parts.append(rest)
            continue

        if current_cat is not None:
            current_parts.append(line)
            if re.search(r"\d{1,2},\d{2}\s*€", line):
                flush()

    flush()
    return dishes


def _pdf_fallback_day(page, target_date, pdf_cache=None):
    pdf_cache = pdf_cache if pdf_cache is not None else {}

    pdf_url = _extract_pdf_link(page)
    if not pdf_url:
        print("[pdf] Kein PDF-Link vorhanden -> Fallback nicht moeglich")
        return []

    if pdf_url in pdf_cache:
        pdf_text = pdf_cache[pdf_url]
    else:
        try:
            pdf_bytes = _http_get_bytes(pdf_url)
            print(f"[pdf] Download OK: {pdf_url} ({len(pdf_bytes)} bytes)")
        except Exception as e:
            print(f"[pdf] Download-Fehler: {e}")
            return []

        pdf_text = _extract_pdf_text_with_library(pdf_bytes)
        if not pdf_text.strip():
            pdf_text = _extract_pdf_text_with_pdftotext(pdf_bytes)
        if not pdf_text.strip():
            pdf_text = _extract_pdf_text_basic(pdf_bytes)

        if not pdf_text.strip():
            print("[pdf] Konnte keinen Text aus PDF extrahieren")
            return []

        pdf_cache[pdf_url] = pdf_text
        _save_text_dump(f"{LOCATION_NAME}_pdf_raw", pdf_text[:30000])

    section = _pdf_day_section(pdf_text, target_date)
    _save_text_dump(f"{LOCATION_NAME}_{target_date.isoformat()}_pdf_section", section[:20000])

    dishes = _parse_pdf_day_section_by_categories(section)
    if not dishes:
        dishes = _parse_pdf_day_section_linewise(section)

    print(f"[pdf] Parsed dishes: {len(dishes)}")
    for d in dishes[:10]:
        print(f"[pdf] {d['kategorie']}: {d['name']} | {d['preis']}")

    return dishes


def scrape_day(page, date_obj):
    date_label = date_obj.strftime("%d.%m.")
    print(f"\n[scrape] {date_label}")

    before = _get_menu_snapshot(page)
    print(
        f"[before] week={before.get('activeWeekValue')!r} "
        f"day={before.get('activeDayName')!r} {before.get('activeDayDate')!r} "
        f"select={before.get('selectValue')!r} "
        f"first={before.get('firstMeals')}"
    )

    if _current_view_matches_target(page, date_obj):
        print("[scrape] Zieltag scheint bereits aktiv zu sein -> parse aktuelle Ansicht")
        for attempt in range(1, 3):
            try:
                raw_json = page.evaluate(JS_EXTRACT)
                if raw_json and raw_json not in ("[]", "null", ""):
                    dishes = parse_eurest_dom(raw_json)
                    print(f"[dom-current] {len(dishes)} Gerichte")
                    print(f"[dom-current] erste Gerichte: {[d['name'] for d in dishes[:3]]}")
                    return dishes
            except Exception as e:
                print(f"[dom-current] Fehler (Versuch {attempt}/2): {e}")
            page.wait_for_timeout(800)

    clicked = False
    used_method = None

    clicked = click_day_candidate(page, date_obj)
    if clicked:
        used_method = "candidate"

    if not clicked:
        print("[scrape] Kandidaten-Klick nicht erfolgreich -> Fallback select.menuDaySelect")
        clicked = click_day_select(page, date_obj)
        if clicked:
            used_method = "select"

    if not clicked:
        print(f"[scrape] Konnte Zieltag {date_obj} weder per Kandidatenklick noch per select waehlen")
        return []

    expected_day = _date_token_for_button(date_obj)
    expected_select_prefix = date_obj.strftime("%Y-%m-%d") if used_method == "select" else None

    _wait_for_stable_menu(
        page,
        expected_day_date=expected_day,
        expected_select_prefix=expected_select_prefix,
        before_snapshot=before,
        require_change=False,
        max_wait_ms=10000,
        interval_ms=500,
    )

    for sel in [".menuListWrapper", ".meal-wrapper", ".mealNameWrapper"]:
        try:
            page.wait_for_selector(sel, timeout=8000)
            break
        except Exception:
            pass

    _dump_debug(page, f"scrape-{date_label}")

    for attempt in range(1, 3):
        try:
            raw_json = page.evaluate(JS_EXTRACT)
            if raw_json and raw_json not in ("[]", "null", ""):
                dishes = parse_eurest_dom(raw_json)
                print(f"[dom] {len(dishes)} Gerichte")
                print(f"[dom] erste Gerichte: {[d['name'] for d in dishes[:3]]}")
                return dishes

            print(f"[dom] Leer (Versuch {attempt}/2)")
        except Exception as e:
            print(f"[dom] Fehler (Versuch {attempt}/2): {e}")

        page.wait_for_timeout(1000)

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


# ── Render Tagesansicht ────────────────────────────────────────────────────────
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
    day_str = f"{GERMAN_DAY_LONG[target_date.weekday()]}, {target_date.strftime('%d.%m.%Y')}"
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
        uni_size = _find_uniform_font_size(d, all_names, name_max_w, name_max_h, size_start=18, size_min=9)
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
                d.text((cx1 - (pb[2] - pb[0]) - PAD, cy1 - (pb[3] - pb[1]) - PAD), it["preis"], font=fprc, fill=LIGHT)

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
    d.text(((W - (b[2] - b[0])) // 2, H - FOOTER_H + (FOOTER_H - (b[3] - b[1])) // 2), footer_txt, font=fftr, fill=WHITE)

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
        col = (220, 150, 0) if is_today else (C_HOL_HDR if is_hol else ((160, 160, 160) if is_past else LIGHT))
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

            bg = (235, 235, 235) if is_past else (
                C_HOL_BG if is_hol else ((255, 253, 230) if is_today else (R_ODD if ri % 2 == 0 else R_EVEN))
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
    d.text(((W - (b[2] - b[0])) // 2, H - FOOTER_H + (FOOTER_H - (b[3] - b[1])) // 2), footer_txt, font=fftr, fill=WHITE)

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

    pdf_cache = {}

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

                capture = ResponseCapture(target_date)
                capture.attach(page)

                setup_eurest(page)
                ensure_target_week(page, target_date)

                day_candidates = _get_day_candidates(page)
                print(f"[main] gefundene dayCandidates: {len(day_candidates)}")

                target_visible_in_candidates = _target_day_present_in_candidates(page, target_date)
                available_values = _get_available_dates(page)
                target_str = target_date.strftime("%Y-%m-%d")
                target_visible_in_select = any(v.startswith(target_str) for v in available_values)

                print(f"[main] target_visible_in_candidates={target_visible_in_candidates}")
                print(f"[main] target_visible_in_select={target_visible_in_select}")
                print(f"[main] current_view_matches_target={_current_view_matches_target(page, target_date)}")

                dishes = scrape_day(page, target_date)

                if not dishes:
                    print("[main] DOM-Scrape fehlgeschlagen -> Netzwerk/JSON/HTML-Fallback")
                    dishes = _network_fallback(page, capture, target_date)

                if not dishes:
                    print("[main] Netzwerk-Fallback fehlgeschlagen -> PDF-Fallback")
                    dishes = _pdf_fallback_day(page, target_date, pdf_cache)

                if not dishes:
                    capture.dump(f"{LOCATION_NAME}_responses_fail")
                    _dump_debug(page, "day-failed")
                    _save_debug_dump(page, "day-failed")
                    browser.close()
                    print("FEHLER: Zieltag konnte weder per DOM noch per Netzwerk/JSON noch per PDF-Fallback geladen werden.")
                    print("FEHLER: Abbruch, damit latest-Bild NICHT ueberschrieben wird.")
                    sys.exit(1)

                browser.close()

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

            capture = ResponseCapture(target_monday)
            capture.attach(page)

            setup_eurest(page)
            ensure_target_week(page, target_monday)

            for date_obj in scrape_dates:
                dk = day_key(date_obj)
                print(f"[week] scrape {dk}")

                d2 = scrape_day(page, date_obj)
                if not d2:
                    print(f"[week] DOM-Scrape leer fuer {dk} -> Netzwerk-Fallback")
                    d2 = _network_fallback(page, capture, date_obj)
                if not d2:
                    print(f"[week] Netzwerk-Fallback leer fuer {dk} -> PDF-Fallback")
                    d2 = _pdf_fallback_day(page, date_obj, pdf_cache)

                if d2:
                    week_data[dk] = d2

            if not week_data:
                capture.dump(f"{LOCATION_NAME}_responses_week_fail")
                _dump_debug(page, "week-failed")
                _save_debug_dump(page, "week-failed")

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
