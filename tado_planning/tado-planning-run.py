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

    if "away_temp" in cfg:
        try:
            float(cfg["away_temp"])
        except (TypeError, ValueError):
            errors.append(f"[VALIDATION] '{zone}': 'away_temp' must be a number")

    if "away_enabled" in cfg and not isinstance(cfg["away_enabled"], bool):
        errors.append(f"[VALIDATION] '{zone}': 'away_enabled' must be boolean")

    if "early_start" in cfg and not isinstance(cfg["early_start"], bool):
        errors.append(f"[VALIDATION] '{zone}': 'early_start' must be boolean")

    VALID_PREHEAT = {"off", "eco", "balance", "comfort", "équilibre", "confort"}
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
# PLANNING SELECTION
# ---------------------------------------------------------------------------

def _parse_dt_safe(s: str | None) -> datetime.datetime | None:
    if s is None:
        return None
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")


def select_planning(plannings: list, now: datetime.datetime) -> dict:
    """
    Select the active planning for the given datetime.
    Precedence rules:
        1. start present, start <= now → latest start wins;
                                         equal start → earliest end wins
        2. start absent, end present, end >= now → earliest end wins
        3. start absent, end absent → the standard (fallback)
    """
    group1 = []  # with start <= now
    group2 = []  # without start, with end >= now
    group3 = []  # without start, without end (standard)

    for p in plannings:
        start = _parse_dt_safe(p.get("start"))
        end   = _parse_dt_safe(p.get("end"))

        if start is not None:
            if start <= now:
                if end is None or end >= now:
                    group1.append(p)
        elif end is not None:
            if end >= now:
                group2.append(p)
        else:
            group3.append(p)

    if group1:
        # latest start wins; tie → earliest end wins (None = infinity, loses)
        def g1_sort(p):
            start = _parse_dt_safe(p["start"])
            end   = _parse_dt_safe(p.get("end"))
            end_key = end if end is not None else datetime.datetime.max
            return (-start.timestamp(), end_key.timestamp())
        group1.sort(key=g1_sort)
        chosen = group1[0]
        log(f"[PLANNING] Selected '{chosen['name']}' "
            f"(start={chosen.get('start')}, end={chosen.get('end') or '—'})")
        return chosen

    if group2:
        # earliest end wins
        group2.sort(key=lambda p: _parse_dt_safe(p["end"]))
        chosen = group2[0]
        log(f"[PLANNING] Selected '{chosen['name']}' "
            f"(no start, end={chosen.get('end')})")
        return chosen

    if group3:
        chosen = group3[0]
        log(f"[PLANNING] Selected '{chosen['name']}' (standard — no start, no end)")
        return chosen

    log("[ERROR] No active planning found for current date/time.")
    sys.exit(1)


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

def tado_put(tado: Tado, command: str, payload):
    log(f"[API]  PUT {command}", 4)
    log(f"       payload : {json.dumps(payload, ensure_ascii=False)}", 4)
    req    = TadoRequest(command=command, action=Action.CHANGE, payload=payload, mode=Mode.OBJECT)
    result = tado._http.request(req)
    log(f"       response: {result}", 4)
    return result


def tado_get(tado: Tado, command: str):
    log(f"[API]  GET {command}", 4)
    req    = TadoRequest(command=command, action=Action.GET, mode=Mode.OBJECT)
    result = tado._http.request(req)
    log(f"       response : {result}", 4)
    return result


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
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            pass
        log("[AUTH] Waiting for validation...")
        while True:
            try:
                tado.device_activation()
                break
            except Exception as ex:
                log(f"[AUTH] Not validated yet, retrying in 10s... ({ex})")
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


def zone_needs_update(tado: Tado, zone_id: int, zone_cfg: dict, zone_key: str) -> bool:
    if "timetable" in zone_cfg:
        tt           = zone_cfg["timetable"]
        timetable_id = TIMETABLE_IDS[tt]
        active    = tado_get(tado, f"zones/{zone_id}/schedule/activeTimetable")
        active_id = active.get("id") if isinstance(active, dict) else None
        if active_id != timetable_id:
            log(f"[DIFF]  '{zone_key}' timetable: current={active_id}, wanted={timetable_id}", 1)
            return True
        expected_blocks = build_blocks(zone_cfg)
        for day_type, exp_blocks in expected_blocks.items():
            result   = tado_get(tado, f"zones/{zone_id}/schedule/timetables/{timetable_id}/blocks/{day_type}")
            received = result if isinstance(result, list) else result.get("blocks", [])
            if not _blocks_equal(exp_blocks, received):
                log(f"[DIFF]  '{zone_key}' blocks {day_type} differ", 1)
                return True

    if "early_start" in zone_cfg:
        result = tado_get(tado, f"zones/{zone_id}/earlyStart")
        actual = result.get("enabled") if isinstance(result, dict) else None
        if actual != zone_cfg["early_start"]:
            log(f"[DIFF]  '{zone_key}' early_start: current={actual}, wanted={zone_cfg['early_start']}", 1)
            return True

    if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
        preheat_map = {
            "off": "OFF", "eco": "ECO",
            "équilibre": "BALANCE", "balance": "BALANCE",
            "confort": "COMFORT",   "comfort": "COMFORT",
        }
        preheat_level = preheat_map.get(zone_cfg.get("preheat", "eco").lower(), "ECO")
        away_temp     = float(zone_cfg.get("away_temp", 15.0))
        away_enabled  = zone_cfg.get("away_enabled", True)
        if not away_enabled:
            preheat_level = "OFF"
        result   = tado_get(tado, f"zones/{zone_id}/awayConfiguration")
        actual_t = result.get("minimumAwayTemperature", {}).get("celsius") if isinstance(result, dict) else None
        actual_p = result.get("preheatingLevel") if isinstance(result, dict) else None
        if actual_t is None or abs(float(actual_t) - away_temp) > 0.01:
            log(f"[DIFF]  '{zone_key}' away_temp: current={actual_t}, wanted={away_temp}", 1)
            return True
        if actual_p != preheat_level:
            log(f"[DIFF]  '{zone_key}' preheat: current={actual_p}, wanted={preheat_level}", 1)
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

    if "early_start" in zone_cfg:
        tado_put(tado, f"zones/{zone_id}/earlyStart", {"enabled": zone_cfg["early_start"]})
        log(f"[OK]   '{zone_key}' early_start: {zone_cfg['early_start']}", 1)

    if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
        preheat_map = {
            "off": "OFF", "eco": "ECO",
            "équilibre": "BALANCE", "balance": "BALANCE",
            "confort": "COMFORT",   "comfort": "COMFORT",
        }
        preheat_level = preheat_map.get(zone_cfg.get("preheat", "eco").lower(), "ECO")
        away_temp     = zone_cfg.get("away_temp", 15.0)
        away_enabled  = zone_cfg.get("away_enabled", True)
        if not away_enabled:
            preheat_level = "OFF"
        tado_put(tado, f"zones/{zone_id}/awayConfiguration", {
            "type":                    "HEATING",
            "preheatingLevel":         preheat_level,
            "minimumAwayTemperature":  {"celsius": float(away_temp)},
        })
        log(f"[OK]   '{zone_key}' away: {away_temp}°C preheat={preheat_level}", 1)


