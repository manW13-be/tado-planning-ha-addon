#!/usr/bin/env python3
"""
tado-planning-run.py  v2.0
==========================
Manages Tado heating schedules from two consolidated JSON files:

    plannings.json   — all plannings (standard + exceptions) in one file
    weekconfigs.json — all zone configurations in one file

Planning selection (precedence, highest to lowest):
    1. Planning with start <= now  → latest start wins;
                                     equal start → earliest end wins
    2. Planning without start, with end >= now → earliest end wins
    3. Planning without start, without end  → the "standard" (only one allowed)

Conflict rules (rejected at validation):
    - Two plannings without start AND without end
    - Two plannings with same start AND same end
    - Two plannings with same start AND both without end
    - Two plannings without start AND with same end

Cycle types:
    one-week        — 7-day repeating cycle (single week, "week" field ignored)
    two-weeks-iso   — odd/even ISO week number
    two-weeks-seq   — two alternating weeks counted from ref_date

Usage:
    python3 tado-planning-run.py                  # auto mode
    python3 tado-planning-run.py -d 2026-06-01    # simulate a date
    python3 tado-planning-run.py -v               # verbosity 1
    python3 tado-planning-run.py -vv              # verbosity 2
    python3 tado-planning-run.py -vvv             # + API blocks
    python3 tado-planning-run.py -vvvv            # + raw PUT/GET

Verbosity:
    0  ISO week, active planning, active configs, result
    1  + weekconfig zone details
    2  + cycle candidates with selection trace
    3  + blocks sent to API
    4  + raw PUT/GET requests

Requirements:
    pip install "python-tado>=0.18"
"""

import sys
import os
import json
import argparse
import webbrowser
import datetime
import platform
import time

from PyTado.interface.interface import Tado
from PyTado.http import TadoRequest, Action, Mode, Domain, DeviceActivationStatus

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

if platform.system() == "Darwin":
    _DEFAULT_TOKEN_FILE  = os.path.join(_SCRIPT_DIR, "tado_refresh_token")
    _DEFAULT_DATA_DIR    = os.path.join(_SCRIPT_DIR, "schedules")
else:
    _DEFAULT_TOKEN_FILE  = "/data/tado_refresh_token"
    _DEFAULT_DATA_DIR    = "/config/tado-planning/schedules"

TOKEN_FILE = os.environ.get("TADO_TOKEN_FILE",    _DEFAULT_TOKEN_FILE)
DATA_DIR   = os.environ.get("TADO_SCHEDULES_DIR", _DEFAULT_DATA_DIR)

PLANNINGS_FILE    = os.path.join(DATA_DIR, "plannings.json")
WEEKCONFIGS_FILE  = os.path.join(DATA_DIR, "weekconfigs.json")
STATS_FILE        = os.path.join(DATA_DIR, "api_stats.json")

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

TIMETABLE_MON_SUN         = "Mon-Sun"
TIMETABLE_MON_FRI_SAT_SUN = "Mon-Fri, Sat, Sun"
TIMETABLE_MON_TO_SUN      = "Mon, ..., Sun"
VALID_TIMETABLES = (TIMETABLE_MON_SUN, TIMETABLE_MON_FRI_SAT_SUN, TIMETABLE_MON_TO_SUN)

TIMETABLE_IDS = {
    TIMETABLE_MON_SUN:          0,
    TIMETABLE_MON_FRI_SAT_SUN:  1,
    TIMETABLE_MON_TO_SUN:       2,
}

TIMETABLE_REQUIRED_KEYS = {
    TIMETABLE_MON_SUN:         ["Mon-Sun"],
    TIMETABLE_MON_FRI_SAT_SUN: ["Mon-Fri", "Sat", "Sun"],
    TIMETABLE_MON_TO_SUN:      ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}

DAY_KEY_TO_API = {
    "Mon-Sun": "MONDAY_TO_SUNDAY",
    "Mon-Fri": "MONDAY_TO_FRIDAY",
    "Sat":     "SATURDAY",
    "Sun":     "SUNDAY",
    "Mon":     "MONDAY",
    "Tue":     "TUESDAY",
    "Wed":     "WEDNESDAY",
    "Thu":     "THURSDAY",
    "Fri":     "FRIDAY",
}

DAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}

DAY_NAMES_EN = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday",
    3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday",
}

VALID_CYCLES = ("one-week", "two-weeks-iso", "two-weeks-seq")

VERBOSITY = 0


def log(msg: str, level: int = 0):
    if VERBOSITY >= level:
        print(msg, flush=True)


# ---------------------------------------------------------------------------
# FILE LOADING
# ---------------------------------------------------------------------------

