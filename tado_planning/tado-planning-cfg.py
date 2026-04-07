#!/usr/bin/env python3
"""
tado-planning-cfg.py
====================
Web-based configuration editor for tado-planning.
Runs on macOS and Home Assistant (accessible via browser).

Usage:
    python3.11 tado-planning-cfg.py
    python3.11 tado-planning-cfg.py --port 8080
    python3.11 tado-planning-cfg.py --no-browser
"""

import sys
import os
import json
import glob
import argparse
import platform
import datetime
import threading
import webbrowser
import time

from flask import Flask, jsonify, request, render_template, send_from_directory

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

if platform.system() == "Darwin":
    _DEFAULT_TOKEN_FILE    = os.path.join(_SCRIPT_DIR, "tado_refresh_token")
    _DEFAULT_SCHEDULES_DIR = os.path.join(_SCRIPT_DIR, "schedules")
else:
    _DEFAULT_TOKEN_FILE    = "/data/tado_refresh_token"
    _DEFAULT_SCHEDULES_DIR = "/config/tado-planning/schedules"

TOKEN_FILE    = os.environ.get("TADO_TOKEN_FILE",    _DEFAULT_TOKEN_FILE)
SCHEDULES_DIR = os.environ.get("TADO_SCHEDULES_DIR", _DEFAULT_SCHEDULES_DIR)

# ---------------------------------------------------------------------------
# FLASK APP
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_SORT_KEYS"] = False

# ---------------------------------------------------------------------------
# TADO CLIENT
# ---------------------------------------------------------------------------

_tado_client = None

