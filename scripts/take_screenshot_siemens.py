#!/usr/bin/env python3
"""
Siemens Kantine Regensburg – merged variant for the common photoframe repo.

Features:
- Tagesansicht (`DISPLAY_MODE=day`) als Default
- Wochenansicht (`DISPLAY_MODE=week`) optional
- Siemens-Farbwelt: Petrol, Schwarz, Weiß, Healthy Orange
- Feste Kategorien: Suppe, Essen 1, Essen 2, Essen 3, Aktion
- Ausgabe kompatibel zu Schaeffler/Aumovio in `docs/images`

Environment variables:
  CATERINGPORTAL_URL   default: https://siemens.cateringportal.io/menu/Regensburg/Mittagessen
  CATERINGPORTAL_SID   optional session id appended as `?ste_sid=...`
  WEEK_OFFSET          0=current week, 1=next week (relevant for week mode)
  DISPLAY_MODE         day (default) | week
  DISPLAY_DAY          monday|tuesday|wednesday|thursday|friday
                       leer = Automatik: vor 13:00 heute, ab 13:00 nächster Werktag
"""

import os
import re
import sys
import json
import shutil
from pathlib import Path
from datetime import date, datetime, timezone, timedelta

from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont

# ── Config ────────────────────────────────────────────────────────────────────
URL_BASE = os.environ.get(
    "CATERINGPORTAL_URL",
    "https://siemens.cateringportal.io/menu/Regensburg/Mittagessen",
)
_SID = os.environ.get("CATERINGPORTAL_SID", "").strip()
WEEK_OFFSET = int(os.environ.get("WEEK_OFFSET", "1"))
DISPLAY_MODE = os.environ.get("DISPLAY_MODE", "day").strip().lower()
DISPLAY_DAY = os.environ.get("DISPLAY_DAY", "").strip().lower()

SWITCH_HOUR_LOCAL = 13
SWITCH_MINUTE_LOCAL = 00

LOCATION_NAME = "siemens"
LOCATION_LABEL = "SIEMENS Regensburg"

OUT_DIR = Path("docs/images")
OUT_DIR.mkdir(parents=True, exist_ok=True)
MAX_KEEP = 5
W, H = 800, 600
FOOTER_H = 20

GERMAN_DAY_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
GERMAN_DAY_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
DAY_NAME_MAP = {
    "monday": 0, "montag": 0, "mo": 0,
    "tuesday": 1, "dienstag": 1, "di": 1,
    "wednesday": 2, "mittwoch": 2, "mi": 2,
    "thursday": 3, "donnerstag": 3, "do": 3,
    "friday": 4, "freitag": 4, "fr": 4,
}

# ── Theme ─────────────────────────────────────────────────────────────────────
def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


THEME = {
    "PRIMARY": hex_rgb("#009999"),        # Siemens Petrol
    "SECONDARY": hex_rgb("#000000"),      # Schwarz
    "BACKGROUND": hex_rgb("#FFFFFF"),     # Weiß
    "ROW_ODD": hex_rgb("#F2FAFA"),
    "ROW_EVEN": hex_rgb("#FFFFFF"),
    "TEXT": hex_rgb("#000000"),
    "TEXT_MUTED": hex_rgb("#666666"),
    "TEXT_ON_PRIMARY": hex_rgb("#FFFFFF"),
    "TEXT_ON_SECONDARY": hex_rgb("#FFFFFF"),
    "SUBTITLE_ON_PRIMARY": hex_rgb("#D9F2F2"),
    "PRICE": hex_rgb("#EC6602"),          # Healthy Orange
    "GRID": hex_rgb("#C7E5E5"),
    "HOLIDAY_BG": hex_rgb("#F1F1F1"),
    "HOLIDAY_HDR": hex_rgb("#7A7A7A"),
    "HOLIDAY_TXT": hex_rgb("#333333"),
    "VEGAN": hex_rgb("#009999"),
    "VEGETARIAN": hex_rgb("#EC6602"),
    "TODAY": (255, 200, 0),
}

PRIMARY = THEME["PRIMARY"]
SECONDARY = THEME["SECONDARY"]
BG = THEME["BACKGROUND"]
R_ODD = THEME["ROW_ODD"]
R_EVEN = THEME["ROW_EVEN"]
TEXT = THEME["TEXT"]
TEXT_MUTED = THEME["TEXT_MUTED"]
TEXT_ON_PRIMARY = THEME["TEXT_ON_PRIMARY"]
TEXT_ON_SECONDARY = THEME["TEXT_ON_SECONDARY"]
SUBTITLE_ON_PRIMARY = THEME["SUBTITLE_ON_PRIMARY"]
PRICE = THEME["PRICE"]
GRID = THEME["GRID"]
C_HOL_BG = THEME["HOLIDAY_BG"]
C_HOL_HDR = THEME["HOLIDAY_HDR"]
C_HOL_TXT = THEME["HOLIDAY_TXT"]
C_VG = THEME["VEGAN"]
C_V = THEME["VEGETARIAN"]
C_TODAY = THEME["TODAY"]
WHITE = (255, 255, 255)

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


# ── Bavarian holidays ─────────────────────────────────────────────────────────
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
        date(y, 1, 6): "Hl. Drei Könige",
        e - timedelta(2): "Karfreitag",
        e: "Ostersonntag",
        e + timedelta(1): "Ostermontag",
        date(y, 5, 1): "Tag der Arbeit",
        e + timedelta(39): "Christi Himmelfahrt",
        e + timedelta(49): "Pfingstsonntag",
        e + timedelta(50): "Pfingstmontag",
        e + timedelta(60): "Fronleichnam",
        date(y, 8, 15): "Mariä Himmelfahrt",
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
        f"{short[i]} {(monday_date + timedelta(i)).strftime('%d.%m')}":
        hols.get(monday_date + timedelta(i)) for i in range(5)
    }


# ── Time helpers ──────────────────────────────────────────────────────────────
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
        local_dt.hour > SWITCH_HOUR_LOCAL or
        (local_dt.hour == SWITCH_HOUR_LOCAL and local_dt.minute >= SWITCH_MINUTE_LOCAL)
    )
    candidate = today + timedelta(days=1) if after_switch else today

    for _ in range(14):
        if candidate.weekday() < 5 and candidate not in holidays_all:
            break
        candidate += timedelta(days=1)

    print(f"[target] after_switch={after_switch}, today={today} -> Ziel: {candidate}")
    return candidate


# ── localStorage helpers ──────────────────────────────────────────────────────
_LANG_STORAGE = {}


def _snap(page):
    try:
        raw = page.evaluate(
            "()=>{var o={};for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);o[k]=localStorage.getItem(k);}return JSON.stringify(o);}"
        )
        return json.loads(raw)
    except Exception:
        return {}


def _inject(page):
    if not _LANG_STORAGE:
        return
    try:
        page.evaluate(
            "(p)=>{p.forEach(function(x){localStorage.setItem(x[0],x[1]);})}",
            list(_LANG_STORAGE.items()),
        )
        print(f"    [lang] re-injected {len(_LANG_STORAGE)} localStorage keys")
    except Exception as e:
        print(f"    [lang] inject error: {e}")


# ── Cookie banner ─────────────────────────────────────────────────────────────
def dismiss_cookie(page):
    for sel in [
        "button:has-text('Got it')",
        "text=Got it!",
        "button:has-text('Accept')",
        "button:has-text('Akzeptieren')",
        "button:has-text('Alle akzeptieren')",
        "[id*='accept' i]",
        "[class*='accept' i] button",
        "[id*='cookie' i] button",
        "[class*='cookie' i] button",
    ]:
        try:
            page.click(sel, timeout=2000)
            print(f"  [cookie] dismissed via {sel!r}")
            page.wait_for_timeout(500)
            return True
        except Exception:
            pass
    print("  [cookie] no banner found (OK)")
    return False


