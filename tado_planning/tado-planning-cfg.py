#!/usr/bin/env python3
"""
tado-planning-cfg.py  v2.0
==========================
Web configurator for tado-planning v2.
Manages two consolidated JSON files:
    weekconfigs.json  — all zone configurations
    plannings.json    — all plannings (standard + exceptions)

Usage:
    python3 tado-planning-cfg.py
    python3 tado-planning-cfg.py --port 8099 --host 0.0.0.0 --no-browser
"""

import sys
import os
import json
import copy
import argparse
import platform
import datetime
import threading
import webbrowser
import time

from flask import Flask, jsonify, request, render_template

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

if platform.system() == "Darwin":
    _DEFAULT_TOKEN_FILE = os.path.join(_SCRIPT_DIR, "tado_refresh_token")
    _DEFAULT_DATA_DIR   = os.path.join(_SCRIPT_DIR, "schedules")
else:
    _DEFAULT_TOKEN_FILE = "/data/tado_refresh_token"
    _DEFAULT_DATA_DIR   = "/config/tado-planning/schedules"

TOKEN_FILE = os.environ.get("TADO_TOKEN_FILE",    _DEFAULT_TOKEN_FILE)
DATA_DIR   = os.environ.get("TADO_SCHEDULES_DIR", _DEFAULT_DATA_DIR)

PLANNINGS_FILE   = os.path.join(DATA_DIR, "plannings.json")
WEEKCONFIGS_FILE = os.path.join(DATA_DIR, "weekconfigs.json")

# ---------------------------------------------------------------------------
# FLASK APP
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_SORT_KEYS"] = False

# ---------------------------------------------------------------------------
# FILE I/O
# ---------------------------------------------------------------------------