def load_json(path: str) -> object:
    if not os.path.exists(path):
        log(f"[ERROR] File not found: {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            log(f"[ERROR] JSON parse error in {path}: {e}")
            sys.exit(1)


def load_data_files() -> tuple[list, dict]:
    """Load and return (plannings list, weekconfigs dict)."""
    plannings   = load_json(PLANNINGS_FILE)
    weekconfigs = load_json(WEEKCONFIGS_FILE)
    if not isinstance(plannings, list):
        log(f"[ERROR] {PLANNINGS_FILE}: expected a JSON array at root")
        sys.exit(1)
    if not isinstance(weekconfigs, dict):
        log(f"[ERROR] {WEEKCONFIGS_FILE}: expected a JSON object at root")
        sys.exit(1)
    return plannings, weekconfigs


# ---------------------------------------------------------------------------
# VALIDATION — WEEKCONFIGS
# ---------------------------------------------------------------------------

def _validate_slot(slot: object, zone: str, day_key: str, idx: int) -> str | None:
    if not isinstance(slot, dict):
        return f"[VALIDATION] '{zone}'/'{day_key}' slot #{idx}: expected dict, got {type(slot).__name__}"
    for field in ("start", "temp"):
        if field not in slot:
            return f"[VALIDATION] '{zone}'/'{day_key}' slot #{idx}: missing field '{field}'"
    start = slot["start"]
    if not isinstance(start, str) or len(start) != 5 or start[2] != ":":
        return f"[VALIDATION] '{zone}'/'{day_key}' slot #{idx}: 'start' must be HH:MM, got '{start}'"
    try:
        h, m = int(start[:2]), int(start[3:])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except ValueError:
        return f"[VALIDATION] '{zone}'/'{day_key}' slot #{idx}: invalid time '{start}'"
    try:
        float(slot["temp"])
    except (TypeError, ValueError):
        return f"[VALIDATION] '{zone}'/'{day_key}' slot #{idx}: 'temp' must be a number, got '{slot['temp']}'"
    return None


def validate_zone_cfg(zone: str, cfg: object) -> list[str]:
    errors = []
    if not isinstance(cfg, dict):
        return [f"[VALIDATION] '{zone}': expected dict, got {type(cfg).__name__}"]

    AWAY_FIELDS = ("away_temp", "away_enabled", "preheat")
    away_present = [k for k in AWAY_FIELDS if k in cfg]
    if away_present and len(away_present) < len(AWAY_FIELDS):
        missing = [k for k in AWAY_FIELDS if k not in cfg]
        errors.append(
            f"[VALIDATION] '{zone}': incomplete away config — "
            f"present: {away_present}, missing: {missing}. "
            f"All three fields (away_temp, away_enabled, preheat) must be defined together."
        )

    if "away_temp" in cfg:
        try:
            float(cfg["away_temp"])
        except (TypeError, ValueError):
            errors.append(f"[VALIDATION] '{zone}': 'away_temp' must be a number")

    if "away_enabled" in cfg and not isinstance(cfg["away_enabled"], bool):
        errors.append(f"[VALIDATION] '{zone}': 'away_enabled' must be boolean")

    if "early_start" in cfg and not isinstance(cfg["early_start"], bool):
        errors.append(f"[VALIDATION] '{zone}': 'early_start' must be boolean")

    VALID_PREHEAT = {"off", "eco", "balance", "comfort", "équilibre", "confort", "medium"}
    if "preheat" in cfg and cfg["preheat"].lower() not in VALID_PREHEAT:
        errors.append(f"[VALIDATION] '{zone}': invalid 'preheat' '{cfg['preheat']}'")

    if "timetable" not in cfg:
        return errors  # away-only zone

    tt = cfg["timetable"]
    if tt not in VALID_TIMETABLES:
        errors.append(f"[VALIDATION] '{zone}': unknown timetable '{tt}'")
        return errors

    required = TIMETABLE_REQUIRED_KEYS[tt]
    for key in required:
        if key not in cfg:
            errors.append(f"[VALIDATION] '{zone}': missing key '{key}' for timetable '{tt}'")
        else:
            slots = cfg[key]
            if not isinstance(slots, list) or len(slots) == 0:
                errors.append(f"[VALIDATION] '{zone}'/'{key}': must be a non-empty list")
            else:
                for i, s in enumerate(slots):
                    err = _validate_slot(s, zone, key, i)
                    if err:
                        errors.append(err)
    return errors


def validate_weekconfigs(weekconfigs: dict) -> list[str]:
    errors = []
    if not weekconfigs:
        errors.append("[VALIDATION] weekconfigs.json is empty — no configs defined")
        return errors
    for config_name, zones in weekconfigs.items():
        if not isinstance(zones, dict):
            errors.append(f"[VALIDATION] config '{config_name}': expected dict of zones")
            continue
        for zone, cfg in zones.items():
            errors.extend(validate_zone_cfg(zone, cfg))
    return errors


# ---------------------------------------------------------------------------
# VALIDATION — PLANNINGS
# ---------------------------------------------------------------------------

def _parse_dt(s: str, field: str, planning_name: str) -> datetime.datetime | None:
    if s is None:
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        return None  # reported by caller


def validate_planning(p: dict, weekconfigs: dict) -> list[str]:
    errors = []
    name = p.get("name", "<unnamed>")

    if "name" not in p:
        errors.append("[VALIDATION] planning missing 'name' field")

    cycle = p.get("cycle")
    if cycle not in VALID_CYCLES:
        errors.append(f"[VALIDATION] planning '{name}': invalid cycle '{cycle}'. "
                      f"Valid: {list(VALID_CYCLES)}")

    if cycle == "two-weeks-seq" and not p.get("ref_date"):
        errors.append(f"[VALIDATION] planning '{name}': cycle 'two-weeks-seq' requires 'ref_date'")

    if cycle == "two-weeks-seq" and p.get("ref_date"):
        try:
            datetime.datetime.strptime(p["ref_date"], "%Y-%m-%d")
        except ValueError:
            errors.append(f"[VALIDATION] planning '{name}': invalid ref_date '{p['ref_date']}'")

    # start / end
    start_raw = p.get("start")
    end_raw   = p.get("end")
    start_dt  = None
    end_dt    = None

    if start_raw is not None:
        try:
            start_dt = datetime.datetime.strptime(start_raw, "%Y-%m-%d %H:%M")
        except ValueError:
            errors.append(f"[VALIDATION] planning '{name}': invalid start '{start_raw}'")

    if end_raw is not None:
        try:
            end_dt = datetime.datetime.strptime(end_raw, "%Y-%m-%d %H:%M")
        except ValueError:
            errors.append(f"[VALIDATION] planning '{name}': invalid end '{end_raw}'")

    if start_dt and end_dt and start_dt >= end_dt:
        errors.append(f"[VALIDATION] planning '{name}': start must be before end")

    # events
    events = p.get("events", [])
    if not isinstance(events, list):
        errors.append(f"[VALIDATION] planning '{name}': 'events' must be a list")
        return errors

    has_l1 = any(e.get("level") == 1 for e in events)
    if not has_l1:
        errors.append(f"[VALIDATION] planning '{name}': no level 1 events defined")

    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            errors.append(f"[VALIDATION] planning '{name}' event #{i}: expected dict")
            continue
        for field in ("day", "time", "level", "config"):
            if field not in ev:
                errors.append(f"[VALIDATION] planning '{name}' event #{i}: missing '{field}'")
        if "day" in ev and ev["day"].lower() not in DAY_NAMES:
            errors.append(f"[VALIDATION] planning '{name}' event #{i}: "
                          f"invalid day '{ev['day']}'")
        if "level" in ev and ev["level"] not in (1, 2):
            errors.append(f"[VALIDATION] planning '{name}' event #{i}: "
                          f"level must be 1 or 2")
        if "config" in ev:
            if ev["config"] not in weekconfigs:
                errors.append(f"[VALIDATION] planning '{name}' event #{i}: "
                               f"config '{ev['config']}' not found in weekconfigs.json")
        if "week" in ev and cycle in ("two-weeks-iso", "two-weeks-seq"):
            if ev["week"].lower() not in ("odd", "even", "both"):
                errors.append(f"[VALIDATION] planning '{name}' event #{i}: "
                               f"invalid week '{ev['week']}' (expected odd/even/both)")

    return errors


def validate_planning_conflicts(plannings: list) -> list[str]:
    """Check for forbidden duplicate start/end combinations."""
    errors = []

    def key(p):
        return (p.get("start"), p.get("end"))

    # Group by (start, end) to detect conflicts
    seen = {}
    for p in plannings:
        k = key(p)
        name = p.get("name", "<unnamed>")
        if k in seen:
            other = seen[k]
            s, e = k
            if s is None and e is None:
                errors.append(f"[VALIDATION] plannings '{other}' and '{name}': "
                               f"both have no start and no end (only one 'standard' allowed)")
            elif s is None:
                errors.append(f"[VALIDATION] plannings '{other}' and '{name}': "
                               f"both have no start and same end '{e}'")
            elif e is None:
                errors.append(f"[VALIDATION] plannings '{other}' and '{name}': "
                               f"both have same start '{s}' and no end")
            else:
                errors.append(f"[VALIDATION] plannings '{other}' and '{name}': "
                               f"identical start '{s}' and end '{e}'")
        else:
            seen[k] = name

    return errors


def validate_all(plannings: list, weekconfigs: dict) -> bool:
    """Run full validation. Returns True if valid, False if errors found."""
    all_errors = []

    # Weekconfigs
    all_errors.extend(validate_weekconfigs(weekconfigs))

    # Each planning individually
    for p in plannings:
        all_errors.extend(validate_planning(p, weekconfigs))

    # Cross-planning conflict check
    all_errors.extend(validate_planning_conflicts(plannings))

    if all_errors:
        log("[VALIDATION] Errors found:")
        for err in all_errors:
            log(f"  {err}")
        return False

    log(f"[VALIDATION] OK — {len(plannings)} planning(s), "
        f"{len(weekconfigs)} weekconfig(s) validated.")
    return True


# ---------------------------------------------------------------------------
# PLANNING SELECTION — per zone
# ---------------------------------------------------------------------------

def _parse_dt_safe(s: str | None) -> datetime.datetime | None:
    if s is None:
        return None
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")


def active_plannings_at(plannings: list, now: datetime.datetime) -> list:
    """Return all plannings active at `now`, sorted by precedence (highest first)."""
    group1, group2, group3 = [], [], []
    for p in plannings:
        s = _parse_dt_safe(p.get("start"))
        e = _parse_dt_safe(p.get("end"))
        if s is not None:
            if s <= now and (e is None or e > now):
                group1.append(p)
        elif e is not None:
            if e > now:
                group2.append(p)
        else:
            group3.append(p)

    def g1_key(p):
        s = _parse_dt_safe(p["start"])
        e = _parse_dt_safe(p.get("end"))
        return (-s.timestamp(), e.timestamp() if e else float("inf"))

    group1.sort(key=g1_key)
    group2.sort(key=lambda p: _parse_dt_safe(p["end"]))
    return group1 + group2 + group3


def resolve_config_for_zone(zone: str, level: int,
                             plannings_by_precedence: list,
                             now: datetime.datetime,
                             weekconfigs: dict) -> tuple[str | None, str | None]:
    """
    Return (config_name, planning_name) for a zone+level at time now.
    Walks plannings by precedence, returns the first that covers this zone+level.
    """
    monday = (now - datetime.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)

    for planning in plannings_by_precedence:
        cycle  = planning.get("cycle", "two-weeks-iso")
        events = [e for e in planning.get("events", [])
                  if isinstance(e, dict) and e.get("level") == level
                  and e.get("config") is not None]
        if not events:
            continue

        # Determine parity for this planning
        ref_date = planning.get("ref_date")
        if cycle == "two-weeks-iso":
            iso_week = now.isocalendar()[1]
            is_odd   = (iso_week % 2 == 1)
        elif cycle == "two-weeks-seq" and ref_date:
            ref     = datetime.datetime.strptime(ref_date, "%Y-%m-%d")
            ref_mon = ref - datetime.timedelta(days=ref.weekday())
            now_mon = now - datetime.timedelta(days=now.weekday())
            is_odd  = int((now_mon - ref_mon).days / 7) % 2 == 0
        else:
            is_odd = True

        if is_odd:
            odd_mon, even_mon = monday, monday + datetime.timedelta(weeks=1)
            p_odd, p_even = monday - datetime.timedelta(weeks=2), monday - datetime.timedelta(weeks=1)
        else:
            even_mon, odd_mon = monday, monday + datetime.timedelta(weeks=1)
            p_even, p_odd = monday - datetime.timedelta(weeks=2), monday - datetime.timedelta(weeks=1)

        candidates = []
        for ev in events:
            week = ev.get("week", "both").lower()
            d    = DAY_NAMES.get(ev["day"].lower(), 0)
            h, m = map(int, ev["time"].split(":"))
            if cycle == "one-week":
                for mon in [odd_mon, even_mon, p_odd, p_even]:
                    candidates.append((mon + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))
            else:
                if week in ("odd", "both"):
                    candidates.append((odd_mon + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))
                    candidates.append((p_odd   + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))
                if week in ("even", "both"):
                    candidates.append((even_mon + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))
                    candidates.append((p_even   + datetime.timedelta(days=d, hours=h, minutes=m), ev["config"]))

        if not candidates:
            continue

        past = [(dt, cfg) for dt, cfg in candidates if now >= dt]
        cfg  = max(past, key=lambda x: x[0])[1] if past else min(candidates, key=lambda x: x[0])[1]

        # Check if resolved config covers this zone
        if cfg in weekconfigs and zone in weekconfigs[cfg]:
            return cfg, planning.get("name")

    return None, None


# ---------------------------------------------------------------------------
# CYCLE RESOLUTION
# ---------------------------------------------------------------------------

def _week_parity_iso(now: datetime.datetime) -> str:
    """Return 'odd' or 'even' based on ISO week number."""
    return "odd" if now.isocalendar()[1] % 2 == 1 else "even"


def _week_parity_seq(now: datetime.datetime, ref_date: str) -> str:
    """Return 'odd' or 'even' based on weeks elapsed since ref_date."""
    ref = datetime.datetime.strptime(ref_date, "%Y-%m-%d")
    # Align both to Monday of their respective week
    ref_monday = ref - datetime.timedelta(days=ref.weekday())
    now_monday = now - datetime.timedelta(days=now.weekday())
    weeks = int((now_monday - ref_monday).days / 7)
    return "odd" if weeks % 2 == 0 else "even"


def select_config_for_level(events: list, level: int, now: datetime.datetime,
                             cycle: str, ref_date: str | None = None) -> tuple[str | None, datetime.datetime | None]:
    """
    Return (config_name, since_datetime) for the given level.
    Returns (None, None) if no events for this level.
    """
    level_events = [e for e in events if e.get("level") == level]
    if not level_events:
        return None, None

    current_monday = now - datetime.timedelta(days=now.weekday())
    current_monday = current_monday.replace(hour=0, minute=0, second=0, microsecond=0)

    if cycle == "one-week":
        # All events repeat every week regardless of parity
        candidates = []
        for ev in level_events:
            d = DAY_NAMES[ev["day"].lower()]
            h, m = map(int, ev["time"].split(":"))
            dt = current_monday + datetime.timedelta(days=d, hours=h, minutes=m)
            candidates.append((dt, ev["config"]))
            # Also previous week for wrap-around
            dt_prev = dt - datetime.timedelta(weeks=1)
            candidates.append((dt_prev, ev["config"]))

    elif cycle in ("two-weeks-iso", "two-weeks-seq"):
        if cycle == "two-weeks-iso":
            iso_week = now.isocalendar()[1]
            is_odd = (iso_week % 2 == 1)
        else:
            parity = _week_parity_seq(now, ref_date)
            is_odd = (parity == "odd")

        if is_odd:
            odd_monday       = current_monday
            even_monday      = current_monday + datetime.timedelta(weeks=1)
            prev_odd_monday  = current_monday - datetime.timedelta(weeks=2)
            prev_even_monday = current_monday - datetime.timedelta(weeks=1)
        else:
            even_monday      = current_monday
            odd_monday       = current_monday + datetime.timedelta(weeks=1)
            prev_even_monday = current_monday - datetime.timedelta(weeks=2)
            prev_odd_monday  = current_monday - datetime.timedelta(weeks=1)

        def build_cycle(o_mon, e_mon):
            result = []
            for ev in level_events:
                week = ev.get("week", "both").lower()
                d = DAY_NAMES[ev["day"].lower()]
                h, m = map(int, ev["time"].split(":"))
                if week in ("odd", "both"):
                    result.append((o_mon + datetime.timedelta(days=d, hours=h, minutes=m),
                                   ev["config"]))
                if week in ("even", "both"):
                    result.append((e_mon + datetime.timedelta(days=d, hours=h, minutes=m),
                                   ev["config"]))
            result.sort(key=lambda x: x[0])
            return result

        current_cycle = build_cycle(odd_monday, even_monday)
        prev_cycle    = build_cycle(prev_odd_monday, prev_even_monday)
        candidates    = current_cycle + prev_cycle

    else:
        return None, None

    log(f"\n[CANDIDATES level {level}] ({cycle}):", 2)
    for dt, cfg in sorted(candidates):
        marker = " ◄" if now >= dt else ""
        log(f"  {'✓' if now >= dt else '·'} {dt.strftime('%a %d/%m %H:%M')} → {cfg}{marker}", 2)

    # Find the most recent past event
    past = [(dt, cfg) for dt, cfg in candidates if now >= dt]
    if past:
        past.sort(key=lambda x: x[0], reverse=True)
        chosen_dt, chosen_cfg = past[0]
        log(f"  → Selected: {chosen_cfg} (since {chosen_dt.strftime('%a %d/%m %H:%M')})", 2)
        return chosen_cfg, chosen_dt

    # Wrap-around: use the last event of all candidates
    all_sorted = sorted(candidates, key=lambda x: x[0])
    if all_sorted:
        chosen_dt, chosen_cfg = all_sorted[-1]
        log(f"  → Wrap-around: {chosen_cfg} ({chosen_dt.strftime('%a %d/%m %H:%M')})", 2)
        return chosen_cfg, chosen_dt

    return None, None


# ---------------------------------------------------------------------------
# TADO API HELPERS
# ---------------------------------------------------------------------------

_api_stats: dict[str, int] = {"GET": 0, "PUT": 0}
_last_put_time: list[str]  = []   # list so we can mutate from nested scope

def _load_api_stats():
    try:
        with open(STATS_FILE, encoding="utf-8") as _f:
            _d = json.load(_f)
            _api_stats["GET"] = int(_d.get("GET", 0))
            _api_stats["PUT"] = int(_d.get("PUT", 0))
    except (FileNotFoundError, Exception):
        pass

_load_api_stats()
_preheat_unsupported: set  = set() # zones where Tado rejected preheatingLevel
_auth_not_validated_logged = False  # suppress repeated "not validated yet" lines


def tado_put(tado: Tado, command: str, payload):
    _api_stats["PUT"] += 1
    _last_put_time[:] = [datetime.datetime.now().astimezone().isoformat()]
    log(f"[API]  PUT {command}", 4)
    log(f"       payload : {json.dumps(payload, ensure_ascii=False)}", 4)
    req    = TadoRequest(command=command, action=Action.CHANGE, payload=payload, mode=Mode.OBJECT)
    result = tado._http.request(req)
    log(f"       response: {result}", 4)
    # Http.request() has no raise_for_status — surface API errors explicitly
    if isinstance(result, dict) and result.get("errors"):
        log(f"[API ERROR] PUT {command} rejected: {result['errors']}")
    return result


def tado_get(tado: Tado, command: str):
    _api_stats["GET"] += 1
    log(f"[API]  GET {command}", 4)
    req    = TadoRequest(command=command, action=Action.GET, mode=Mode.OBJECT)
    result = tado._http.request(req)
    log(f"       response : {result}", 4)
    return result


def log_api_stats():
    total = _api_stats["GET"] + _api_stats["PUT"]
    log(f"[API] {total} calls ({_api_stats['GET']} GET, {_api_stats['PUT']} PUT)")

def save_api_stats():
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as _f:
            json.dump(_api_stats, _f)
    except Exception as _e:
        log(f"[WARN] Could not save api_stats: {_e}", 1)


def push_ha_sensors():
    """Push run stats to HA sensor entities. No-op outside HA (no SUPERVISOR_TOKEN)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return
    try:
        import requests as _req
    except ImportError:
        return

    ha_base  = "http://supervisor/core/api/states"
    sup_base = "http://supervisor"
    ha_hdr   = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    run_ts   = datetime.datetime.now().astimezone().isoformat()

    # --- Supervisor: version + update info ---
    try:
        sup_r    = _req.get(f"{sup_base}/addons/self/info", headers=ha_hdr, timeout=5)
        sup_data = sup_r.json().get("data", {}) if sup_r.ok else {}
    except Exception:
        sup_data = {}
    current_v  = sup_data.get("version", "?")
    latest_v   = sup_data.get("version_latest")
    update_av  = sup_data.get("update_available", False)

    sensors = [
        ("sensor.tado_planning_last_run", {
            "state": run_ts,
            "attributes": {"friendly_name": "Tado Planning — dernier run",
                           "device_class": "timestamp", "icon": "mdi:clock-check"},
        }),
        ("sensor.tado_planning_api_get_calls", {
            "state": str(_api_stats["GET"]),
            "attributes": {"friendly_name": "Tado Planning — appels API GET",
                           "unit_of_measurement": "calls", "icon": "mdi:download-network"},
        }),
        ("sensor.tado_planning_api_put_calls", {
            "state": str(_api_stats["PUT"]),
            "attributes": {"friendly_name": "Tado Planning — appels API PUT",
                           "unit_of_measurement": "calls", "icon": "mdi:upload-network"},
        }),
        ("sensor.tado_planning_version", {
            "state": current_v,
            "attributes": {"friendly_name": "Tado Planning — version installée",
                           "icon": "mdi:tag"},
        }),
        ("binary_sensor.tado_planning_update_available", {
            "state": "on" if update_av else "off",
            "attributes": {"friendly_name": "Tado Planning — update disponible",
                           "device_class": "update"},
        }),
    ]
    if latest_v:
        sensors.append(("sensor.tado_planning_latest_version", {
            "state": latest_v,
            "attributes": {"friendly_name": "Tado Planning — version disponible",
                           "icon": "mdi:tag-arrow-up"},
        }))
    if _last_put_time:
        sensors.append(("sensor.tado_planning_last_put", {
            "state": _last_put_time[0],
            "attributes": {"friendly_name": "Tado Planning — dernier PUT",
                           "device_class": "timestamp", "icon": "mdi:upload-network"},
        }))

    for entity_id, payload in sensors:
        try:
            r = _req.post(f"{ha_base}/{entity_id}", headers=ha_hdr,
                          json=payload, timeout=5)
            log(f"[HA] {entity_id} → HTTP {r.status_code}", 1)
        except Exception as e:
            log(f"[HA] Failed to update {entity_id}: {e}", 1)


# ---------------------------------------------------------------------------
# AUTHENTICATION
# ---------------------------------------------------------------------------

def get_tado_client() -> Tado:
    tado   = Tado(token_file_path=TOKEN_FILE)
    status = tado.device_activation_status()

    if status.value == "NOT_STARTED":
        log("[AUTH] Existing token detected, initialising...")
        tado._http._device_activation_status = DeviceActivationStatus.COMPLETED
        req         = TadoRequest()
        req.command = "me"
        req.action  = Action.GET
        req.domain  = Domain.ME
        req.mode    = Mode.OBJECT
        try:
            me = tado._http.request(req)
            if "homes" not in me:
                raise KeyError("homes")
            tado._http._id    = me["homes"][0]["id"]
            tado._http._x_api = False
        except Exception as e:
            log(f"[AUTH] Invalid or expired token ({e}), re-authenticating...")
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
            tado   = Tado(token_file_path=TOKEN_FILE)
            status = tado.device_activation_status()
            if status.value != "PENDING":
                log(f"[AUTH] Unexpected status after token reset: {status}")
                sys.exit(1)

    if tado.device_activation_status().value == "PENDING":
        global _auth_not_validated_logged
        _auth_not_validated_logged = False
        url = tado.device_verification_url()
        log(f"\n[AUTH] ╔══════════════════════════════════════════════════════╗")
        log(f"[AUTH] ║         FIRST CONNECTION REQUIRED                   ║")
        log(f"[AUTH] ╠══════════════════════════════════════════════════════╣")
        log(f"[AUTH] ║ Open this URL in your browser and log in with Tado: ║")
        log(f"[AUTH] ║                                                      ║")
        log(f"[AUTH] ║  {url}")
        log(f"[AUTH] ║                                                      ║")
        log(f"[AUTH] ║ The token will be saved automatically.              ║")
        log(f"[AUTH] ╚══════════════════════════════════════════════════════╝\n")
        log(f"[AUTH] URL: {url}")
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            pass
        log("[AUTH] Waiting for validation...")
        _retry = 0
        while True:
            try:
                tado.device_activation()
                break
            except Exception:
                if not _auth_not_validated_logged:
                    log("[AUTH] Not validated yet — retrying every 10s…")
                    _auth_not_validated_logged = True
                _retry += 1
                if _retry % 6 == 0:  # repeat URL every ~60s
                    log(f"[AUTH] URL: {url}")
                time.sleep(10)
                tado = Tado(token_file_path=TOKEN_FILE)

    log("[AUTH] Authentication successful.")
    return tado


# ---------------------------------------------------------------------------
# ZONE LOOKUP
# ---------------------------------------------------------------------------

def find_zones(tado: Tado, target_names: list) -> dict:
    all_zones = tado.get_zones()
    found = {}
    for zone in all_zones:
        zone_name_lower = zone["name"].lower().replace(" ", "_")
        zone_id         = zone["id"]
        for target in target_names:
            if target.lower() in zone_name_lower or zone_name_lower in target.lower():
                found[target] = zone_id
                log(f"[ZONES] '{zone['name']}' (ID={zone_id})", 1)
                break
    return found


# ---------------------------------------------------------------------------
# BLOCKS
# ---------------------------------------------------------------------------

def build_blocks(zone_cfg: dict) -> dict:
    tt = zone_cfg["timetable"]
    result = {}
    for day_key in TIMETABLE_REQUIRED_KEYS[tt]:
        api_day = DAY_KEY_TO_API[day_key]
        slots = zone_cfg[day_key]
        times = [s["start"] for s in slots]
        temps = [s["temp"]  for s in slots]
        ends  = times[1:] + ["00:00"]
        result[api_day] = [
            {
                "dayType": api_day,
                "start":   start,
                "end":     end,
                "geolocationOverride": False,
                "setting": {
                    "type":        "HEATING",
                    "power":       "ON",
                    "temperature": {"celsius": float(temp)}
                }
            }
            for start, end, temp in zip(times, ends, temps)
        ]
    return result


# ---------------------------------------------------------------------------
# COMPARISON + APPLICATION
# ---------------------------------------------------------------------------

def _blocks_equal(expected: list, received: list) -> bool:
    if len(expected) != len(received):
        return False
    for e, r in zip(expected, received):
        if e["start"] != r.get("start") or e["end"] != r.get("end"):
            return False
        e_temp = e["setting"]["temperature"]["celsius"]
        r_temp = r.get("setting", {}).get("temperature", {}).get("celsius")
        if r_temp is None or abs(e_temp - float(r_temp)) > 0.01:
            return False
    return True


def zone_needs_update(tado: Tado, zone_id: int, zone_cfg: dict, zone_key: str,
                      log_level: int = 1) -> bool:
    if "timetable" in zone_cfg:
        tt           = zone_cfg["timetable"]
        timetable_id = TIMETABLE_IDS[tt]
        active    = tado_get(tado, f"zones/{zone_id}/schedule/activeTimetable")
        active_id = active.get("id") if isinstance(active, dict) else None
        if active_id != timetable_id:
            log(f"[DIFF]  '{zone_key}' timetable: current={active_id}, wanted={timetable_id}", log_level)
            return True
        expected_blocks = build_blocks(zone_cfg)
        for day_type, exp_blocks in expected_blocks.items():
            result   = tado_get(tado, f"zones/{zone_id}/schedule/timetables/{timetable_id}/blocks/{day_type}")
            received = result if isinstance(result, list) else result.get("blocks", [])
            if not _blocks_equal(exp_blocks, received):
                log(f"[DIFF]  '{zone_key}' blocks {day_type} differ", log_level)
                return True

    if "early_start" in zone_cfg and "timetable" in zone_cfg:
        result = tado_get(tado, f"zones/{zone_id}/earlyStart")
        actual = result.get("enabled") if isinstance(result, dict) else None
        if actual != zone_cfg["early_start"]:
            log(f"[DIFF]  '{zone_key}' early_start: current={actual}, wanted={zone_cfg['early_start']}", log_level)
            return True

    if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
        preheat_map = {
            "off": "OFF", "eco": "ECO",
            "balance": "MEDIUM", "équilibre": "MEDIUM", "medium": "MEDIUM",
            "comfort": "COMFORT", "confort": "COMFORT",
        }
        away_disabled = zone_cfg.get("away_enabled") is False
        if away_disabled:
            preheat_level = "OFF"
        elif "timetable" in zone_cfg and "preheat" in zone_cfg:
            preheat_level = preheat_map.get(zone_cfg["preheat"].lower(), "ECO")
        else:
            preheat_level = None  # away-only zone: preserve existing, don't compare
        away_temp     = float(zone_cfg.get("away_temp", 15.0))
        result      = tado_get(tado, f"zones/{zone_id}/awayConfiguration")
        actual_t    = result.get("minimumAwayTemperature", {}).get("celsius") if isinstance(result, dict) else None
        actual_p    = result.get("preheatingLevel") if isinstance(result, dict) else None
        auto_adjust = result.get("autoAdjust", False) if isinstance(result, dict) else False
        log(f"[CHECK] '{zone_key}' away: cfg_preheat={zone_cfg.get('preheat','(missing)')}→{preheat_level} tado={actual_p}"
            f" | cfg_temp={zone_cfg.get('away_temp','(missing)')}→{away_temp} tado={actual_t}"
            + (f" | autoAdjust={auto_adjust}" if auto_adjust else ""), 1)
        if actual_t is None or abs(float(actual_t) - away_temp) > 0.01:
            log(f"[DIFF]  '{zone_key}' away_temp: current={actual_t}, wanted={away_temp}", log_level)
            return True
        if preheat_level is not None and actual_p != preheat_level:
            if zone_key in _preheat_unsupported:
                log(f"[SKIP]  '{zone_key}' preheat={preheat_level} non supporté par cette zone "
                    f"(Tado a refusé) — changez la config en ECO ou OFF.")
                # Don't return True — preheat mismatch is a hardware constraint, not a transient diff
            elif auto_adjust:
                log(f"[DIFF]  '{zone_key}' preheat: current={actual_p}, wanted={preheat_level}"
                    f" (autoAdjust=true — will be disabled on apply)", log_level)
                return True
            else:
                log(f"[DIFF]  '{zone_key}' preheat: current={actual_p}, wanted={preheat_level}", log_level)
                return True

    return False


def apply_zone_config(tado: Tado, zone_id: int, zone_key: str, zone_cfg: dict):
    if "timetable" in zone_cfg:
        tt           = zone_cfg["timetable"]
        timetable_id = TIMETABLE_IDS[tt]
        blocks_by_day = build_blocks(zone_cfg)
        tado_put(tado, f"zones/{zone_id}/schedule/activeTimetable", {"id": timetable_id})
        log(f"[OK]   '{zone_key}' timetable {tt} activated", 1)
        for day_type, day_blocks in blocks_by_day.items():
            log(f"[OK]   '{zone_key}' {day_type} → {len(day_blocks)} blocks", 1)
            for b in day_blocks:
                log(f"         {b['start']} → {b['end']} : "
                    f"{b['setting']['temperature']['celsius']}°C", 3)
            tado_put(tado, f"zones/{zone_id}/schedule/timetables/{timetable_id}/blocks/{day_type}",
                     day_blocks)

    if "early_start" in zone_cfg and "timetable" in zone_cfg:
        tado_put(tado, f"zones/{zone_id}/earlyStart", {"enabled": zone_cfg["early_start"]})
        log(f"[OK]   '{zone_key}' early_start: {zone_cfg['early_start']}", 1)

    if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
        preheat_map = {
            "off": "OFF", "eco": "ECO",
            "balance": "MEDIUM", "équilibre": "MEDIUM", "medium": "MEDIUM",
            "comfort": "COMFORT", "confort": "COMFORT",
        }
        away_disabled = zone_cfg.get("away_enabled") is False
        if away_disabled:
            preheat_level = "OFF"
        elif "timetable" in zone_cfg and "preheat" in zone_cfg:
            preheat_level = preheat_map.get(zone_cfg["preheat"].lower(), "ECO")
        else:
            preheat_level = None  # away-only zone: preserve existing, don't touch
        away_temp = zone_cfg.get("away_temp", 15.0)
        # Read existing config to log it and preserve the full temperature structure
        existing = tado_get(tado, f"zones/{zone_id}/awayConfiguration")
        log(f"[AWAY] '{zone_key}' GET  awayConfiguration: {existing}")

        # Build a clean payload — only the fields Tado accepts for PUT.
        ex = existing if isinstance(existing, dict) else {}
        payload: dict = {"type": ex.get("type", "HEATING")}
        if "comfortLevel" in ex:
            payload["comfortLevel"] = ex["comfortLevel"]
        payload["autoAdjust"] = False
        if preheat_level is not None:
            payload["preheatingLevel"] = preheat_level
        elif "preheatingLevel" in ex:
            payload["preheatingLevel"] = ex["preheatingLevel"]  # preserve for away-only zones
        if isinstance(ex.get("minimumAwayTemperature"), dict):
            mat = dict(ex["minimumAwayTemperature"])
            mat["celsius"] = float(away_temp)
            payload["minimumAwayTemperature"] = mat
        else:
            payload["minimumAwayTemperature"] = {"celsius": float(away_temp)}
        log(f"[AWAY] '{zone_key}' PUT  awayConfiguration: {payload}")
        result = tado_put(tado, f"zones/{zone_id}/awayConfiguration", payload)
        log(f"[AWAY] '{zone_key}' RESP awayConfiguration: {result}")
        if isinstance(result, dict) and any(
            e.get("code") == "typeMismatch" and "preheatingLevel" in e.get("title", "")
            for e in result.get("errors", [])
        ):
            log(f"[WARN] '{zone_key}' preheat={preheat_level} refusé par Tado — "
                f"cette zone ne supporte pas ce niveau. Changez la config en ECO ou OFF.")
            _preheat_unsupported.add(zone_key)


_AWAY_KEYS = ("away_temp", "away_enabled")

def merge_zone_configs(cfg_l1: dict, cfg_l2: dict | None) -> dict:
    """
    Merge level-1 and level-2 zone configs into a single virtual config.
    If cfg_l2 has a timetable: full override (all L2 keys replace L1).
    If cfg_l2 is away-only (no timetable): only merge away_temp and away_enabled
    — preheat, early_start, and schedule keys are preserved from L1.
    If cfg_l2 is None, returns a copy of cfg_l1 unchanged.
    """
    import copy
    merged = copy.deepcopy(cfg_l1)
    if cfg_l2:
        if "timetable" in cfg_l2:
            merged.update(copy.deepcopy(cfg_l2))
        else:
            for k in _AWAY_KEYS:
                if k in cfg_l2:
                    merged[k] = copy.deepcopy(cfg_l2[k])
    return merged


def apply_merged(tado: Tado,
                 zone_merged_map: dict[str, dict],
                 zone_l1_cfg_name: dict[str, str],
                 zone_l2_cfg_name: dict[str, str]):
    """
    Compare and apply a fully-merged (L1 + L2) config per zone in a single pass.

    zone_merged_map   : {zone_key: merged_cfg}
    zone_l1_cfg_name  : {zone_key: l1_config_name}   (for logging)
    zone_l2_cfg_name  : {zone_key: l2_config_name}   (for logging, may be absent)
    """
    zone_targets = list(zone_merged_map.keys())
    log(f"\n[APPLY] Merged config — {len(zone_targets)} zone(s)...")

    zones = find_zones(tado, zone_targets)
    if not zones:
        log("[ERROR] No zones found in Tado for the resolved configs!")
        sys.exit(1)

    updated = 0
    skipped = 0
    for zone_key, zone_id in zones.items():
        merged_cfg = zone_merged_map[zone_key]
        l1 = zone_l1_cfg_name.get(zone_key, "?")
        l2 = zone_l2_cfg_name.get(zone_key)
        label = f"L1={l1}" + (f" + L2={l2}" if l2 else "")
        if not zone_needs_update(tado, zone_id, merged_cfg, zone_key):
            log(f"[SKIP]  '{zone_key}' ({label}) — already compliant, no changes needed.")
            skipped += 1
            continue
        log(f"[UPDATE] '{zone_key}' ({label}) — applying changes...")
        apply_zone_config(tado, zone_id, zone_key, merged_cfg)
        updated += 1

    log(f"[✓] {updated} zone(s) updated, {skipped} unchanged.")

    # Verification pass — always log any remaining mismatches (log_level=0)
    log("\n[VERIFY] Re-reading from Tado...", 1)
    all_ok = True
    for zone_key, zone_id in zones.items():
        merged_cfg = zone_merged_map[zone_key]
        if zone_needs_update(tado, zone_id, merged_cfg, zone_key, log_level=0):
            log(f"[MISMATCH] '{zone_key}' still differs after apply — see diffs above.")
            all_ok = False
    if all_ok:
        log("[✓] Verification OK — all zones compliant.")
    log_api_stats()


# ---------------------------------------------------------------------------
# WEEKCONFIG SUMMARY
# ---------------------------------------------------------------------------

def print_config_summary(config_name: str, zone_cfg_map: dict, level: int):
    log(f"\n[CONFIG level {level}] '{config_name}'", 1)
    for zone, cfg in zone_cfg_map.items():
        log(f"  {zone}:", 1)
        if "timetable" in cfg:
            tt = cfg["timetable"]
            log(f"    Timetable  : {tt}", 1)
            for key in TIMETABLE_REQUIRED_KEYS.get(tt, []):
                if key in cfg:
                    log(f"    {key:12}: {[(s['start'], s['temp']) for s in cfg[key]]}", 1)
        if "early_start" in cfg:
            log(f"    Early start: {'enabled' if cfg['early_start'] else 'disabled'}", 1)
        if any(k in cfg for k in ("away_temp", "away_enabled", "preheat")):
            def _fmt(val, default, unit=""):
                return f"{val}{unit}" if val is not None else f"(not set → {default}{unit})"
            log(f"    Away       : {_fmt(cfg.get('away_temp'), 15.0, '°C')}, "
                f"preheat={_fmt(cfg.get('preheat'), 'ECO')}, "
                f"enabled={cfg.get('away_enabled', '(not set → True)')}", 1)
    log("", 1)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def cmd_tado_zones():
    """Read current zone configurations from Tado and print as JSON on stdout.
    All log/auth messages are redirected to stderr so stdout stays clean."""
    import sys as _sys
    _stdout, _sys.stdout = _sys.stdout, _sys.stderr   # redirect log() output to stderr

    _TT_ID_TO_NAME   = {0: "Mon-Sun", 1: "Mon-Fri, Sat, Sun", 2: "Mon, ..., Sun"}
    _TT_KEYS         = {
        "Mon-Sun":           ["Mon-Sun"],
        "Mon-Fri, Sat, Sun": ["Mon-Fri", "Sat", "Sun"],
        "Mon, ..., Sun":     ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }
    _PREHEAT_REVERSE = {"OFF": "off", "ECO": "ECO", "MEDIUM": "BALANCE", "COMFORT": "COMFORT"}

    tado      = get_tado_client()
    all_zones = tado.get_zones()
    result    = {}
    errors    = []

    for zone in all_zones:
        zid   = zone["id"]
        zname = zone["name"].lower().replace(" ", "_").replace(".", "").replace("-", "_")
        try:
            zcfg = {}
            active_tt = tado_get(tado, f"zones/{zid}/schedule/activeTimetable")
            tt_id     = active_tt.get("id") if isinstance(active_tt, dict) else None
            tt_name   = _TT_ID_TO_NAME.get(tt_id)
            if tt_name:
                zcfg["timetable"] = tt_name
                for day_key in _TT_KEYS[tt_name]:
                    api_day = DAY_KEY_TO_API[day_key]
                    raw     = tado_get(tado, f"zones/{zid}/schedule/timetables/{tt_id}/blocks/{api_day}")
                    blocks  = raw if isinstance(raw, list) else raw.get("blocks", [])
                    zcfg[day_key] = [
                        {"start": b["start"],
                         "temp":  b.get("setting", {}).get("temperature", {}).get("celsius")}
                        for b in blocks if b.get("setting", {}).get("power") == "ON"
                    ]
                es = tado_get(tado, f"zones/{zid}/earlyStart")
                zcfg["early_start"] = es.get("enabled", False) if isinstance(es, dict) else False
            away = tado_get(tado, f"zones/{zid}/awayConfiguration")
            if isinstance(away, dict):
                pl  = away.get("preheatingLevel", "ECO")
                mat = away.get("minimumAwayTemperature")
                zcfg["preheat"]      = _PREHEAT_REVERSE.get(pl, (pl or "ECO").lower())
                zcfg["away_temp"]    = mat.get("celsius") if isinstance(mat, dict) else None
                zcfg["away_enabled"] = pl != "OFF"
            result[zname] = zcfg
        except Exception as e:
            errors.append(f"{zname}: {e}")

    out = {"zones": result}
    if errors:
        out["errors"] = errors
    log_api_stats()
    _sys.stdout = _stdout          # restore stdout before printing JSON
    print(json.dumps(out))


def cmd_simulate(date_str: str | None = None):
    """Compute merged zone configs without connecting to Tado. Outputs JSON on stdout."""
    import sys as _sys
    import copy as _copy
    _stdout, _sys.stdout = _sys.stdout, _sys.stderr

    if date_str:
        try:
            now = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            _sys.stdout = _stdout
            print(json.dumps({"error": f"Invalid date '{date_str}'"}))
            return
    else:
        now = datetime.datetime.now()

    plannings, weekconfigs = load_data_files()
    if not validate_all(plannings, weekconfigs):
        _sys.stdout = _stdout
        print(json.dumps({"error": "Validation failed — check server logs"}))
        return

    active_pls = active_plannings_at(plannings, now)
    all_zones  = sorted({z for cfg in weekconfigs.values() for z in cfg.keys()})

    to_apply = {1: {}, 2: {}}
    for zone in all_zones:
        for level in (1, 2):
            cfg, pl_name = resolve_config_for_zone(zone, level, active_pls, now, weekconfigs)
            if cfg:
                to_apply[level].setdefault(cfg, []).append(zone)

    zone_merged_map  = {}
    zone_l1_cfg_name = {}
    zone_l2_cfg_name = {}
    _zone_raw        = {}   # {zone: (cfg_l1, cfg_l2, l1_name, l2_name)}

    for zone in all_zones:
        l1_cfg_name = next(
            (cfg for cfg, zs in to_apply[1].items() if zone in zs), None
        )
        if l1_cfg_name is None:
            continue
        zone_l1_cfg_name[zone] = l1_cfg_name
        cfg_l1 = weekconfigs[l1_cfg_name][zone]

        l2_cfg_name = next(
            (cfg for cfg, zs in to_apply[2].items() if zone in zs), None
        )
        cfg_l2 = weekconfigs[l2_cfg_name][zone] if l2_cfg_name else None
        if l2_cfg_name:
            zone_l2_cfg_name[zone] = l2_cfg_name

        zone_merged_map[zone] = merge_zone_configs(cfg_l1, cfg_l2)
        _zone_raw[zone] = (cfg_l1, cfg_l2, l1_cfg_name, l2_cfg_name)

    # Build per-field provenance: {zone: {field: {level, config}}}
    provenance = {}
    for zone, merged in zone_merged_map.items():
        cfg_l1, cfg_l2, l1_name, l2_name = _zone_raw[zone]
        prov = {}
        for key in merged:
            if cfg_l2 and "timetable" in cfg_l2:
                # full L2 override — field comes from L2 if present there, else L1
                if key in cfg_l2:
                    prov[key] = {"level": 2, "config": l2_name}
                else:
                    prov[key] = {"level": 1, "config": l1_name}
            elif cfg_l2 and key in _AWAY_KEYS and key in cfg_l2:
                # away-only L2 — only away keys come from L2
                prov[key] = {"level": 2, "config": l2_name}
            else:
                prov[key] = {"level": 1, "config": l1_name}
        provenance[zone] = prov

    iso_week = now.isocalendar()[1]
    out = {
        "zones":      zone_merged_map,
        "provenance": provenance,
        "meta": {
            "date":     now.strftime("%Y-%m-%d"),
            "weekday":  DAY_NAMES_EN[now.weekday()],
            "iso_week": iso_week,
            "plannings": [p["name"] for p in active_pls],
            "l1_map":   zone_l1_cfg_name,
            "l2_map":   zone_l2_cfg_name,
        },
    }
    _sys.stdout = _stdout
    print(json.dumps(out))


def main():
    global VERBOSITY

    parser = argparse.ArgumentParser(
        description="Applies Tado heating schedules from plannings.json + weekconfigs.json.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("-d", "--date", metavar="YYYY-MM-DD",
                        help="Simulate a specific date (also applies to --simulate)")
    parser.add_argument("--tado-zones", action="store_true",
                        help="Read current zone configs from Tado and output as JSON")
    parser.add_argument("--simulate", action="store_true",
                        help="Compute expected zone configs without connecting to Tado; output JSON")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help=("-v    : config zone details\n"
                              "-vv   : + cycle candidates\n"
                              "-vvv  : + blocks sent to API\n"
                              "-vvvv : + raw PUT/GET"))
    args = parser.parse_args()
    VERBOSITY = min(args.verbose, 4)

    if args.tado_zones:
        cmd_tado_zones()
        return

    if args.simulate:
        cmd_simulate(args.date)
        return

    if args.date:
        try:
            now = datetime.datetime.strptime(args.date, "%Y-%m-%d")
            log(f"[MODE] Simulated date: {now.strftime('%d/%m/%Y')}")
        except ValueError:
            log(f"[ERROR] Invalid date format '{args.date}' (expected YYYY-MM-DD)")
            sys.exit(1)
    else:
        now = datetime.datetime.now()

    iso_week = now.isocalendar()[1]
    parity   = "odd" if iso_week % 2 == 1 else "even"
    log(f"[INFO] {DAY_NAMES_EN[now.weekday()]} {now.strftime('%d/%m/%Y %H:%M')} "
        f"— ISO week #{iso_week} ({parity})")

    # Step 1 — load files
    log(f"[INFO] Loading {PLANNINGS_FILE}")
    log(f"[INFO] Loading {WEEKCONFIGS_FILE}")
    plannings, weekconfigs = load_data_files()

    # Step 2 — validate
    if not validate_all(plannings, weekconfigs):
        sys.exit(1)

    # Step 3 — find active plannings (by precedence)
    active_pls = active_plannings_at(plannings, now)
    log(f"[INFO] Active plannings : {[p['name'] for p in active_pls]}")

    # Step 4 — resolve per zone, per level
    all_zones = sorted({z for cfg in weekconfigs.values() for z in cfg.keys()})
    log(f"[INFO] Zones to process : {len(all_zones)}")

    # Collect unique (level, config) pairs to apply, noting which zones each covers
    # Structure: {level: {config_name: [zone, ...]}}
    to_apply = {1: {}, 2: {}}
    for zone in all_zones:
        for level in (1, 2):
            cfg, pl_name = resolve_config_for_zone(zone, level, active_pls, now, weekconfigs)
            if cfg:
                to_apply[level].setdefault(cfg, []).append(zone)
                log(f"[INFO] {zone} L{level} → {cfg} (from {pl_name})", 1)

    if not to_apply[1] and not to_apply[2]:
        log("[WARN] No configs resolved for any zone — nothing to apply.")

    # Summary at verbosity 1
    for level in (1, 2):
        for cfg in to_apply[level]:
            print_config_summary(cfg, weekconfigs[cfg], level)

    # Step 5 — build merged configs per zone (L1 overridden by L2 where present)
    zone_merged_map  = {}   # {zone: merged_cfg}
    zone_l1_cfg_name = {}   # {zone: cfg_name}
    zone_l2_cfg_name = {}   # {zone: cfg_name}

    for zone in all_zones:
        # Resolve L1 config for this zone
        l1_cfg_name = None
        for cfg, zones_for_cfg in to_apply[1].items():
            if zone in zones_for_cfg:
                l1_cfg_name = cfg
                break

        if l1_cfg_name is None:
            continue  # zone has no L1 → skip entirely

        zone_l1_cfg_name[zone] = l1_cfg_name
        cfg_l1 = weekconfigs[l1_cfg_name][zone]

        # Resolve optional L2 config for this zone
        l2_cfg_name = None
        for cfg, zones_for_cfg in to_apply[2].items():
            if zone in zones_for_cfg:
                l2_cfg_name = cfg
                break

        cfg_l2 = weekconfigs[l2_cfg_name][zone] if l2_cfg_name else None
        if l2_cfg_name:
            zone_l2_cfg_name[zone] = l2_cfg_name

        zone_merged_map[zone] = merge_zone_configs(cfg_l1, cfg_l2)

    if not zone_merged_map:
        log("[WARN] No configs resolved for any zone — nothing to apply.")
        return

    # Step 6 — connect and apply merged config in a single pass
    log("[TADO] Connecting...")
    tado      = get_tado_client()
    home_name = tado.get_me()["homes"][0]["name"]
    log(f"[TADO] Home: '{home_name}'")

    apply_merged(tado, zone_merged_map, zone_l1_cfg_name, zone_l2_cfg_name)
    save_api_stats()
    push_ha_sensors()


if __name__ == "__main__":
    main()