# ── Language switch ───────────────────────────────────────────────────────────
def switch_to_german(page):
    global _LANG_STORAGE
    before = _snap(page)

    TRIGGER_SELS = [
        "button[aria-label='language']",
        "button[aria-label='Language']",
        "button[aria-label*='language' i]",
        "button[aria-label*='sprache' i]",
        "button:has(img[src*='flags/'])",
        "button:has(img[src*='en-US'])",
        "button:has(img[alt='English'])",
        "button:has(img[title='English'])",
        "button:has-text('English')",
        "button:has-text('EN')",
        "mat-select[aria-label*='lang' i]",
    ]
    DE_SELS = [
        "button[aria-label='Deutsch']",
        "button[lang='de-DE']",
        "button[value='de-DE']",
        "[role='menuitem'][aria-label='Deutsch']",
        "[role='menuitem'][lang='de-DE']",
    ]

    def _wait_for_menu_and_click_de():
        overlay_sels = [".mat-mdc-menu-panel", ".mat-menu-panel", "[class*='mat-menu']", "div[role='menu']"]
        overlay_found = False
        for osel in overlay_sels:
            try:
                page.wait_for_selector(osel, timeout=3000, state="attached")
                print(f"  [lang] mat-menu overlay found via {osel!r}")
                overlay_found = True
                break
            except Exception:
                pass
        if not overlay_found:
            print("  [lang] mat-menu overlay not detected, trying DE button anyway")

        for sel in DE_SELS:
            try:
                el = page.wait_for_selector(sel, timeout=3000, state="visible")
                if el:
                    el.click()
                    print(f"  [lang] ✓ clicked DE button via {sel!r}")
                    return True
            except Exception:
                pass

        print("  [lang] DE selectors failed, trying JS click on de-DE flag img...")
        result = page.evaluate(
            r"""
            (function(){
              var img=document.querySelector("img[src*='de-DE'],img[alt='Deutsch'],img[title='Deutsch']");
              if(!img)return 'de-DE img not found';
              var btn=img.closest('button,[role="menuitem"],[role="option"],a')||img.parentElement;
              btn.click();
              return 'JS-clicked:'+btn.tagName+'|'+(img.src||'');
            })()
            """
        )
        print(f"  [lang] JS fallback result: {result}")
        return "not found" not in result

    switched = False

    for sel in DE_SELS:
        try:
            el = page.wait_for_selector(sel, timeout=800, state="visible")
            if el:
                el.click()
                print(f"  [lang] ✓ DE button already visible, clicked via {sel!r}")
                switched = True
                break
        except Exception:
            pass

    if not switched:
        print("  [lang] DE button not directly visible – opening language trigger...")
        trigger_opened = False
        for tsel in TRIGGER_SELS:
            try:
                page.click(tsel, timeout=2500)
                print(f"  [lang] ✓ trigger clicked via {tsel!r}")
                page.wait_for_timeout(600)
                trigger_opened = True
                break
            except Exception:
                pass
        if not trigger_opened:
            print("  [lang] no trigger selector matched, trying JS flag-button click...")
            result = page.evaluate(
                r"""
                (function(){
                  var imgs=Array.from(document.querySelectorAll("img[src*='flags/']"));
                  for(var img of imgs){
                    var btn=img.closest('button,[role="button"]')||img.parentElement;
                    if(btn&&btn!==img){
                      btn.click();
                      return 'JS trigger clicked:'+btn.tagName+'|'+(img.src||'');
                    }
                  }
                  return 'no flags img found';
                })()
                """
            )
            print(f"  [lang] JS trigger fallback: {result}")
            page.wait_for_timeout(600)

        switched = _wait_for_menu_and_click_de()

    if not switched:
        print("  [lang] ✗ WARNING: could not switch to German – proceeding in English")
        return False

    page.wait_for_timeout(1000)
    confirmed = False
    for de_text_sel in [
        "text=Suppe / Vorspeise",
        "text=Suppe",
        "text=Essen 1",
        "h3.category-header",
        "text=Mittagessen",
    ]:
        try:
            page.wait_for_selector(de_text_sel, timeout=3500)
            print(f"  [lang] ✓ German UI confirmed via {de_text_sel!r}")
            confirmed = True
            break
        except Exception:
            pass
    if not confirmed:
        print("  [lang] German UI not yet confirmed (may appear after navigation)")

    after = _snap(page)
    diff = {k: v for k, v in after.items() if before.get(k) != v}
    if diff:
        _LANG_STORAGE = diff
        print(f"  [lang] localStorage diff captured: {dict(diff)}")
    else:
        _LANG_STORAGE = {k: "de-DE" for k in [
            "language", "locale", "lang", "i18n", "selectedLanguage", "appLanguage",
            "selectedLocale", "userLanguage", "NG_TRANSLATE_LANG_KEY",
        ]}
        _LANG_STORAGE.update({k: "de" for k in ["language", "locale", "lang"]})
        print(f"  [lang] no localStorage diff – injecting {len(_LANG_STORAGE)} fallback keys")

    return confirmed or switched


def warmup(page, base_url):
    print("[warmup] Loading base URL...")
    try:
        page.goto(base_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"[warmup] goto failed: {e}")
        return
    page.wait_for_timeout(1800)
    dismiss_cookie(page)
    switch_to_german(page)
    print(f"[warmup] done. _LANG_STORAGE has {len(_LANG_STORAGE)} keys")


# ── DOM extractor ─────────────────────────────────────────────────────────────
JS_EXTRACT = r"""
(function(){
  var panel=null;
  var panels=Array.from(document.querySelectorAll('mat-tab-nav-panel,[role="tabpanel"],mat-tab-body'));
  for(var p of panels){
    var s=window.getComputedStyle(p);
    if(s.display!=='none'&&s.visibility!=='hidden'&&p.offsetHeight>0){panel=p;break;}
  }
  if(!panel&&panels.length)panel=panels[0];
  var root=panel||document;
  var result=[];
  var categories=Array.from(root.querySelectorAll('app-category,.grid-row'));
  if(!categories.length)categories=Array.from(root.querySelectorAll('h3.category-header')).map(h=>h.parentElement);
  for(var cat of categories){
    var hdr=cat.querySelector('h3.category-header,.category-header,h3');
    var catName=hdr?hdr.textContent.trim():'';
    if(!catName)continue;
    var products=Array.from(cat.querySelectorAll('div.product-wrapper'));
    if(!products.length)products=Array.from(cat.querySelectorAll('[class*="product"]'));
    var catProducts=[];
    for(var prod of products){
      var nameEl=prod.querySelector('span.legacy-text-xxl,span.pre-wrap,button span,.name-column span');
      var rawName=nameEl?nameEl.textContent:'';
      var name=rawName.trim().replace(/\u00a0/g,' ');
      var priceEls=Array.from(prod.querySelectorAll('div.price,.price'));
      var intPrice='',extPrice='';
      for(var pe of priceEls){
        var pt=pe.textContent.replace(/\u00a0/g,' ').replace(/\s+/g,' ').trim();
        if(/^Int/i.test(pt))intPrice=pt.replace(/^Int\s*/i,'').trim();
        if(/^Ext/i.test(pt))extPrice=pt.replace(/^Ext\s*/i,'').trim();
      }
      catProducts.push({name:name,intPrice:intPrice,extPrice:extPrice});
    }
    result.push({category:catName,products:catProducts});
  }
  return JSON.stringify(result);
})()
"""