def apply_level(tado: Tado, level: int, config_name: str,
                weekconfigs: dict, planning_name: str):
    zone_cfg_map = weekconfigs[config_name]
    zone_targets = list(zone_cfg_map.keys())

    log(f"\n[APPLY level {level}] '{config_name}' "
        f"(planning: {planning_name}) — {len(zone_targets)} zone(s)...")

    zones = find_zones(tado, zone_targets)
    if not zones:
        log(f"[ERROR] No zones from level {level} config '{config_name}' found in Tado!")
        sys.exit(1)

    updated = 0
    skipped = 0
    for zone_key, zone_id in zones.items():
        zone_cfg = zone_cfg_map[zone_key]
        if not zone_needs_update(tado, zone_id, zone_cfg, zone_key):
            log(f"[SKIP]  '{zone_key}' — already compliant, no changes needed.")
            skipped += 1
            continue
        log(f"[UPDATE] '{zone_key}' — applying changes...")
        apply_zone_config(tado, zone_id, zone_key, zone_cfg)
        updated += 1

    log(f"[✓] Level {level} '{config_name}': {updated} zone(s) updated, {skipped} unchanged.")

    # Verification pass
    log(f"\n[VERIFY level {level}] Re-reading from Tado...", 1)
    all_ok = True
    for zone_key, zone_id in zones.items():
        zone_cfg = zone_cfg_map[zone_key]
        if zone_needs_update(tado, zone_id, zone_cfg, zone_key):
            log(f"[!] '{zone_key}' still differs after apply.")
            all_ok = False
    if all_ok:
        log(f"[✓] Verification OK — schedule is compliant.")


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
            log(f"    Away       : {cfg.get('away_temp', '?')}°C, "
                f"preheat={cfg.get('preheat', '?')}, "
                f"enabled={cfg.get('away_enabled', True)}", 1)
    log("", 1)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    global VERBOSITY

    parser = argparse.ArgumentParser(
        description="Applies Tado heating schedules from plannings.json + weekconfigs.json.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("-d", "--date", metavar="YYYY-MM-DD",
                        help="Simulate a specific date")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help=("-v    : config zone details\n"
                              "-vv   : + cycle candidates\n"
                              "-vvv  : + blocks sent to API\n"
                              "-vvvv : + raw PUT/GET"))
    args = parser.parse_args()
    VERBOSITY = min(args.verbose, 4)

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

    # Step 3 — select active planning
    active_planning = select_planning(plannings, now)
    planning_name   = active_planning["name"]
    cycle           = active_planning["cycle"]
    ref_date        = active_planning.get("ref_date")
    events          = active_planning.get("events", [])

    log(f"[INFO] Active planning : '{planning_name}' (cycle: {cycle})")

    # Step 4 — resolve configs per level
    config_l1, since_l1 = select_config_for_level(events, 1, now, cycle, ref_date)
    config_l2, since_l2 = select_config_for_level(events, 2, now, cycle, ref_date)

    if config_l1 is None:
        log(f"[ERROR] No level 1 config resolved from planning '{planning_name}'")
        sys.exit(1)

    since_l1_str = since_l1.strftime("%a %d/%m %H:%M") if since_l1 else "?"
    since_l2_str = since_l2.strftime("%a %d/%m %H:%M") if since_l2 else "?"
    log(f"[INFO] Level 1 config  : '{config_l1}' (since {since_l1_str})")
    if config_l2:
        log(f"[INFO] Level 2 config  : '{config_l2}' (since {since_l2_str})")
    else:
        log(f"[INFO] Level 2 config  : (none)")

    # Summary at verbosity 1
    print_config_summary(config_l1, weekconfigs[config_l1], 1)
    if config_l2:
        print_config_summary(config_l2, weekconfigs[config_l2], 2)

    # Step 5 — connect and apply
    log("[TADO] Connecting...")
    tado      = get_tado_client()
    home_name = tado.get_me()["homes"][0]["name"]
    log(f"[TADO] Home: '{home_name}'")

    apply_level(tado, 1, config_l1, weekconfigs, planning_name)

    if config_l2:
        apply_level(tado, 2, config_l2, weekconfigs, planning_name)


if __name__ == "__main__":
    main()