def get_tado_client():
    global _tado_client
    if _tado_client is not None:
        return _tado_client
    try:
        from PyTado.interface.interface import Tado
        _tado_client = Tado(token_file_path=TOKEN_FILE)
        return _tado_client
    except Exception as e:
        raise RuntimeError(f"Cannot connect to Tado: {e}")

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def load_json_file(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save_json_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def list_weekconfigs():
    """List all weekconfig files (non-planning JSON files)."""
    if not os.path.isdir(SCHEDULES_DIR):
        return []
    files = []
    for f in sorted(os.listdir(SCHEDULES_DIR)):
        if f.endswith(".json") and not f.startswith("planning_"):
            files.append(os.path.splitext(f)[0])
    return files

def list_exception_plannings():
    """List all exception planning files."""
    pattern = os.path.join(SCHEDULES_DIR, "planning_*.json")
    files = []
    for f in sorted(glob.glob(pattern)):
        name = os.path.basename(f)
        if name != "planning_standard.json":
            files.append(os.path.splitext(name)[0])
    return files

def get_active_status():
    """Compute what tado_planning.py would apply right now."""
    try:
        sys.path.insert(0, _SCRIPT_DIR)
        import importlib
        import types

        # Minimal reimplementation of the selection logic
        standard_path = os.path.join(SCHEDULES_DIR, "planning_standard.json")
        if not os.path.exists(standard_path):
            return {"error": "planning_standard.json not found"}

        with open(standard_path, encoding="utf-8") as f:
            planning = json.load(f)

        now = datetime.datetime.now()
        iso_week = now.isocalendar()[1]
        parity = "odd" if iso_week % 2 == 1 else "even"

        DAY_NAMES = {
            "monday": 0, "tuesday": 1, "wednesday": 2,
            "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
        }
        DAY_NAMES_EN = {0: "Monday", 1: "Tuesday", 2: "Wednesday",
                        3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday"}

        events = [e for e in planning.get("events", []) if "day" in e and "level" in e]

        current_monday = now - datetime.timedelta(days=now.weekday())
        current_monday = current_monday.replace(hour=0, minute=0, second=0, microsecond=0)
        is_odd = (iso_week % 2 == 1)

        if is_odd:
            odd_monday  = current_monday
            even_monday = current_monday + datetime.timedelta(weeks=1)
            p_odd       = current_monday - datetime.timedelta(weeks=2)
            p_even      = current_monday - datetime.timedelta(weeks=1)
        else:
            even_monday = current_monday
            odd_monday  = current_monday + datetime.timedelta(weeks=1)
            p_even      = current_monday - datetime.timedelta(weeks=2)
            p_odd       = current_monday - datetime.timedelta(weeks=1)

        def build_cycle(o_mon, e_mon, evts):
            cycle = []
            for ev in evts:
                w = ev.get("week", "both").lower()
                d = DAY_NAMES.get(ev["day"].lower(), 0)
                h, m = map(int, ev["time"].split(":"))
                if w in ("odd", "both"):
                    cycle.append((o_mon + datetime.timedelta(days=d, hours=h, minutes=m),
                                  "odd", ev["config"], ev["level"]))
                if w in ("even", "both"):
                    cycle.append((e_mon + datetime.timedelta(days=d, hours=h, minutes=m),
                                  "even", ev["config"], ev["level"]))
            cycle.sort(key=lambda x: x[0])
            return cycle

        result = {"now": now.strftime("%A %d/%m/%Y %H:%M"),
                  "iso_week": iso_week, "parity": parity,
                  "level1": None, "level2": None, "exceptions": []}

        for level in (1, 2):
            levt = [e for e in events if e.get("level") == level]
            if not levt:
                continue
            curr = build_cycle(odd_monday, even_monday, levt)
            prev = build_cycle(p_odd, p_even, levt)
            chosen = None
            chosen_dt = None
            for dt, wt, cfg, lv in curr:
                if now >= dt:
                    chosen = cfg
                    chosen_dt = dt
            if chosen is None and prev:
                chosen = prev[-1][2]
                chosen_dt = prev[-1][0]
            if chosen:
                key = f"level{level}"
                result[key] = {
                    "config": chosen,
                    "since": chosen_dt.strftime("%a %d/%m %H:%M") if chosen_dt else "?"
                }

        # Check exceptions
        for fp in sorted(glob.glob(os.path.join(SCHEDULES_DIR, "planning_*.json"))):
            fname = os.path.basename(fp)
            if fname == "planning_standard.json":
                continue
            with open(fp, encoding="utf-8") as f:
                exc = json.load(f)
            period = exc.get("period")
            if not period:
                continue
            try:
                ps = datetime.datetime.strptime(period["start"], "%Y-%m-%d %H:%M")
                pe = datetime.datetime.strptime(period["end"],   "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if ps <= now <= pe:
                result["exceptions"].append({
                    "file": fname,
                    "description": exc.get("_description", fname),
                    "period": f"{ps.strftime('%d/%m %H:%M')} → {pe.strftime('%d/%m %H:%M')}"
                })

        return result
    except Exception as e:
        return {"error": str(e)}

# ---------------------------------------------------------------------------
# ROUTES — UI
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")

# ---------------------------------------------------------------------------
# API — ZONES
# ---------------------------------------------------------------------------

@app.route("/api/zones")
def api_zones():
    try:
        tado = get_tado_client()
        zones = tado.get_zones()
        result = [{"id": z["id"], "name": z["name"], "type": z.get("type", "?")}
                  for z in sorted(zones, key=lambda x: x["name"].lower())]
        return jsonify({"zones": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# API — WEEKCONFIGS
# ---------------------------------------------------------------------------

@app.route("/api/configs")
def api_configs_list():
    return jsonify({"configs": list_weekconfigs()})

@app.route("/api/configs/<name>")
def api_config_get(name):
    path = os.path.join(SCHEDULES_DIR, f"{name}.json")
    data = load_json_file(path)
    if data is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(data)

@app.route("/api/configs/<name>", methods=["POST"])
def api_config_save(name):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    path = os.path.join(SCHEDULES_DIR, f"{name}.json")
    save_json_file(path, data)
    return jsonify({"ok": True, "saved": name})

@app.route("/api/configs/<name>", methods=["DELETE"])
def api_config_delete(name):
    path = os.path.join(SCHEDULES_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# API — PLANNING STANDARD
# ---------------------------------------------------------------------------

@app.route("/api/planning/standard")
def api_planning_standard_get():
    path = os.path.join(SCHEDULES_DIR, "planning_standard.json")
    data = load_json_file(path)
    if data is None:
        return jsonify({"events": []})
    return jsonify(data)

@app.route("/api/planning/standard", methods=["POST"])
def api_planning_standard_save():
    data = request.get_json()
    path = os.path.join(SCHEDULES_DIR, "planning_standard.json")
    save_json_file(path, data)
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# API — EXCEPTION PLANNINGS
# ---------------------------------------------------------------------------

@app.route("/api/planning/exceptions")
def api_exceptions_list():
    names = list_exception_plannings()
    result = []
    for name in names:
        path = os.path.join(SCHEDULES_DIR, f"{name}.json")
        data = load_json_file(path)
        result.append({
            "name": name,
            "description": data.get("_description", name) if data else name,
            "period": data.get("period", {}) if data else {}
        })
    return jsonify({"exceptions": result})

@app.route("/api/planning/exceptions/<name>")
def api_exception_get(name):
    path = os.path.join(SCHEDULES_DIR, f"{name}.json")
    data = load_json_file(path)
    if data is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(data)

@app.route("/api/planning/exceptions/<name>", methods=["POST"])
def api_exception_save(name):
    data = request.get_json()
    path = os.path.join(SCHEDULES_DIR, f"{name}.json")
    save_json_file(path, data)
    return jsonify({"ok": True})

@app.route("/api/planning/exceptions/<name>", methods=["DELETE"])
def api_exception_delete(name):
    path = os.path.join(SCHEDULES_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# API — STATUS
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    return jsonify(get_active_status())

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="tado-planning configuration editor")
    parser.add_argument("--port",       type=int, default=5000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--host",       default="0.0.0.0")
    args = parser.parse_args()

    url = f"http://localhost:{args.port}"
    print(f"\n  tado-planning configurator")
    print(f"  ──────────────────────────")
    print(f"  URL      : {url}")
    print(f"  Schedules: {SCHEDULES_DIR}")
    print(f"  Token    : {TOKEN_FILE}")
    print(f"\n  Press Ctrl+C to stop.\n")

    if not args.no_browser and platform.system() == "Darwin":
        def open_browser():
            time.sleep(1)
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    app.run(host=args.host, port=args.port, debug=False)

if __name__ == "__main__":
    main()