CAT_NORM_FIXED = {
    "suppe / vorspeise": "Suppe",
    "suppe/vorspeise": "Suppe",
    "suppe": "Suppe",
    "soup / starter": "Suppe",
    "soup/starter": "Suppe",
    "soup": "Suppe",
    "essen 1": "Essen 1",
    "food 1": "Essen 1",
    "gericht 1": "Essen 1",
    "essen 2": "Essen 2",
    "food 2": "Essen 2",
    "gericht 2": "Essen 2",
    "essen 3": "Essen 3",
    "food 3": "Essen 3",
    "gericht 3": "Essen 3",
}
CAT_FLEXIBLE = {
    "fisch": "Essen 3",
    "fish": "Essen 3",
    "vegan": "Essen 2",
    "vegane": "Essen 2",
    "vegetarisch": "Essen 2",
    "vegetarian": "Essen 2",
    "aktion": "Aktion",
}
_ESSEN_SLOTS = ["Essen 1", "Essen 2", "Essen 3", "Aktion"]
CATS = ["Suppe", "Essen 1", "Essen 2", "Essen 3", "Aktion"]


def norm_cat(raw, used_cats=None):
    key = raw.lower().strip()
    if key in CAT_NORM_FIXED:
        return CAT_NORM_FIXED[key]
    if key in CAT_FLEXIBLE:
        preferred = CAT_FLEXIBLE[key]
        if used_cats is None:
            return preferred
        try:
            start_idx = _ESSEN_SLOTS.index(preferred)
        except ValueError:
            start_idx = 0
        if preferred not in used_cats:
            return preferred
        for slot in _ESSEN_SLOTS[start_idx + 1:]:
            if slot not in used_cats:
                return slot
        for slot in _ESSEN_SLOTS[:start_idx]:
            if slot not in used_cats:
                return slot
        return preferred
    if used_cats is not None:
        for slot in _ESSEN_SLOTS:
            if slot not in used_cats:
                return slot
    return raw.strip()


