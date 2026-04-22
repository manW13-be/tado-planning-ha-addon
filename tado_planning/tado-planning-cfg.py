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
import subprocess
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

TOKEN_FILE    = os.environ.get("TADO_TOKEN_FILE",    _DEFAULT_TOKEN_FILE)
DATA_DIR      = os.environ.get("TADO_SCHEDULES_DIR", _DEFAULT_DATA_DIR)
TADO_CONTEXT  = os.environ.get("TADO_CONTEXT", "unknown")

PLANNINGS_FILE   = os.path.join(DATA_DIR, "plannings.json")
WEEKCONFIGS_FILE = os.path.join(DATA_DIR, "weekconfigs.json")
SETTINGS_FILE    = os.path.join(DATA_DIR, "settings.json")
LOOP_STATUS_FILE  = os.path.join(DATA_DIR, "loop_status.json")
LOOP_TRIGGER_FILE = os.path.join(DATA_DIR, "loop_trigger")
LOG_FILE          = os.path.join(DATA_DIR, "tado-planning.log")
LOG_FILE_PREV     = os.path.join(DATA_DIR, "tado-planning.log.1")

# Script / project paths
_SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR    = os.path.dirname(_SCRIPT_DIR)
PLANNING_SCRIPT = os.path.join(_SCRIPT_DIR, "tado-planning-run.py")

# ---------------------------------------------------------------------------
# FLASK APP
# ---------------------------------------------------------------------------

app = Flask(__name__,
            template_folder=os.path.join(_SCRIPT_DIR, "templates"),
            static_folder=os.path.join(_SCRIPT_DIR, "static"))
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


