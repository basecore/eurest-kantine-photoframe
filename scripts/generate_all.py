#!/usr/bin/env python3
"""
Generate all photoframe menu images in one run.

Repo layout expected:
- scripts/generate_all.py
- scripts/take_screenshot_eurest.py
- scripts/take_screenshot_siemens.py
- scripts/generate_combined.py
- scripts/generate_rss.py
- docs/images/

Supported env vars:
  DISPLAY_MODE        day|week         default: day
  DISPLAY_DAY         optional weekday override for day mode
  WEEK_OFFSET         optional         default: 0 for day, 1 for week
  ONLY                optional comma-separated filter, e.g. "siemens" or "schaeffler,aumovio"
  GENERATE_COMBINED   1|0|true|false  default: 1
"""

import os
import sys
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PYTHON = sys.executable

GLOBAL_DISPLAY_MODE = (os.environ.get("DISPLAY_MODE") or "day").strip().lower() or "day"
GLOBAL_DISPLAY_DAY = (os.environ.get("DISPLAY_DAY") or "").strip().lower()
DEFAULT_WEEK_OFFSET = "0" if GLOBAL_DISPLAY_MODE == "day" else "1"
GLOBAL_WEEK_OFFSET = (os.environ.get("WEEK_OFFSET") or DEFAULT_WEEK_OFFSET).strip()

ONLY = {
    x.strip().lower()
    for x in (os.environ.get("ONLY") or "").split(",")
    if x.strip()
}

GENERATE_COMBINED = (
    (os.environ.get("GENERATE_COMBINED") or "1").strip().lower()
    not in {"0", "false", "no", "off"}
)

REQUIRED_FOR_COMBINED = {"schaeffler", "aumovio", "siemens"}

TASKS = [
    {
        "name": "schaeffler",
        "script": "take_screenshot_eurest.py",
        "env": {
            "EUREST_LOCATION_ID": "8949",
            "EUREST_LOCATION_NAME": "schaeffler",
        },
    },
    {
        "name": "aumovio",
        "script": "take_screenshot_eurest.py",
        "env": {
            "EUREST_LOCATION_ID": "8950",
            "EUREST_LOCATION_NAME": "aumovio",
        },
    },
    {
        "name": "siemens",
        "script": "take_screenshot_siemens.py",
        "env": {},
    },
]


def should_run(task_name):
    if not ONLY:
        return True
    return task_name.lower() in ONLY


def should_run_combined():
    if not GENERATE_COMBINED:
        print("\n[skip] all_main – GENERATE_COMBINED ist deaktiviert")
        return False

    if GLOBAL_DISPLAY_MODE != "day":
        print(f"\n[skip] all_main – nur im day-Modus sinnvoll (aktuell: {GLOBAL_DISPLAY_MODE})")
        return False

    if not ONLY:
        return True

    if REQUIRED_FOR_COMBINED.issubset(ONLY):
        return True

    print("\n[skip] all_main – ONLY-Filter enthält nicht alle drei Kantinen")
    return False


def run_python_script(script_name, title, extra_env=None):
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(
            f"Script not found: {script_path}\n"
            f"Bitte prüfen, ob die Datei unter scripts/{script_name} liegt."
        )

    env = os.environ.copy()
    env.update({
        "DISPLAY_MODE": GLOBAL_DISPLAY_MODE,
        "DISPLAY_DAY": GLOBAL_DISPLAY_DAY,
        "WEEK_OFFSET": GLOBAL_WEEK_OFFSET,
    })
    if extra_env:
        env.update(extra_env)

    print("\n" + "=" * 72)
    print(f"Generiere: {title}")
    print(f"Script   : {script_path}")
    print(f"CWD      : {REPO_ROOT}")
    print(f"Mode     : {env.get('DISPLAY_MODE')}")
    print(f"Day      : {env.get('DISPLAY_DAY')!r}")
    print(f"Offset   : {env.get('WEEK_OFFSET')}")
    print("=" * 72)

    subprocess.run(
        [PYTHON, str(script_path)],
        cwd=str(REPO_ROOT),
        env=env,
        check=True,
    )


def run_task(task):
    run_python_script(task["script"], task["name"], task["env"])


def run_combined_task():
    run_python_script("generate_combined.py", "all_main")


def main():
    print("Photoframe batch generation")
    print(f"Script dir         : {SCRIPT_DIR}")
    print(f"Repo root          : {REPO_ROOT}")
    print(f"DISPLAY_MODE       : {GLOBAL_DISPLAY_MODE}")
    print(f"DISPLAY_DAY        : {GLOBAL_DISPLAY_DAY!r}")
    print(f"WEEK_OFFSET        : {GLOBAL_WEEK_OFFSET}")
    print(f"ONLY filter        : {sorted(ONLY) if ONLY else 'none'}")
    print(f"GENERATE_COMBINED  : {GENERATE_COMBINED}")

    failures = []

    for task in TASKS:
        if not should_run(task["name"]):
            print(f"\n[skip] {task['name']} – nicht im ONLY-Filter")
            continue

        try:
            run_task(task)
        except Exception as e:
            failures.append((task["name"], str(e)))
            print(f"\n[ERROR] {task['name']} fehlgeschlagen: {e}")

    if not failures and should_run_combined():
        try:
            run_combined_task()
        except Exception as e:
            failures.append(("all_main", str(e)))
            print(f"\n[ERROR] all_main fehlgeschlagen: {e}")

    print("\n" + "-" * 72)
    if failures:
        print("Batch abgeschlossen mit Fehlern:")
        for name, err in failures:
            print(f"- {name}: {err}")
        sys.exit(1)

    print("Batch erfolgreich abgeschlossen.")
    print("Erwartete Outputs in docs/images/:")
    print("- latest_schaeffler.jpg")
    print("- latest_aumovio.jpg")
    print("- latest_siemens.jpg")
    if should_run_combined():
        print("- latest_all_main.jpg")


if __name__ == "__main__":
    main()