def _norm_food_text(text):
    text = (text or "").lower()
    repl = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }
    for a, b in repl.items():
        text = text.replace(a, b)
    text = re.sub(r"[^a-z0-9\s/-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


EXPLICIT_VEGAN_KEYWORDS = {
    "vegan",
    "vegane",
    "veganer",
    "veganes",
    "plant based",
}

EXPLICIT_VEG_KEYWORDS = {
    "vegetarisch",
    "vegetarische",
    "vegetarischer",
    "vegetarisches",
    "vegetarian",
    "veggie",
}

NON_VEG_CATEGORY_KEYWORDS = {
    "fisch",
    "fish",
}

NON_VEG_KEYWORDS = {
    "fleisch", "hack", "hackfleisch", "faschiertes",
    "rind", "rinder", "rinderbraten", "rinderroulade", "rindersteak", "rindfleisch", "beef",
    "kalb", "kalbfleisch", "veal",
    "schwein", "schweine", "schweinefleisch", "schweinebauch", "schweinebraten",
    "schweineruecken", "schweinegeschnetzeltes", "geschnetzeltes", "spanferkel",
    "surbraten", "krustenbraten", "stelze", "haxe", "schaeuferl", "schaeufele",
    "braten", "gulasch", "goulash", "ragout", "ragu", "bolognese", "chili con carne",
    "cevapcici", "koefte", "kofte", "frikadelle", "bulette", "fleischpflanzerl", "pflanzerl",
    "leberkaese", "leberkase",
    "wurst", "bratwurst", "currywurst", "weisswurst", "bockwurst", "regensburger",
    "debreciner", "salami", "schinken", "kochschinken", "rohschinken", "speck", "bacon",
    "prosciutto", "jamon", "serrano", "chorizo", "mortadella", "pastrami", "salciccia",
    "pancetta", "guanciale", "krautwickel"
    "pute", "puten", "putengeschnetzeltes", "truthahn", "turkey",
    "huhn", "huehnchen", "haehnchen", "hendl", "chicken", "pollo", "poulet",
    "ente", "entenbrust", "gans", "gaensebraten", "gefluegel",
    "lamm", "lammragout", "lammkeule", "wild", "wildragout", "reh", "hirsch", "kaninchen",
    "scaloppine", "saltimbocca", "schnitzel", "cordon bleu", "cordonbleu",
    "piccata", "ossobuco", "osso buco", "steak", "rumpsteak", "minutensteak",
    "filet", "medaillons", "roastbeef", "hamburger",
    "fisch", "fish", "fischfilet", "seelachs", "seelachsfilet", "lachs", "lachsfilet",
    "forelle", "zander", "kabeljau", "dorsch", "rotbarsch", "pangasius",
    "thunfisch", "tunfisch", "matjes", "hering", "makrele", "sardine", "sardinen",
    "calamari", "tintenfisch", "pulpo", "oktopus", "garnele", "garnelen", "shrimp",
    "scampi", "muscheln", "miesmuscheln", "meerestiere", "meeresfruechte", "seafood",
    "paella valenciana", "paella marinera",
}

VEG_LIKELY_KEYWORDS = {
    "kaesespaetzle", "kasespaetzle", "kasspatzen", "kaspressknoedel", "spinatknoedel",
    "schlutzkrapfen", "rahmschwammerl", "pilzragout", "champignonrahm",
    "gemueselasagne", "gemuese-lasagne", "lasagne verdure", "verdure",
    "falafel", "tofu", "tempeh", "seitan", "veggie burger", "veggieburger",
    "quinoa", "couscous", "bulgur", "linsen", "linsencurry", "kichererbsen",
    "chana masala", "dal", "dhal", "palak paneer", "paneer", "saag paneer", "aloo gobi",
    "chili sin carne", "arrabbiata", "pasta napoli", "napoli", "pomodoro", "pesto",
    "aglio e olio", "gnocchi al pomodoro", "ravioli ricotta", "tortellini formaggio",
    "mozzarella", "caprese", "risotto ai funghi", "pilzrisotto", "gemuesecurry",
    "curry gemuese", "ratatouille", "gemuesepfanne", "wokgemuese",
    "brokkoli", "blumenkohl", "spinat", "mangold", "spargel", "kuerbis", "aubergine",
    "zucchini", "paprika", "kartoffelgratin", "ofenkartoffel", "grillgemuese",
    "kaiserschmarrn", "apfelstrudel", "germknoedel", "marillenknoedel", "topfenknoedel",
    "omelette", "omelett", "omelet", "eierspeise", "spiegelei", "ruehrei", "eiersalat",
    "quiche", "zwiebelkuchen", "flammkuchen vegetarisch",
    "pizza margherita", "pizza verdure", "pizza spinaci", "pizza quattro formaggi", "pizza funghi",
    "feta", "hirtenkaese", "ziegenkaese", "camembert", "brie", "raclette", "fondue kaese",
}


def _contains_any_keyword(text, keywords):
    return any(kw in text for kw in keywords)

def classify_food(name, raw_cat=""):
    name_n = _norm_food_text(name)
    cat_n = _norm_food_text(raw_cat)

    if _contains_any_keyword(name_n, EXPLICIT_VEGAN_KEYWORDS):
        return "VG"

    if _contains_any_keyword(name_n, EXPLICIT_VEG_KEYWORDS):
        return "V"

    if _contains_any_keyword(cat_n, NON_VEG_CATEGORY_KEYWORDS):
        return ""

    if _contains_any_keyword(name_n, NON_VEG_KEYWORDS):
        return ""

    if _contains_any_keyword(name_n, VEG_LIKELY_KEYWORDS):
        return "EV"

    return "EV"


def vv_from_name(name, raw_cat=""):
    return classify_food(name, raw_cat)

def vv_from_cat(raw_cat):
    key = _norm_food_text(raw_cat)
    if key in ("vegan", "vegane"):
        return "VG"
    if key in ("vegetarisch", "vegetarian"):
        return "V"
    if key in NON_VEG_CATEGORY_KEYWORDS:
        return ""
    return ""


def _dedup_products(prods):
    seen = set()
    out = []
    for p in prods:
        key = (p["name"].strip().lower(), p["intPrice"].strip())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _names_differ(a, b):
    def norm(s):
        return re.sub(r"[\s\"'«»„]+", "", s).lower()
    return norm(a) != norm(b)


WAHLWEISE_RE = re.compile(r"^wahlweise(\s+dazu)?(\s+|$)", re.IGNORECASE)


def parse_dom_result(raw_json):
    try:
        data = json.loads(raw_json)
    except Exception as e:
        print(f"  [parse] JSON error: {e}")
        return []

    dishes = []
    used_cats = set()

    for cat_entry in data:
        raw_cat = cat_entry.get("category", "")
        cat = norm_cat(raw_cat, used_cats)
        if not cat:
            print(f"  [parse] skip: {raw_cat!r}")
            continue

        cat_vv = vv_from_cat(raw_cat)
        prods = _dedup_products(cat_entry.get("products", []))
        print(f"  [parse] {cat!r} (raw:{raw_cat!r}) -> {len(cat_entry.get('products', []))} raw -> {len(prods)} deduped")

        main_dish = None
        explicit_oder = False
        next_is_zusatz = False

        for p in prods:
            name = p["name"].strip().replace("\n", " / ")

            if WAHLWEISE_RE.match(name):
                remainder = WAHLWEISE_RE.sub("", name).strip()
                if remainder and main_dish is not None:
                    main_dish["zusatz"] = f"wahlweise: {remainder}"
                    main_dish["zusatz_preis"] = p["intPrice"]
                    print(f"    [parse] zusatz (inline): {remainder!r} | {p['intPrice']!r}")
                elif main_dish is not None:
                    next_is_zusatz = True
                    print("    [parse] zusatz marker found, next product = ingredient")
                continue

            if next_is_zusatz:
                if main_dish is not None:
                    main_dish["zusatz"] = f"wahlweise: {name}"
                    main_dish["zusatz_preis"] = p["intPrice"]
                    print(f"    [parse] zusatz (next): {name!r} | {p['intPrice']!r}")
                next_is_zusatz = False
                continue

            if re.match(r"^(oder|or)$", name.strip(), re.IGNORECASE):
                explicit_oder = True
                print("    [parse] 'oder' separator")
                continue

            if main_dish is None:
                vv = vv_from_name(name, raw_cat) or cat_vv
                main_dish = {
                    "kategorie": cat,
                    "name": name,
                    "preis_int": p["intPrice"],
                    "vv": vv,
                    "oder": "",
                    "oder_prefix": "mit",
                    "oder_preis": "",
                    "oder_vv": "",
                    "zusatz": "",
                    "zusatz_preis": "",
                }
                explicit_oder = False
                print(f"    [parse] main: {name!r} | {p['intPrice']!r}")
            elif explicit_oder or main_dish["oder"] == "":
                if _names_differ(main_dish["name"], name):
                    main_dish["oder"] = name
                    main_dish["oder_prefix"] = "oder" if explicit_oder else "mit"
                    main_dish["oder_preis"] = p["intPrice"]
                    main_dish["oder_vv"] = vv_from_name(name, raw_cat)
                    print(f"    [parse] {main_dish['oder_prefix']}: {name!r} vv={main_dish['oder_vv']!r}")
                else:
                    print(f"    [parse] skip dup: {name!r}")
                explicit_oder = False

        if main_dish:
            dishes.append(main_dish)
            used_cats.add(cat)

    return dishes


# ── Fallback: innerText line-parser ──────────────────────────────────────────
INT_PRICE_RE = re.compile(
    r"Int[\s\u00a0]+[\u20ac$]?([0-9]+[.,][0-9]{2})|Int[\s\u00a0]+([0-9]+[.,][0-9]{2})[\s\u00a0]*[\u20ac$]",
    re.IGNORECASE,
)
ALLERGEN_RE = re.compile(r"^[A-Z]{1,10}$")
OR_RE = re.compile(r"^(oder|or)$", re.IGNORECASE)
EXT_RE = re.compile(r"^Ext[\s\u00a0]", re.IGNORECASE)
CAT_HEADERS_FB = {
    "Soup / Starter": "Suppe",
    "Soup/Starter": "Suppe",
    "Soup": "Suppe",
    "Suppe / Vorspeise": "Suppe",
    "Suppe/Vorspeise": "Suppe",
    "Suppe": "Suppe",
    "Food 1": "Essen 1",
    "Food 2": "Essen 2",
    "Food 3": "Essen 3",
    "Essen 1": "Essen 1",
    "Essen 2": "Essen 2",
    "Essen 3": "Essen 3",
    "Gericht 1": "Essen 1",
    "Gericht 2": "Essen 2",
    "Gericht 3": "Essen 3",
    "Fish": "Essen 3",
    "Fisch": "Essen 3",
    "Vegan": "Essen 2",
    "Vegetarisch": "Essen 2",
    "Aktion": "Aktion",
}
NOISE_FB = {
    "Learn more", "Got it!", "home", "Home", "view_compact", "Menu", "place", "Stores",
    "Impressum", "close", "Close", "English", "Lunch", "filter_list", "Filter", "Store",
    "clear", "Info", "MyCasinoCard", "Opening hours", "Nutzungsbedingungen",
    "Datenschutzerklärung", "Speiseplan", "Mittagessen", "Informationen", "Deutsch",
    "Mehr erfahren", "note", "Aktuelle Woche",
}


def _norm_price(raw):
    try:
        return f"{float(raw.replace(',', '.')):.2f}".replace(".", ",") + " €"
    except Exception:
        return raw


def parse_flat_fallback(lines):
    print("  [fallback-parser] running")
    dishes = []
    cur_cat = None
    cur_vv = ""
    cur_name = []
    seen_cats = set()
    after_oder = False
    oder_name = []
    slots = ["Suppe", "Essen 1", "Essen 2", "Essen 3", "Aktion"]

    def next_free(slot):
        try:
            idx = slots.index(slot)
        except ValueError:
            return None
        return next((s for s in slots[idx + 1:] if s not in seen_cats), None)

    def flush(price=""):
        nonlocal cur_name, cur_vv, after_oder, oder_name
        toks = [
            t for t in cur_name
            if not ALLERGEN_RE.match(t)
            and not OR_RE.match(t)
            and t not in NOISE_FB
            and not INT_PRICE_RE.search(t)
            and not EXT_RE.match(t)
        ]
        while toks and ALLERGEN_RE.match(toks[-1]):
            toks.pop()
        name = " ".join(toks).strip()
        if cur_cat and name and len(name) >= 3 and cur_cat not in seen_cats:
            dishes.append({
                "kategorie": cur_cat,
                "name": name,
                "preis_int": price,
                "vv": cur_vv,
                "oder": "",
                "oder_prefix": "mit",
                "oder_preis": "",
                "oder_vv": "",
                "zusatz": "",
                "zusatz_preis": "",
            })
            seen_cats.add(cur_cat)
        cur_name = []
        cur_vv = ""
        after_oder = False
        oder_name = []

    def flush_oder():
        nonlocal after_oder, oder_name
        toks = [
            t for t in oder_name
            if not ALLERGEN_RE.match(t)
            and t not in NOISE_FB
            and not INT_PRICE_RE.search(t)
            and not EXT_RE.match(t)
        ]
        while toks and ALLERGEN_RE.match(toks[-1]):
            toks.pop()
        name = " ".join(toks).strip()
        if name and len(name) >= 3:
            for dish in reversed(dishes):
                if dish["kategorie"] == cur_cat:
                    if _names_differ(dish["name"], name):
                        dish["oder"] = name
                        dish["oder_prefix"] = "oder"
                        dish["oder_vv"] = vv_from_name(name, cur_cat)
                    break
        after_oder = False
        oder_name = []

    vegan_labels = {
        "Vegan": "VG",
        "Vegetarian": "V",
        "Vegetarisch": "V",
        "vegetarisch": "V",
    }

    for line in lines:
        line = line.strip()
        if not line or line in NOISE_FB:
            continue
        if line in CAT_HEADERS_FB:
            if after_oder:
                flush_oder()
            flush()
            cur_cat = CAT_HEADERS_FB[line]
            cur_name = []
            cur_vv = ""
            continue
        if cur_cat is None:
            continue
        if line in vegan_labels:
            if after_oder:
                flush_oder()
            flush()
            nxt = next_free(cur_cat)
            if nxt:
                cur_cat = nxt
            cur_vv = vegan_labels[line]
            cur_name = []
            continue
        m = INT_PRICE_RE.search(line)
        if m:
            if after_oder:
                flush_oder()
            else:
                flush(_norm_price(m.group(1) or m.group(2) or ""))
            continue
        if OR_RE.match(line):
            if after_oder:
                flush_oder()
            after_oder = True
            oder_name = []
            continue
        if EXT_RE.match(line) or ALLERGEN_RE.match(line):
            continue
        if after_oder:
            oder_name.append(line)
        else:
            cur_name.append(line)

    if after_oder:
        flush_oder()
    flush()
    return dishes


# ── JS helpers ────────────────────────────────────────────────────────────────
JS_CLICK_TAB = r"""
(function(dateStr){
  var tabs=Array.from(document.querySelectorAll('[role="tab"],.mdc-tab,.mat-tab-label,.mat-mdc-tab'));
  var all=Array.from(document.querySelectorAll('button,a,div,span,li')).filter(function(el){
    var t=(el.innerText||el.textContent||'').trim();
    return t.includes(dateStr)&&t.length<40;
  });
  var target=tabs.concat(all).find(function(el){
    return (el.innerText||el.textContent||'').trim().includes(dateStr);
  });
  if(target){target.click();return (target.innerText||target.textContent||'').trim().slice(0,60);}
  return null;
})
"""

JS_TAB_TEXT = r"""
(function(){
  var panels=Array.from(document.querySelectorAll('mat-tab-nav-panel,[role="tabpanel"],mat-tab-body'));
  var active=panels.find(function(p){
    var s=window.getComputedStyle(p);
    return s.display!=='none'&&s.visibility!=='hidden'&&p.offsetHeight>0;
  })||panels[0];
  if(!active){
    var b=document.body.cloneNode(true);
    b.querySelectorAll('script,style,noscript').forEach(function(e){e.remove();});
    return b.innerText||b.textContent||'';
  }
  return active.innerText||active.textContent||'';
}())
"""


# ── Scrape one day ────────────────────────────────────────────────────────────
def scrape_day(page, date_obj):
    url = f"{URL_BASE}/date/{date_obj.strftime('%Y-%m-%d')}"
    if _SID:
        url += f"?ste_sid={_SID}"
    date_label = date_obj.strftime("%d.%m")

    print(f"\n[scrape] {url}  [{date_label}]")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(800)
    _inject(page)
    page.wait_for_timeout(400)
    dismiss_cookie(page)

    found_sel = None
    for sel in [
        "h3.category-header",
        "text=Suppe / Vorspeise",
        "text=Suppe",
        "text=Essen 1",
        "text=Soup / Starter",
        "text=Food 1",
        ".category-grid",
        "app-category-list",
    ]:
        try:
            page.wait_for_selector(sel, timeout=12000)
            found_sel = sel
            print(f"  [wait] found: {sel!r}")
            break
        except Exception:
            pass

    if not found_sel:
        print("  [wait] WARNING: no menu selector, extra 5s")
        page.wait_for_timeout(5000)

    try:
        hdrs = page.evaluate(
            "()=>Array.from(document.querySelectorAll('h3.category-header,h3')).map(h=>h.textContent.trim()).filter(Boolean)"
        )
        print(f"  [debug] h3 headers: {hdrs}")
    except Exception:
        pass

    before_sig = " ".join((page.evaluate(JS_TAB_TEXT) or "").split()[:8])
    clicked = page.evaluate(f"({JS_CLICK_TAB})('{date_label}')")
    print(f"  [tab] click result: {clicked!r}")

    if clicked:
        for attempt in range(25):
            page.wait_for_timeout(200)
            if " ".join((page.evaluate(JS_TAB_TEXT) or "").split()[:8]) != before_sig:
                print(f"  [tab] content changed after {attempt + 1} polls")
                break
        else:
            print("  [tab] content unchanged")
    else:
        print(f"  [tab] no tab for {date_label!r}")
        page.wait_for_timeout(800)

    try:
        hdrs2 = page.evaluate(
            "()=>Array.from(document.querySelectorAll('h3.category-header')).map(h=>h.textContent.trim())"
        )
        print(f"  [debug] category-header after tab: {hdrs2}")
    except Exception:
        pass

    dishes = []
    try:
        raw_json = page.evaluate(JS_EXTRACT)
        print(f"  [dom] JS_EXTRACT length: {len(raw_json) if raw_json else 0}")
        if raw_json and raw_json != "[]":
            dishes = parse_dom_result(raw_json)
            print(f"  [dom] {len(dishes)} dishes")
        else:
            print("  [dom] empty, falling back")
    except Exception as e:
        print(f"  [dom] error: {e}")

    if not dishes:
        raw = page.evaluate(JS_TAB_TEXT) or ""
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        print(f"  [fallback] {len(lines)} lines")
        for i, l in enumerate(lines[:40]):
            print(f"    {i:3d}: {l[:100]}")
        dishes = parse_flat_fallback(lines)

    for dish in dishes:
        zusatz_str = f" | zusatz: {dish.get('zusatz', '')[:40]!r}" if dish.get("zusatz") else ""
        oder_str = (
            f" | {dish.get('oder_prefix', 'oder')}: {dish['oder'][:25]!r} vv={dish.get('oder_vv', '')!r}"
            if dish.get("oder") else ""
        )
        print(
            f"  [result] {dish['kategorie']:8s} | vv={dish['vv']!r:3s} | {dish['name'][:35]!r}"
            + oder_str
            + zusatz_str
        )

    return dishes


# ── Render helpers ────────────────────────────────────────────────────────────
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


def _find_uniform_font_size(draw, texts, max_w, max_h, size_start=19, size_min=10, bold=False):
    for size in range(size_start, size_min - 1, -1):
        f = lf(size, bold)
        lh = _line_h(draw, f)
        all_fit = True
        for text in texts:
            if not text:
                continue
            lines = wrap_text(draw, text, f, max_w, max_lines=20)
            if not lines:
                continue
            if lh * len(lines) > max_h:
                all_fit = False
                break
        if all_fit:
            return size
    return size_min


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


def normalize_day_dishes(dishes):
    by_cat = {}
    for dish in dishes or []:
        cat = dish.get("kategorie", "").strip()
        if cat and cat not in by_cat:
            by_cat[cat] = dish

    out = []
    for cat in CATS:
        out.append(by_cat.get(cat, {
            "kategorie": cat,
            "name": "–",
            "preis_int": "",
            "vv": "",
            "oder": "",
            "oder_prefix": "mit",
            "oder_preis": "",
            "oder_vv": "",
            "zusatz": "",
            "zusatz_preis": "",
        }))
    return out


def _draw_footer_bar(d, text, fftr):
    d.rectangle([(0, H - FOOTER_H), (W, H)], fill=SECONDARY)
    b = d.textbbox((0, 0), text, font=fftr)
    d.text(
        ((W - (b[2] - b[0])) // 2, H - FOOTER_H + (FOOTER_H - (b[3] - b[1])) // 2),
        text,
        font=fftr,
        fill=TEXT_ON_SECONDARY,
    )


def render_day(dishes, target_date, kw, label, local_dt, is_holiday=None):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    ftit = lf(20, True)
    fdate = lf(13)
    fftr = lf(10)
    fbdg = lf(11, True)
    fprc = lf(14, True)
    fcat = lf(12, True)
    fextra = lf(11)

    HDR_H = 58
    LEGEND_H = 22
    GAP = 4
    PAD = 6

    d.rectangle([(0, 0), (W, HDR_H)], fill=PRIMARY)
    day_str = f"{GERMAN_DAY_LONG[target_date.weekday()]}, {target_date.strftime('%d.%m.%Y')}"
    title_str = f"{LOCATION_LABEL}  |  KW {kw:02d}"

    bt = d.textbbox((0, 0), title_str, font=ftit)
    bd = d.textbbox((0, 0), day_str, font=fdate)
    total_h = (bt[3] - bt[1]) + 3 + (bd[3] - bd[1])
    ty = (HDR_H - total_h) // 2

    d.text(((W - (bt[2] - bt[0])) // 2, ty), title_str, font=ftit, fill=TEXT_ON_PRIMARY)
    d.text(((W - (bd[2] - bd[0])) // 2, ty + (bt[3] - bt[1]) + 3), day_str, font=fdate, fill=SUBTITLE_ON_PRIMARY)

    content_top = HDR_H + GAP
    content_bot = H - FOOTER_H - LEGEND_H - 4
    content_h = content_bot - content_top

    real_dishes = [x for x in (dishes or []) if (x.get("name") or "").strip() not in ("", "–")]

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

    elif not real_dishes:
        d.rectangle([(0, content_top), (W, content_bot)], fill=R_ODD)
        msg = "Keine Speisedaten verfügbar"
        fm = lf(22, True)
        bm = d.textbbox((0, 0), msg, font=fm)
        d.text(
            ((W - (bm[2] - bm[0])) // 2, content_top + (content_h - (bm[3] - bm[1])) // 2),
            msg,
            font=fm,
            fill=TEXT_MUTED,
        )

    else:
        dishes_norm = normalize_day_dishes(dishes)

        NCOLS = 2
        NROWS = 2
        cell_w = (W - GAP * (NCOLS + 1)) // NCOLS
        cell_h = (content_h - GAP * (NROWS + 1)) // NROWS
        cat_label_h = _line_h(d, fcat) + 4
        name_max_w = cell_w - 2 * PAD

        for idx, cat in enumerate(CATS):
            col = idx % NCOLS
            row = idx // NCOLS

            cx0 = GAP + col * (cell_w + GAP)
            cy0 = content_top + GAP + row * (cell_h + GAP)
            cx1 = cx0 + cell_w
            cy1 = cy0 + cell_h

            bg = R_ODD if idx % 2 == 0 else R_EVEN
            d.rounded_rectangle([(cx0, cy0), (cx1, cy1)], radius=6, fill=bg)

            d.rounded_rectangle([(cx0, cy0), (cx1, cy0 + cat_label_h + 2)], radius=6, fill=SECONDARY)
            d.rectangle([(cx0, cy0 + cat_label_h - 2), (cx1, cy0 + cat_label_h + 2)], fill=SECONDARY)
            d.text((cx0 + PAD, cy0 + 3), cat, font=fcat, fill=TEXT_ON_SECONDARY)

            it = next((x for x in dishes_norm if x["kategorie"] == cat), None)
            if not it:
                continue

            badge_box = None
            badge_text = ""
            badge_col = None

            if it.get("vv"):
                if it["vv"] == "VG":
                    badge_text = "Vegan"
                    badge_col = C_VG
                elif it["vv"] == "V":
                    badge_text = "Veg."
                    badge_col = C_V
                elif it["vv"] == "EV":
                    badge_text = "evtl. Veg."
                    badge_col = C_V
                else:
                    badge_text = ""
                    badge_col = None
            
                if badge_text:
                    bb = d.textbbox((0, 0), badge_text, font=fbdg)
                    bw2 = bb[2] - bb[0] + 8
                    bh2 = bb[3] - bb[1] + 4
                    badge_box = (bw2, bh2)
          

            extra_lines = []
            if it.get("oder"):
                line = f"{it.get('oder_prefix', 'mit')}: {it['oder']}"
                if it.get("oder_preis"):
                    line += f"  {it['oder_preis']}"
                extra_lines.append((line, PRIMARY))

            if it.get("zusatz"):
                line = it["zusatz"]
                if it.get("zusatz_preis"):
                    line += f"  {it['zusatz_preis']}"
                extra_lines.append((line, PRICE))

            small_lh = _line_h(d, fextra)
            price_reserved_h = (_line_h(d, fprc) + 6) if it.get("preis_int") else 0
            extra_reserved_h = len(extra_lines) * small_lh

            cy = cy0 + cat_label_h + PAD

            if badge_box:
                bw2, bh2 = badge_box
                d.rounded_rectangle([(cx0 + PAD, cy), (cx0 + PAD + bw2, cy + bh2)], radius=3, fill=badge_col)
                d.text((cx0 + PAD + 4, cy + 1), badge_text, font=fbdg, fill=WHITE)
                cy += bh2 + 4

            text_bottom = cy1 - price_reserved_h - extra_reserved_h - PAD
            text_max_h = max(14, text_bottom - cy)
            fn, lhn, name_lines = _fit_font(d, it.get("name", "–"), name_max_w, text_max_h, size_start=20, size_min=9)

            for ln in name_lines:
                if cy + lhn > text_bottom:
                    break
                d.text((cx0 + PAD, cy), ln, font=fn, fill=TEXT)
                cy += lhn

            for extra_text, extra_color in extra_lines:
                lines = wrap_text(d, extra_text, fextra, name_max_w, max_lines=2)
                for ln in lines:
                    if cy + small_lh > cy1 - price_reserved_h - PAD:
                        break
                    d.text((cx0 + PAD, cy), ln, font=fextra, fill=extra_color)
                    cy += small_lh

            if it.get("preis_int"):
                pb = d.textbbox((0, 0), it["preis_int"], font=fprc)
                d.text(
                    (cx1 - (pb[2] - pb[0]) - PAD, cy1 - (pb[3] - pb[1]) - PAD),
                    it["preis_int"],
                    font=fprc,
                    fill=PRICE,
                )

    leg_y = H - FOOTER_H - LEGEND_H - 2
    d.line([(0, leg_y), (W, leg_y)], fill=GRID, width=1)
    leg_y += 1
    d.rectangle([(0, leg_y), (W, leg_y + LEGEND_H)], fill=BG)

    fleg = lf(11)
    lx = 6
    for col, txt in [
        (C_VG, "Vegan"),
        (C_V, "Vegetarisch"),
        (C_HOL_HDR, "Feiertag"),
    ]:
        d.rectangle([(lx, leg_y + 5), (lx + 12, leg_y + 15)], fill=col)
        b = d.textbbox((0, 0), txt, font=fleg)
        d.text((lx + 15, leg_y + 4), txt, font=fleg, fill=TEXT)
        lx += 15 + (b[2] - b[0]) + 12

    footer_txt = (
        f"KW {kw:02d} / {label}  –  "
        f"{local_dt.strftime('%d.%m.%Y %H:%M Uhr')}  –  "
        f"siemens.cateringportal.io  |  {LOCATION_LABEL}"
    )
    _draw_footer_bar(d, footer_txt, fftr)
    return img


CAT_STUB = {
    "Suppe": "Suppe",
    "Essen 1": "Essen 1",
    "Essen 2": "Essen 2",
    "Essen 3": "Essen 3",
    "Aktion": "Aktion",
}


def _draw_stub_label(d, cat, x0, y0, stub_w, row_h):
    label = CAT_STUB[cat]
    for size in range(11, 6, -1):
        f = lf(size, bold=True)
        b = d.textbbox((0, 0), label, font=f)
        tw = b[2] - b[0]
        th = b[3] - b[1]
        if tw <= stub_w - 4:
            tx = x0 + (stub_w - tw) // 2
            ty = y0 + (row_h - th) // 2
            d.text((tx, ty), label, font=f, fill=WHITE)
            return

    f = lf(6, bold=True)
    b = d.textbbox((0, 0), label, font=f)
    tw = b[2] - b[0]
    th = b[3] - b[1]
    d.text((x0 + (stub_w - tw) // 2, y0 + (row_h - th) // 2), label, font=f, fill=WHITE)


def render_week(week_data, kw, label, local_dt, holiday_map, today_date, monday_date, source=""):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    ftit = lf(18, True)
    fdate = lf(12)
    fday = lf(17, True)
    fbdg = lf(12, True)
    fprc = lf(13, True)
    fftr = lf(10)
    fleg = lf(11)

    HDR_H = 52
    DAY_H = 34
    LEGEND_H = 22
    STUB_W = 72
    TODAY_BW = 3
    PAD = 5

    d.rectangle([(0, 0), (W, HDR_H)], fill=PRIMARY)
    friday_date = monday_date + timedelta(4)
    date_range = f"{monday_date.strftime('%d.%m.%Y')} – {friday_date.strftime('%d.%m.%Y')}"
    title_str = f"{LOCATION_LABEL}  |  KW {kw:02d}"

    bt = d.textbbox((0, 0), title_str, font=ftit)
    bd = d.textbbox((0, 0), date_range, font=fdate)
    total_h = (bt[3] - bt[1]) + 3 + (bd[3] - bd[1])
    ty = (HDR_H - total_h) // 2

    d.text(((W - (bt[2] - bt[0])) // 2, ty), title_str, font=ftit, fill=TEXT_ON_PRIMARY)
    d.text(((W - (bd[2] - bd[0])) // 2, ty + (bt[3] - bt[1]) + 3), date_range, font=fdate, fill=SUBTITLE_ON_PRIMARY)
    y = HDR_H

    all_days = list(holiday_map.keys())
    dw = (W - STUB_W) // len(all_days)

    d.rectangle([(0, y), (STUB_W - 1, y + DAY_H - 1)], fill=SECONDARY)
    for i, day in enumerate(all_days):
        x = STUB_W + i * dw
        is_hol = holiday_map[day] is not None
        is_past = _is_past(day, today_date)
        is_today = _is_today(day, today_date)
        col = (220, 150, 0) if is_today else (C_HOL_HDR if is_hol else ((160, 160, 160) if is_past else PRIMARY))
        d.rectangle([(x, y), (x + dw - 1, y + DAY_H - 1)], fill=col)
        b = d.textbbox((0, 0), day, font=fday)
        d.text(
            (x + (dw - (b[2] - b[0])) // 2, y + (DAY_H - (b[3] - b[1])) // 2),
            day,
            font=fday,
            fill=(TEXT_ON_PRIMARY if not is_hol and not is_past and not is_today else WHITE),
        )
        d.line([(x, y), (x, y + DAY_H)], fill=SECONDARY, width=1)
        if is_today:
            d.line([(x, y), (x + dw, y)], fill=C_TODAY, width=TODAY_BW)
    y += DAY_H

    avail = H - y - FOOTER_H - LEGEND_H - 4
    rs = int(avail * 0.17)
    re_ = (avail - rs) // 4
    row_h = {
        "Suppe": rs,
        "Essen 1": re_,
        "Essen 2": re_,
        "Essen 3": re_,
        "Aktion": avail - rs - 3 * re_,
    }

    for ri, cat in enumerate(CATS):
        rh = row_h[cat]
        d.line([(STUB_W, y), (W, y)], fill=GRID, width=1)
        avw = dw - 2 * PAD

        f_sm = lf(11)
        lh_sm = _line_h(d, f_sm)

        candidate_texts = []
        for day in all_days:
            if holiday_map[day] is not None:
                continue
            if _is_past(day, today_date):
                continue
            items = [it for it in week_data.get(day, []) if it["kategorie"] == cat]
            if not items:
                continue
            it = items[0]
            badge_h = 0
            if it.get("vv"):
                b = d.textbbox((0, 0), "Vegan", font=fbdg)
                badge_h = b[3] - b[1] + 4 + 3
            pr_h = 0
            if it.get("preis_int"):
                pb = d.textbbox((0, 0), it["preis_int"], font=fprc)
                pr_h = pb[3] - pb[1] + 4
            extra = (1 if it.get("oder") else 0) + (1 if it.get("zusatz") else 0)
            reserved = lh_sm * extra + pr_h + PAD
            avail_name = rh - PAD - badge_h - reserved
            if avail_name < 12:
                avail_name = 12
            candidate_texts.append((it["name"], avail_name))

        uniform_size = 19
        if candidate_texts:
            min_avail = min(a for _, a in candidate_texts)
            all_names = [t for t, _ in candidate_texts]
            uniform_size = _find_uniform_font_size(
                d, all_names, avw, min_avail, size_start=19, size_min=10
            )

        fn_uniform = lf(uniform_size)
        lhn_uniform = _line_h(d, fn_uniform)

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
                    d.text((x + (dw - (b[2] - b[0])) // 2, y + rh // 2 - 10), "vergangen", font=fprc, fill=TEXT_MUTED)
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
                d.text((x + (dw - (b[2] - b[0])) // 2, y + rh // 2 - 11), "–", font=lf(17), fill=TEXT_MUTED)
                continue

            it = items[0]
            cy = y + PAD

            if it.get("vv"):
                if it["vv"] == "VG":
                    bl = "Vegan"
                    bc = C_VG
                elif it["vv"] == "V":
                    bl = "Veg."
                    bc = C_V
                elif it["vv"] == "EV":
                    bl = "evtl. Veg."
                    bc = C_V
                else:
                    bl = ""
                    bc = None
            
                if bl:
                    b = d.textbbox((0, 0), bl, font=fbdg)
                    bw2 = b[2] - b[0] + 8
                    bh2 = b[3] - b[1] + 4
                    d.rounded_rectangle([(x + PAD, cy), (x + PAD + bw2, cy + bh2)], radius=3, fill=bc)
                    d.text((x + PAD + 4, cy + 2), bl, font=fbdg, fill=WHITE)
                    cy += bh2 + 3

            for ln in wrap_text(d, it["name"], fn_uniform, avw, max_lines=20):
                d.text((x + PAD, cy), ln, font=fn_uniform, fill=TEXT)
                cy += lhn_uniform

            if it.get("oder"):
                fo, lho, oder_lines = _fit_font(
                    d,
                    f"{it.get('oder_prefix', 'mit')}: {it['oder']}",
                    avw,
                    lh_sm * 3,
                    size_start=min(uniform_size, 13),
                    size_min=10,
                )
                for ln in oder_lines:
                    d.text((x + PAD, cy), ln, font=fo, fill=PRIMARY)
                    cy += lho

            if it.get("zusatz"):
                ztext = it["zusatz"]
                if it.get("zusatz_preis"):
                    ztext += f"  {it['zusatz_preis']}"
                fz, lhz, z_lines = _fit_font(
                    d, ztext, avw, lh_sm * 3, size_start=min(uniform_size, 13), size_min=10
                )
                for ln in z_lines:
                    d.text((x + PAD, cy), ln, font=fz, fill=PRICE)
                    cy += lhz

            if it.get("preis_int"):
                b = d.textbbox((0, 0), it["preis_int"], font=fprc)
                d.text(
                    (x + dw - (b[2] - b[0]) - PAD, y + rh - (b[3] - b[1]) - 3),
                    it["preis_int"],
                    font=fprc,
                    fill=PRICE,
                )

            if is_today:
                d.line([(x, y + rh - 1), (x + dw, y + rh - 1)], fill=C_TODAY, width=TODAY_BW)

        d.rectangle([(0, y), (STUB_W - 1, y + rh - 1)], fill=SECONDARY)
        d.line([(STUB_W, y), (STUB_W, y + rh)], fill=GRID, width=1)
        _draw_stub_label(d, cat, 0, y, STUB_W, rh)
        y += rh

    for i, day in enumerate(all_days):
        if _is_today(day, today_date):
            d.line([(STUB_W + i * dw, y), (STUB_W + i * dw + dw, y)], fill=C_TODAY, width=TODAY_BW)

    d.line([(0, y), (W, y)], fill=GRID, width=1)
    y += 1
    d.rectangle([(0, y), (W, y + LEGEND_H)], fill=BG)

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
        d.text((lx + 15, y + 4), txt, font=fleg, fill=TEXT)
        lx += 15 + (b[2] - b[0]) + 12

    src = f" – {source}" if source else ""
    footer_txt = (
        f"KW {kw:02d} / {label}  –  "
        f"{local_dt.strftime('%d.%m.%Y %H:%M Uhr')}  –  "
        f"siemens.cateringportal.io{src}"
    )
    _draw_footer_bar(d, footer_txt, fftr)
    return img


# ── Persistenz / Manifest ─────────────────────────────────────────────────────
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

def _rgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


SIEMENS_OVERVIEW_INCLUDE = {
    "aktion",  
    "essen 1",
    "essen 2",
    "essen 3",
}


def extract_main_dishes_for_overview(dishes):
    out = []
    for dish in dishes or []:
        cat = (dish.get("kategorie") or "").strip()
        name = re.sub(r"\s+", " ", (dish.get("name") or "")).strip()

        if not name or name == "–":
            continue

        if cat.casefold() not in SIEMENS_OVERVIEW_INCLUDE:
            continue

        out.append({
            "category": cat,
            "name": name,
            "price": (dish.get("preis_int") or "").strip(),
            "vv": (dish.get("vv") or "").strip(),
        })

    return out


def _write_overview_source(
    *,
    location_name,
    location_label,
    target_date,
    target_day_label,
    label,
    kw,
    local_dt,
    dishes,
    is_holiday="",
):
    path = OUT_DIR / f"menu_{location_name}.json"

    data = {
        "location_name": location_name,
        "location_label": location_label,
        "display_mode": "day",
        "label": label,
        "kw": kw,
        "target_date": target_date.isoformat(),
        "target_day_label": target_day_label,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "generated_at_local": local_dt.isoformat(),
        "is_holiday": is_holiday or "",
        "source": "siemens.cateringportal.io",
        "theme": {
            "primary": _rgb_to_hex(PRIMARY),
            "secondary": _rgb_to_hex(SECONDARY),
            "price": _rgb_to_hex(PRICE),
        },
        "main_dishes": extract_main_dishes_for_overview(dishes),
    }

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Overview source: {path}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)
    local = german_time(now)
    today_date = local.date()

    print(f"Kantine     : {LOCATION_LABEL}")
    print(f"DISPLAY_MODE: {DISPLAY_MODE}")
    print(f"DISPLAY_DAY : {DISPLAY_DAY!r}")
    print(f"Today       : {today_date}  {local.strftime('%H:%M')} local")

    all_holidays = {}
    for yr in [today_date.year, today_date.year + 1]:
        all_holidays.update(bavaria_holidays(yr))

    if DISPLAY_MODE == "day":
        target_date = resolve_target_date(local, all_holidays)
        target_monday = target_date - timedelta(days=target_date.weekday())
        label, kw = kw_label(datetime.combine(target_monday, datetime.min.time(), tzinfo=timezone.utc))
        holiday_map = week_holiday_map(target_monday)
        dk = day_key(target_date)
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
                warmup(page, URL_BASE)
                dishes = scrape_day(page, target_date)
                browser.close()

            if not dishes:
                print("FEHLER: Zieltag konnte nicht geladen werden.")
                print("FEHLER: Abbruch, damit latest-Bild NICHT überschrieben wird.")
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


        _write_overview_source(
            location_name=LOCATION_NAME,
            location_label=LOCATION_LABEL,
            target_date=target_date,
            target_day_label=dk,
            label=label,
            kw=kw,
            local_dt=local,
            dishes=dishes,
            is_holiday=is_holiday,
        )
        _prune_old_images(LOCATION_NAME)
        return

    target_monday = today_date - timedelta(days=today_date.weekday()) + timedelta(weeks=WEEK_OFFSET)
    label, kw = kw_label(datetime.combine(target_monday, datetime.min.time(), tzinfo=timezone.utc))
    out_path = OUT_DIR / f"kantine_{label}_{LOCATION_NAME}.jpg"
    latest_path = OUT_DIR / f"latest_{LOCATION_NAME}.jpg"

    print(f"Week label  : {label}  (KW {kw:02d})")
    print(f"WEEK_OFFSET : {WEEK_OFFSET}  ->  ab {target_monday}")

    holiday_map = week_holiday_map(target_monday)
    hol_days = [k for k, v in holiday_map.items() if v]
    all_week_dates = [target_monday + timedelta(i) for i in range(5)]

    if WEEK_OFFSET == 0:
        scrape_dates = [dt for dt in all_week_dates if dt >= today_date and day_key(dt) not in hol_days]
    else:
        scrape_dates = [dt for dt in all_week_dates if day_key(dt) not in hol_days]

    print(f"Feiertage   : {[(k, holiday_map[k]) for k in hol_days] or 'keine'}")
    print(f"Scraping    : {[day_key(dt) for dt in scrape_dates]}")

    week_data = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1400, "height": 900},
            extra_http_headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.1"},
        )
        warmup(page, URL_BASE)
        for date_obj in scrape_dates:
            dk = day_key(date_obj)
            dishes = scrape_day(page, date_obj)
            if dishes:
                week_data[dk] = dishes
        browser.close()

    print(f"\nErgebnis    : {list(week_data.keys())}  ({len(week_data)}/{len(scrape_dates)})")
    if not week_data:
        print("Keine Speisedaten – kein Bild.")
        sys.exit(1)

    img = render_week(
        week_data,
        kw,
        label,
        local,
        holiday_map,
        today_date,
        target_monday,
        f"DOM ({len(week_data)}/{len(scrape_dates)} Tage)",
    )
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