def load_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {"loop_interval": 60, "default_zone": None}
    with open(SETTINGS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_settings(data: dict):
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
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

# ---------------------------------------------------------------------------
# SHARED PLANNING HELPERS
# ---------------------------------------------------------------------------

def _parse_dt(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M") if s else None


def _week_parity(t: datetime.datetime, planning: dict) -> str:
    cycle    = planning.get("cycle", "two-weeks-iso")
    ref_date = planning.get("ref_date")
    if cycle == "two-weeks-iso":
        return "odd" if t.isocalendar()[1] % 2 == 1 else "even"
    elif cycle == "two-weeks-seq" and ref_date:
        ref     = datetime.datetime.strptime(ref_date, "%Y-%m-%d")
        ref_mon = ref - datetime.timedelta(days=ref.weekday())
        t_mon   = t   - datetime.timedelta(days=t.weekday())
        weeks   = int((t_mon - ref_mon).days / 7)
        return "odd" if weeks % 2 == 0 else "even"
    return "odd"


_DAY_MAP = {"monday":0,"tuesday":1,"wednesday":2,
            "thursday":3,"friday":4,"saturday":5,"sunday":6}
_DAY_ABR = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri",5:"Sat",6:"Sun"}


def _active_plannings_at(plannings: list, t: datetime.datetime) -> list:
    """Return all plannings active at time t, sorted by precedence (highest first)."""
    group1, group2, group3 = [], [], []
    for p in plannings:
        s = _parse_dt(p.get("start"))
        e = _parse_dt(p.get("end"))
        if s is not None:
            if s <= t and (e is None or e > t):
                group1.append(p)
        elif e is not None:
            if e > t:
                group2.append(p)
        else:
            group3.append(p)

    def g1_key(p):
        s = _parse_dt(p["start"])
        e = _parse_dt(p.get("end"))
        return (-s.timestamp(), e.timestamp() if e else float("inf"))

    group1.sort(key=g1_key)
    group2.sort(key=lambda p: _parse_dt(p["end"]))
    return group1 + group2 + group3


def _resolve_config_for_zone(zone: str, level: int,
                              plannings_by_precedence: list,
                              t: datetime.datetime,
                              weekconfigs: dict | None = None) -> tuple[str | None, str | None]:
    """
    Return (config_name, planning_name) for a zone+level at time t.
    Iterates plannings by precedence, returns the first that covers this zone+level.
    Pass weekconfigs to avoid repeated disk reads in tight loops.
    """
    if weekconfigs is None:
        weekconfigs = load_weekconfigs()
    monday = (t - datetime.timedelta(days=t.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)

    for planning in plannings_by_precedence:
        cycle  = planning.get("cycle", "two-weeks-iso")
        events = [e for e in planning.get("events", [])
                  if isinstance(e, dict)
                  and e.get("level") == level
                  and e.get("config") is not None]

        if not events:
            continue

        # Check if any event in this planning references this zone
        wc_name = None
        for ev in events:
            cfg = ev.get("config")
            if cfg:
                wc_name = cfg
                break

        # Build candidates
        parity = _week_parity(t, planning)
        is_odd = (parity == "odd")
        if is_odd:
            odd_mon, even_mon = monday, monday + datetime.timedelta(weeks=1)
            p_odd, p_even = monday - datetime.timedelta(weeks=2), monday - datetime.timedelta(weeks=1)
        else:
            even_mon, odd_mon = monday, monday + datetime.timedelta(weeks=1)
            p_even, p_odd = monday - datetime.timedelta(weeks=2), monday - datetime.timedelta(weeks=1)

        candidates = []
        for ev in events:
            week = ev.get("week", "both").lower()
            d    = _DAY_MAP.get(ev.get("day", "monday").lower(), 0)
            h, m = map(int, ev.get("time", "00:00").split(":"))
            if cycle == "one-week":
                for mon in [odd_mon, even_mon, p_odd, p_even]:
                    candidates.append((mon + datetime.timedelta(days=d, hours=h, minutes=m),
                                       ev["config"]))
            else:
                if week in ("odd", "both"):
                    candidates.append((odd_mon + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))
                    candidates.append((p_odd   + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))
                if week in ("even", "both"):
                    candidates.append((even_mon + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))
                    candidates.append((p_even   + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))

        if not candidates:
            continue

        past = [(dt, cfg) for dt, cfg in candidates if t >= dt]
        if past:
            _, cfg = max(past, key=lambda x: x[0])
        else:
            _, cfg = min(candidates, key=lambda x: x[0])

        # Check if the resolved config covers this zone
        if cfg in weekconfigs and zone in weekconfigs[cfg]:
            return cfg, planning.get("name")

    return None, None


def _all_zones_from_weekconfigs(weekconfigs: dict) -> list[str]:
    zones = set()
    for cfg in weekconfigs.values():
        zones.update(cfg.keys())
    return sorted(zones)


# ---------------------------------------------------------------------------
# STATUS — per zone
# ---------------------------------------------------------------------------

def get_status() -> dict:
    """
    Returns:
      now, iso_week, parity,
      plannings: list of all plannings with their current status/cycle info,
      zones: {zone: {l1: {config, planning}, l2: {config, planning}}}
    """
    try:
        plannings   = load_plannings()
        weekconfigs = load_weekconfigs()
        now         = datetime.datetime.now()
        iso_week    = now.isocalendar()[1]
        parity      = "odd" if iso_week % 2 == 1 else "even"

        # Build planning status list
        planning_status = []
        for p in plannings:
            s = _parse_dt(p.get("start"))
            e = _parse_dt(p.get("end"))
            if s is not None and s > now:
                status = "upcoming"
            elif e is not None and e <= now:
                status = "ended"
            else:
                status = "active"

            cycle    = p.get("cycle", "two-weeks-iso")
            cycle_info = None
            if status == "active":
                if cycle == "two-weeks-iso":
                    cycle_info = f"ISO week {iso_week} — {parity}"
                elif cycle == "two-weeks-seq":
                    par = _week_parity(now, p)
                    cycle_info = f"week {'1' if par == 'odd' else '2'} of 2"
                else:
                    cycle_info = "week 1 of 1"

            period_str = None
            if s or e:
                parts = []
                if s:
                    parts.append(s.strftime("%d/%m %H:%M"))
                else:
                    parts.append("…")
                parts.append("→")
                if e:
                    parts.append(e.strftime("%d/%m %H:%M"))
                    # days remaining / ago
                    delta = int((e - now).days)
                    if status == "active":
                        parts.append(f"· ends in {delta} days" if delta >= 0 else f"· ended {-delta} days ago")
                    elif status == "upcoming":
                        delta_s = int((s - now).days)
                        parts.append(f"· starts in {delta_s} days")
                else:
                    parts.append("…")
                period_str = " ".join(parts)
            else:
                period_str = "always active — no period"

            planning_status.append({
                "name":       p.get("name"),
                "status":     status,
                "cycle":      cycle,
                "cycle_info": cycle_info,
                "period":     period_str,
                "start":      p.get("start"),
                "end":        p.get("end"),
            })

        # Per-zone resolution
        active_pls = _active_plannings_at(plannings, now)
        all_zones  = _all_zones_from_weekconfigs(weekconfigs)

        zones_status = {}
        for zone in all_zones:
            l1_cfg, l1_pl = _resolve_config_for_zone(zone, 1, active_pls, now, weekconfigs)
            l2_cfg, l2_pl = _resolve_config_for_zone(zone, 2, active_pls, now, weekconfigs)
            if l1_cfg or l2_cfg:
                zones_status[zone] = {
                    "l1": {"config": l1_cfg, "planning": l1_pl} if l1_cfg else None,
                    "l2": {"config": l2_cfg, "planning": l2_pl} if l2_cfg else None,
                }

        return {
            "now":      now.strftime("%A %d/%m/%Y %H:%M"),
            "iso_week": iso_week,
            "parity":   parity,
            "plannings": planning_status,
            "zones":    zones_status,
        }

    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# TIMELINE — per zone, per level
# ---------------------------------------------------------------------------

def get_timeline(days: int = 14) -> dict:
    """
    Returns:
      columns: list of {dt, label, boundaries: [{planning, type:'starts'|'ends'}]}
      zones:   {zone: {l1: [col_idx_or_null, ...], l2: [...]}}
               Each entry is either null (no change) or {config, planning}
    """
    try:
        plannings   = load_plannings()
        weekconfigs = load_weekconfigs()
        now         = datetime.datetime.now()
        end         = now + datetime.timedelta(days=days)

        # --- Collect all moments ---
        moments = set()
        moments.add(now.replace(second=0, microsecond=0))

        # Planning boundaries within window
        boundaries = {}  # dt → list of {planning, type}
        for p in plannings:
            for field, btype in (("start", "starts"), ("end", "ends")):
                val = p.get(field)
                if val:
                    dt = _parse_dt(val)
                    if now <= dt <= end:
                        moments.add(dt)
                        boundaries.setdefault(dt, []).append(
                            {"planning": p["name"], "type": btype})

        # Expand events week by week
        for p in plannings:
            cycle    = p.get("cycle", "two-weeks-iso")
            events   = [e for e in p.get("events", []) if isinstance(e, dict)]
            p_start  = _parse_dt(p.get("start"))
            p_end    = _parse_dt(p.get("end"))
            scan     = now - datetime.timedelta(days=7)
            while scan <= end + datetime.timedelta(days=7):
                monday = (scan - datetime.timedelta(days=scan.weekday())).replace(
                    hour=0, minute=0, second=0, microsecond=0)
                parity = _week_parity(monday + datetime.timedelta(hours=12), p)
                for ev in events:
                    week = ev.get("week", "both").lower()
                    d    = _DAY_MAP.get(ev.get("day", "monday").lower(), 0)
                    h, m = map(int, ev.get("time", "00:00").split(":"))
                    if cycle != "one-week":
                        if week == "odd"  and parity != "odd":  continue
                        if week == "even" and parity != "even": continue
                    dt = monday + datetime.timedelta(days=d, hours=h, minutes=m)
                    # Only add moment if the planning is active at that datetime
                    if p_start and dt < p_start:
                        continue
                    if p_end and dt >= p_end:
                        continue
                    if now <= dt <= end:
                        moments.add(dt)
                scan += datetime.timedelta(weeks=1)

        sorted_moments = sorted(moments)

        # --- Build columns ---
        columns = []
        for i, t in enumerate(sorted_moments):
            bds = boundaries.get(t, [])
            columns.append({
                "dt":         t.strftime("%Y-%m-%d %H:%M"),
                "label":      f"{_DAY_ABR[t.weekday()]} {t.strftime('%d/%m')}  {t.strftime('%H:%M')}",
                "now":        (i == 0),
                "boundaries": bds,
            })

        # --- Per-zone, per-level resolution at each moment ---
        all_zones = _all_zones_from_weekconfigs(weekconfigs)
        zones_tl  = {}

        # Pre-compute active_plannings per moment (not per zone×moment)
        active_pls_per_t = [_active_plannings_at(plannings, t) for t in sorted_moments]

        # Pre-compute (l1, l2) per zone per moment
        for zone in all_zones:
            l1_prev = l2_prev = None
            l1_row  = []
            l2_row  = []
            has_any = False

            for t, active_pls in zip(sorted_moments, active_pls_per_t):
                l1_cfg, l1_pl = _resolve_config_for_zone(zone, 1, active_pls, t, weekconfigs)
                l2_cfg, l2_pl = _resolve_config_for_zone(zone, 2, active_pls, t, weekconfigs)

                l1_entry = {"config": l1_cfg, "planning": l1_pl} if l1_cfg else None
                l2_entry = {"config": l2_cfg, "planning": l2_pl} if l2_cfg else None

                # Emit only on change (or first column)
                l1_changed = (l1_cfg != l1_prev)
                l2_changed = (l2_cfg != l2_prev)

                l1_row.append(l1_entry if l1_changed else None)
                l2_row.append(l2_entry if l2_changed else None)

                if l1_entry or l2_entry:
                    has_any = True

                l1_prev = l1_cfg
                l2_prev = l2_cfg

            # Only include zones that have something
            has_l2 = any(e is not None for e in l2_row if e)
            if has_any:
                zones_tl[zone] = {
                    "l1": l1_row,
                    "l2": l2_row if has_l2 else None,
                }

        return {
            "columns": columns,
            "zones":   zones_tl,
        }

    except Exception as e:
        return {"error": str(e)}


@app.route("/")
def index():
    ingress_path = request.headers.get("X-Ingress-Path", "")
    return render_template("index.html", ingress_path=ingress_path)


@app.route("/api/status")
def api_status():
    return jsonify(get_status())


@app.route("/api/timeline")
def api_timeline():
    days = int(request.args.get("days", 14))
    return jsonify(get_timeline(days))



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
# API — LOGS
# ---------------------------------------------------------------------------

@app.route("/api/logs")
def api_logs():
    try:
        lines = int(request.args.get("lines", 200))
        lines = max(1, min(lines, 2000))
        result = []
        # Read from .log.1 first (older), then .log (newer)
        for path in (LOG_FILE_PREV, LOG_FILE):
            if os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as f:
                    result.extend(f.readlines())
        # Return last N lines
        tail = result[-lines:] if len(result) > lines else result
        return jsonify({
            "lines": [l.rstrip("\n") for l in tail],
            "total": len(result),
            "has_log": os.path.exists(LOG_FILE),
        })
    except Exception as e:
        return jsonify({"error": str(e), "lines": [], "total": 0, "has_log": False})


@app.route("/api/logs/clear", methods=["POST"])
def api_logs_clear():
    try:
        for path in (LOG_FILE, LOG_FILE_PREV):
            if os.path.exists(path):
                os.remove(path)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API — LOOP STATUS & TRIGGER
# ---------------------------------------------------------------------------

@app.route("/api/loop-status")
def api_loop_status():
    if not os.path.exists(LOOP_STATUS_FILE):
        return jsonify({"running": False})
    try:
        with open(LOOP_STATUS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # pid=1 in Docker = legitimate entrypoint; outside Docker = stale init
        pid = data.get("pid")
        if pid:
            if pid == 1 and not TADO_CONTEXT.startswith("ha-docker"):
                return jsonify({"running": False})
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                return jsonify({"running": False})
        return jsonify({**data, "running": True})
    except Exception as e:
        return jsonify({"running": False, "error": str(e)})


def _loop_is_alive() -> bool:
    """Return True if a --loop process is currently running."""
    if not os.path.exists(LOOP_STATUS_FILE):
        return False
    try:
        with open(LOOP_STATUS_FILE, encoding="utf-8") as f:
            pid = json.load(f).get("pid")
        if pid:
            if pid == 1 and not TADO_CONTEXT.startswith("ha-docker"):
                return False
            os.kill(pid, 0)
            return True
    except Exception:
        pass
    return False


def _run_scheduler_subprocess():
    """Run tado-planning-run.py in a background thread, tee output to log file."""
    import sys
    env = os.environ.copy()
    env["TADO_SCHEDULES_DIR"] = DATA_DIR
    env["TADO_TOKEN_FILE"]    = TOKEN_FILE
    try:
        _rotate_log()
        proc = subprocess.Popen(
            [sys.executable, PLANNING_SCRIPT],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with open(LOG_FILE, "a", encoding="utf-8") as lf:
            for line in proc.stdout:
                print(line, end="", flush=True)   # stdout → docker logs / terminal
                lf.write(line)
                lf.flush()
        proc.wait(timeout=300)
    except Exception as e:
        print(f"[run-now] subprocess error: {e}", flush=True)


def _rotate_log():
    """Rotate log file if > 512KB."""
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 512_000:
            if os.path.exists(LOG_FILE_PREV):
                os.remove(LOG_FILE_PREV)
            os.rename(LOG_FILE, LOG_FILE_PREV)
    except Exception:
        pass


@app.route("/api/run-now", methods=["POST"])
def api_run_now():
    try:
        if _loop_is_alive():
            # Loop is running — use trigger file so it picks it up within 5s
            _ensure_data_dir()
            with open(LOOP_TRIGGER_FILE, "w") as f:
                f.write(str(datetime.datetime.now()))
            return jsonify({"ok": True, "mode": "trigger"})
        else:
            # Loop not running — fire subprocess directly from Flask
            if not os.path.exists(PLANNING_SCRIPT):
                return jsonify({"error": f"Planning script not found: {PLANNING_SCRIPT}"}), 500
            t = threading.Thread(target=_run_scheduler_subprocess, daemon=True)
            t.start()
            return jsonify({"ok": True, "mode": "direct"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API — SETTINGS
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    s = load_settings()
    return jsonify(s)


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid data"}), 400
    # Validate loop_interval
    interval = data.get("loop_interval")
    if interval is not None:
        try:
            interval = int(interval)
            if interval < 1:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "loop_interval must be a positive integer (minutes)"}), 422
        data["loop_interval"] = interval
    save_settings(data)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# CONTEXT & SERVICE (macOS launchd management)
# ---------------------------------------------------------------------------

import re as _re

def _strip_ansi(text):
    return _re.sub(r'\x1b\[[0-9;]*m', '', text)

@app.route("/api/context")
def api_context():
    return jsonify({"context": TADO_CONTEXT})

@app.route("/api/service/status")
def api_service_status():
    if not TADO_CONTEXT.startswith("mac-"):
        return jsonify({"error": "not macOS"}), 400
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/com.tado-planning"],
        capture_output=True, text=True
    )
    active = result.returncode == 0
    plist  = os.path.expanduser("~/Library/LaunchAgents/com.tado-planning.plist")
    return jsonify({"active": active, "plist_exists": os.path.isfile(plist)})

@app.route("/api/service/install", methods=["POST"])
def api_service_install():
    if not TADO_CONTEXT.startswith("mac-"):
        return jsonify({"error": "not macOS"}), 400
    script = os.path.join(_PROJECT_DIR, "scripts", "launchd_install.sh")
    result = subprocess.run(
        ["bash", script], input="o\n", capture_output=True, text=True
    )
    return jsonify({
        "ok": result.returncode == 0,
        "output": _strip_ansi(result.stdout + result.stderr)
    })

@app.route("/api/service/uninstall", methods=["POST"])
def api_service_uninstall():
    if not TADO_CONTEXT.startswith("mac-"):
        return jsonify({"error": "not macOS"}), 400
    script = os.path.join(_PROJECT_DIR, "scripts", "launchd_uninstall.sh")
    result = subprocess.run(
        ["bash", script], input="o\n", capture_output=True, text=True
    )
    return jsonify({
        "ok": result.returncode == 0,
        "output": _strip_ansi(result.stdout + result.stderr)
    })

# ---------------------------------------------------------------------------
# TADO STATE ENDPOINT
# ---------------------------------------------------------------------------

@app.route("/api/simulate")
def api_simulate():
    try:
        date_str = request.args.get("date")
        cmd = [sys.executable, PLANNING_SCRIPT, "--simulate"]
        if date_str:
            cmd += ["-d", date_str]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "Script failed"
            return jsonify({"error": err}), 500
        return jsonify(json.loads(result.stdout))
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Simulation timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/addon")
def api_addon_info():
    token = os.environ.get("SUPERVISOR_TOKEN")
    out = {"ha_available": bool(token)}
    if not token:
        return jsonify(out)
    try:
        import requests as _req
        hdrs = _ha_headers()

        r = _req.get(f"{_SUP_BASE}/addons/self/info", headers=hdrs, timeout=5)
        if r.ok:
            d = r.json().get("data", {})
            out["version"]          = d.get("version")
            out["version_latest"]   = d.get("version_latest")
            out["update_available"] = d.get("update_available", False)

        for key, eid in [
            ("last_run", "sensor.tado_planning_last_run"),
            ("last_put", "sensor.tado_planning_last_put"),
            ("api_get",  "sensor.tado_planning_api_get_calls"),
            ("api_put",  "sensor.tado_planning_api_put_calls"),
        ]:
            try:
                rs = _req.get(f"{_HA_BASE}/states/{eid}", headers=hdrs, timeout=5)
                if rs.ok:
                    out[key] = rs.json().get("state")
            except Exception:
                pass
    except Exception as e:
        out["error"] = str(e)
    return jsonify(out)


@app.route("/api/addon/check-update", methods=["POST"])
def api_addon_check_update():
    """Force Supervisor to reload the addon store, then return fresh addon info."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return jsonify({"error": "Not running inside HA"}), 400
    try:
        import requests as _req
        hdrs = _ha_headers()
        _req.post(f"{_SUP_BASE}/store/reload", headers=hdrs, timeout=15)
        r = _req.get(f"{_SUP_BASE}/addons/self/info", headers=hdrs, timeout=5)
        if not r.ok:
            return jsonify({"error": r.text}), 500
        d = r.json().get("data", {})
        return jsonify({
            "version":          d.get("version"),
            "version_latest":   d.get("version_latest"),
            "update_available": d.get("update_available", False),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/addon/update", methods=["POST"])
def api_addon_update():
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return jsonify({"error": "Not running inside HA"}), 400
    try:
        import requests as _req
        r = _req.post(f"{_SUP_BASE}/addons/self/update", headers=_ha_headers(), timeout=60)
        if r.ok:
            return jsonify({"ok": True})
        return jsonify({"error": r.text}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tado/zones")
def api_tado_zones():
    try:
        result = subprocess.run(
            [sys.executable, PLANNING_SCRIPT, "--tado-zones"],
            capture_output=True, text=True, timeout=40,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "Script failed"
            return jsonify({"error": err}), 500
        return jsonify(json.loads(result.stdout))
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Tado read timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# HA ENTITY MONITOR (background thread)
# ---------------------------------------------------------------------------

_HA_BASE  = "http://supervisor/core/api"
_SUP_BASE = "http://supervisor"

def _ha_headers():
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _ha_monitor():
    """Poll HA input_boolean triggers every 30 s and act when turned on."""
    import requests as _req

    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return  # not running inside HA

    hdrs = _ha_headers()

    # Register initial states so entities appear in HA dashboard
    for eid, fname, icon in [
        ("input_boolean.tado_planning_run_now",   "Tado Planning — run now",    "mdi:play-circle"),
        ("input_boolean.tado_planning_do_update", "Tado Planning — do update",  "mdi:update"),
    ]:
        try:
            _req.post(f"{_HA_BASE}/states/{eid}", headers=hdrs, timeout=5,
                      json={"state": "off",
                            "attributes": {"friendly_name": fname, "icon": icon}})
        except Exception:
            pass

    while True:
        try:
            r = _req.get(f"{_HA_BASE}/states/input_boolean.tado_planning_run_now",
                         headers=hdrs, timeout=5)
            if r.ok and r.json().get("state") == "on":
                subprocess.Popen(
                    [sys.executable, PLANNING_SCRIPT],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                _req.post(f"{_HA_BASE}/services/input_boolean/turn_off", headers=hdrs,
                          timeout=5, json={"entity_id": "input_boolean.tado_planning_run_now"})
        except Exception:
            pass

        try:
            r = _req.get(f"{_HA_BASE}/states/input_boolean.tado_planning_do_update",
                         headers=hdrs, timeout=5)
            if r.ok and r.json().get("state") == "on":
                _req.post(f"{_SUP_BASE}/addons/self/update", headers=hdrs, timeout=60)
                _req.post(f"{_HA_BASE}/services/input_boolean/turn_off", headers=hdrs,
                          timeout=5, json={"entity_id": "input_boolean.tado_planning_do_update"})
        except Exception:
            pass

        time.sleep(30)


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

    threading.Thread(target=_ha_monitor, daemon=True).start()

    if not args.no_browser and platform.system() == "Darwin":
        def open_browser():
            time.sleep(1)
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