def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_weekconfigs() -> dict:
    if not os.path.exists(WEEKCONFIGS_FILE):
        return {}
    with open(WEEKCONFIGS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_weekconfigs(data: dict):
    _ensure_data_dir()
    with open(WEEKCONFIGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_plannings() -> list:
    if not os.path.exists(PLANNINGS_FILE):
        return []
    with open(PLANNINGS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_plannings(data: list):
    _ensure_data_dir()
    with open(PLANNINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
# VALIDATION HELPERS
# ---------------------------------------------------------------------------

VALID_TIMETABLES = ("Mon-Sun", "Mon-Fri, Sat, Sun", "Mon, ..., Sun")

TIMETABLE_KEYS = {
    "Mon-Sun":           ["Mon-Sun"],
    "Mon-Fri, Sat, Sun": ["Mon-Fri", "Sat", "Sun"],
    "Mon, ..., Sun":     ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}

VALID_CYCLES = ("one-week", "two-weeks-iso", "two-weeks-seq")

DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def validate_planning_conflicts(plannings: list, exclude_name: str = None) -> list:
    """Return list of conflict error strings."""
    errors = []
    seen = {}
    for p in plannings:
        if p.get("name") == exclude_name:
            continue
        key = (p.get("start"), p.get("end"))
        name = p.get("name", "?")
        if key in seen:
            other = seen[key]
            s, e = key
            if s is None and e is None:
                errors.append(f"'{other}' and '{name}': both have no start and no end")
            elif s is None:
                errors.append(f"'{other}' and '{name}': both have no start, same end '{e}'")
            elif e is None:
                errors.append(f"'{other}' and '{name}': both have same start '{s}', no end")
            else:
                errors.append(f"'{other}' and '{name}': identical start and end")
        else:
            seen[key] = name
    return errors


def validate_planning(p: dict, weekconfigs: dict, all_plannings: list,
                      exclude_name: str = None) -> list:
    errors = []
    name = p.get("name", "")

    if not name:
        errors.append("Name is required")

    cycle = p.get("cycle")
    if cycle not in VALID_CYCLES:
        errors.append(f"Invalid cycle '{cycle}'")

    if cycle == "two-weeks-seq" and not p.get("ref_date"):
        errors.append("ref_date is required for two-weeks-seq cycle")

    for field in ("start", "end"):
        val = p.get(field)
        if val is not None:
            try:
                datetime.datetime.strptime(val, "%Y-%m-%d %H:%M")
            except ValueError:
                errors.append(f"Invalid {field} format (expected YYYY-MM-DD HH:MM)")

    start = p.get("start")
    end   = p.get("end")
    if start and end:
        try:
            if datetime.datetime.strptime(start, "%Y-%m-%d %H:%M") >= \
               datetime.datetime.strptime(end,   "%Y-%m-%d %H:%M"):
                errors.append("start must be before end")
        except ValueError:
            pass

    # Conflict check against existing plannings
    test_list = [p] + [x for x in all_plannings if x.get("name") != exclude_name]
    conflicts = validate_planning_conflicts(test_list)
    errors.extend(conflicts)

    events = p.get("events", [])
    has_l1 = any(e.get("level") == 1 for e in events if isinstance(e, dict))
    if not has_l1:
        errors.append("At least one level 1 event is required")

    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            continue
        if ev.get("day", "").lower() not in DAY_NAMES:
            errors.append(f"Event #{i+1}: invalid day '{ev.get('day')}'")
        if ev.get("level") not in (1, 2):
            errors.append(f"Event #{i+1}: level must be 1 or 2")
        cfg = ev.get("config", "")
        if cfg and cfg not in weekconfigs:
            errors.append(f"Event #{i+1}: config '{cfg}' not found in weekconfigs")

    return errors


# ---------------------------------------------------------------------------
# STATUS COMPUTATION
# ---------------------------------------------------------------------------

def get_active_status() -> dict:
    try:
        plannings   = load_plannings()
        weekconfigs = load_weekconfigs()

        if not plannings:
            return {"error": "plannings.json is empty or missing"}

        now      = datetime.datetime.now()
        iso_week = now.isocalendar()[1]
        parity   = "odd" if iso_week % 2 == 1 else "even"

        DAY_NAMES_EN = {0:"Monday",1:"Tuesday",2:"Wednesday",
                        3:"Thursday",4:"Friday",5:"Saturday",6:"Sunday"}
        DAY_MAP = {"monday":0,"tuesday":1,"wednesday":2,
                   "thursday":3,"friday":4,"saturday":5,"sunday":6}

        def parse_dt(s):
            if s is None:
                return None
            return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")

        # Select active planning
        group1, group2, group3 = [], [], []
        for p in plannings:
            start = parse_dt(p.get("start"))
            end   = parse_dt(p.get("end"))
            if start is not None:
                if start <= now and (end is None or end >= now):
                    group1.append(p)
            elif end is not None:
                if end >= now:
                    group2.append(p)
            else:
                group3.append(p)

        active = None
        if group1:
            group1.sort(key=lambda p: (
                -parse_dt(p["start"]).timestamp(),
                parse_dt(p["end"]).timestamp() if p.get("end") else float("inf")
            ))
            active = group1[0]
        elif group2:
            group2.sort(key=lambda p: parse_dt(p["end"]))
            active = group2[0]
        elif group3:
            active = group3[0]

        if not active:
            return {"error": "No active planning found"}

        cycle    = active.get("cycle", "two-weeks-iso")
        ref_date = active.get("ref_date")
        events   = active.get("events", [])

        # Resolve cycle parity
        current_monday = now - datetime.timedelta(days=now.weekday())
        current_monday = current_monday.replace(hour=0, minute=0, second=0, microsecond=0)

        if cycle == "two-weeks-iso":
            is_odd = (iso_week % 2 == 1)
        elif cycle == "two-weeks-seq" and ref_date:
            ref = datetime.datetime.strptime(ref_date, "%Y-%m-%d")
            ref_monday = ref - datetime.timedelta(days=ref.weekday())
            now_monday = now - datetime.timedelta(days=now.weekday())
            is_odd = int((now_monday - ref_monday).days / 7) % 2 == 0
        else:
            is_odd = True

        if is_odd:
            odd_monday, even_monday = current_monday, current_monday + datetime.timedelta(weeks=1)
            p_odd, p_even = current_monday - datetime.timedelta(weeks=2), current_monday - datetime.timedelta(weeks=1)
        else:
            even_monday, odd_monday = current_monday, current_monday + datetime.timedelta(weeks=1)
            p_even, p_odd = current_monday - datetime.timedelta(weeks=2), current_monday - datetime.timedelta(weeks=1)

        def build_cycle(o_mon, e_mon, evts, level):
            result = []
            for ev in evts:
                if ev.get("level") != level:
                    continue
                d = DAY_MAP.get(ev["day"].lower(), 0)
                h, m = map(int, ev["time"].split(":"))
                week = ev.get("week", "both").lower()
                if cycle == "one-week" or week in ("odd", "both"):
                    result.append((o_mon + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))
                if cycle != "one-week" and week in ("even", "both"):
                    result.append((e_mon + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))
            result.sort(key=lambda x: x[0])
            return result

        result = {
            "now":      now.strftime("%A %d/%m/%Y %H:%M"),
            "iso_week": iso_week,
            "parity":   parity,
            "planning": active["name"],
            "cycle":    cycle,
            "level1":   None,
            "level2":   None,
        }

        for level in (1, 2):
            curr = build_cycle(odd_monday, even_monday, events, level)
            prev = build_cycle(p_odd, p_even, events, level)
            if not curr:
                continue
            past = [(dt, cfg) for dt, cfg in curr if now >= dt]
            if past:
                past.sort(key=lambda x: x[0], reverse=True)
                chosen_dt, chosen_cfg = past[0]
            elif prev:
                chosen_dt, chosen_cfg = sorted(prev, key=lambda x: x[0])[-1]
            else:
                chosen_dt, chosen_cfg = sorted(curr, key=lambda x: x[0])[-1]
            result[f"level{level}"] = {
                "config": chosen_cfg,
                "since":  chosen_dt.strftime("%a %d/%m %H:%M")
            }

        return result

    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# ROUTES — UI
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    ingress_path = request.headers.get("X-Ingress-Path", "")
    return render_template("index.html", ingress_path=ingress_path)


# ---------------------------------------------------------------------------
# API — STATUS
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    return jsonify(get_active_status())


# ---------------------------------------------------------------------------
# API — ZONES (from Tado)
# ---------------------------------------------------------------------------

@app.route("/api/zones")
def api_zones():
    try:
        tado  = get_tado_client()
        zones = tado.get_zones()
        result = [{"id": z["id"], "name": z["name"]}
                  for z in sorted(zones, key=lambda x: x["name"].lower())]
        return jsonify({"zones": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API — WEEKCONFIGS
# ---------------------------------------------------------------------------

@app.route("/api/weekconfigs")
def api_weekconfigs_list():
    wc = load_weekconfigs()
    return jsonify({"names": list(wc.keys()), "weekconfigs": wc})


@app.route("/api/weekconfigs/<name>")
def api_weekconfig_get(name):
    wc = load_weekconfigs()
    if name not in wc:
        return jsonify({"error": "Not found"}), 404
    return jsonify(wc[name])


@app.route("/api/weekconfigs/<name>", methods=["POST"])
def api_weekconfig_save(name):
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid data"}), 400
    overwrite = request.args.get("overwrite", "false").lower() == "true"
    wc = load_weekconfigs()
    if name in wc and not overwrite:
        return jsonify({"exists": True, "error": f"'{name}' already exists"}), 409
    wc[name] = data
    save_weekconfigs(wc)
    return jsonify({"ok": True})


@app.route("/api/weekconfigs/<name>", methods=["DELETE"])
def api_weekconfig_delete(name):
    wc = load_weekconfigs()
    if name not in wc:
        return jsonify({"error": "Not found"}), 404
    # Check if referenced in any planning
    plannings = load_plannings()
    refs = []
    for p in plannings:
        for ev in p.get("events", []):
            if isinstance(ev, dict) and ev.get("config") == name:
                refs.append(p["name"])
                break
    if refs:
        return jsonify({
            "error": f"Config '{name}' is referenced in planning(s): {', '.join(refs)}. "
                     f"Remove those references first."
        }), 409
    del wc[name]
    save_weekconfigs(wc)
    return jsonify({"ok": True})


@app.route("/api/weekconfigs/<name>/rename", methods=["POST"])
def api_weekconfig_rename(name):
    body    = request.get_json() or {}
    newname = body.get("newname", "").strip()
    if not newname:
        return jsonify({"error": "newname is required"}), 400
    wc = load_weekconfigs()
    if name not in wc:
        return jsonify({"error": "Not found"}), 404
    if newname in wc:
        return jsonify({"error": f"'{newname}' already exists"}), 409
    wc[newname] = wc.pop(name)
    # Update references in plannings
    plannings = load_plannings()
    for p in plannings:
        for ev in p.get("events", []):
            if isinstance(ev, dict) and ev.get("config") == name:
                ev["config"] = newname
    save_weekconfigs(wc)
    save_plannings(plannings)
    return jsonify({"ok": True})


@app.route("/api/weekconfigs/<name>/copy", methods=["POST"])
def api_weekconfig_copy(name):
    body    = request.get_json() or {}
    newname = body.get("newname", "").strip()
    if not newname:
        return jsonify({"error": "newname is required"}), 400
    wc = load_weekconfigs()
    if name not in wc:
        return jsonify({"error": "Source not found"}), 404
    if newname in wc:
        return jsonify({"error": f"'{newname}' already exists"}), 409
    wc[newname] = copy.deepcopy(wc[name])
    save_weekconfigs(wc)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API — PLANNINGS
# ---------------------------------------------------------------------------

@app.route("/api/plannings")
def api_plannings_list():
    plannings = load_plannings()
    return jsonify({"plannings": plannings})


@app.route("/api/plannings/<name>")
def api_planning_get(name):
    plannings = load_plannings()
    for p in plannings:
        if p.get("name") == name:
            return jsonify(p)
    return jsonify({"error": "Not found"}), 404


@app.route("/api/plannings/<name>", methods=["POST"])
def api_planning_save(name):
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid data"}), 400
    overwrite = request.args.get("overwrite", "false").lower() == "true"
    data["name"] = name

    plannings   = load_plannings()
    weekconfigs = load_weekconfigs()
    existing    = next((p for p in plannings if p.get("name") == name), None)

    if existing and not overwrite:
        return jsonify({"exists": True, "error": f"'{name}' already exists"}), 409

    # Validate
    errors = validate_planning(data, weekconfigs, plannings, exclude_name=name)
    if errors:
        return jsonify({"error": "; ".join(errors)}), 422

    if existing:
        idx = plannings.index(existing)
        plannings[idx] = data
    else:
        plannings.append(data)

    save_plannings(plannings)
    return jsonify({"ok": True})


@app.route("/api/plannings/<name>", methods=["DELETE"])
def api_planning_delete(name):
    plannings = load_plannings()
    match = next((p for p in plannings if p.get("name") == name), None)
    if not match:
        return jsonify({"error": "Not found"}), 404
    # Refuse deletion of last standard planning (no start, no end)
    remaining = [p for p in plannings if p.get("name") != name]
    has_standard = any(p.get("start") is None and p.get("end") is None
                       for p in remaining)
    if not has_standard and match.get("start") is None and match.get("end") is None:
        return jsonify({
            "error": "Cannot delete the only standard planning (no start, no end)."
        }), 409
    plannings = remaining
    save_plannings(plannings)
    return jsonify({"ok": True})


@app.route("/api/plannings/<name>/rename", methods=["POST"])
def api_planning_rename(name):
    body    = request.get_json() or {}
    newname = body.get("newname", "").strip()
    if not newname:
        return jsonify({"error": "newname is required"}), 400
    plannings = load_plannings()
    match = next((p for p in plannings if p.get("name") == name), None)
    if not match:
        return jsonify({"error": "Not found"}), 404
    if any(p.get("name") == newname for p in plannings):
        return jsonify({"error": f"'{newname}' already exists"}), 409
    match["name"] = newname
    save_plannings(plannings)
    return jsonify({"ok": True})


@app.route("/api/plannings/<name>/copy", methods=["POST"])
def api_planning_copy(name):
    body    = request.get_json() or {}
    newname = body.get("newname", "").strip()
    if not newname:
        return jsonify({"error": "newname is required"}), 400
    plannings = load_plannings()
    match = next((p for p in plannings if p.get("name") == name), None)
    if not match:
        return jsonify({"error": "Source not found"}), 404
    if any(p.get("name") == newname for p in plannings):
        return jsonify({"error": f"'{newname}' already exists"}), 409
    new_p = copy.deepcopy(match)
    new_p["name"] = newname
    plannings.append(new_p)
    save_plannings(plannings)
    return jsonify({"ok": True})


@app.route("/api/plannings/validate", methods=["POST"])
def api_planning_validate():
    """Validate a planning without saving — used for real-time UI feedback."""
    data = request.get_json() or {}
    plannings   = load_plannings()
    weekconfigs = load_weekconfigs()
    exclude     = data.pop("_exclude_name", None)
    errors      = validate_planning(data, weekconfigs, plannings, exclude_name=exclude)
    return jsonify({"valid": len(errors) == 0, "errors": errors})


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="tado-planning v2 configurator")
    parser.add_argument("--port",       type=int, default=5000)
    parser.add_argument("--host",       default="0.0.0.0")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://localhost:{args.port}"
    print(f"\n  tado-planning configurator  v2")
    print(f"  ──────────────────────────────")
    print(f"  URL      : {url}")
    print(f"  Data dir : {DATA_DIR}")
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
