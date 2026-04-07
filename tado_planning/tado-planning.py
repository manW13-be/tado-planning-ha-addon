#!/usr/bin/env python3
"""
tado_planning.py
================
Manages Tado heating schedules via JSON files.

File structure:
    schedules/
        planning_standard.json     → base planning (odd/even weeks, level 1 and 2)
        planning_vacances.json      → exception with fixed period or odd/even cycle
        planning_noel.json          → another exception
        normalwithkids.json        → weekconfig (temperatures per room)
        normalwithoutkids.json
        away15.json
        away18.json
        ...

Weekconfig format — 3 timetable modes:

    Mode "Mon-Sun" (same schedule every day):
    {
        "ch_lucas": {
            "timetable": "Mon-Sun",
            "Mon-Sun": [ {"start": "00:00", "temp": 15}, ... ],
            "away_temp": 15.0, "away_enabled": false,
            "preheat": "off", "early_start": false
        }
    }

    Mode "Mon-Fri, Sat, Sun" (weekdays / Saturday / Sunday distinct):
    {
        "ch_lucas": {
            "timetable": "Mon-Fri, Sat, Sun",
            "Mon-Fri": [ {"start": "00:00", "temp": 15}, ... ],
            "Sat":     [ {"start": "00:00", "temp": 17}, ... ],
            "Sun":     [ {"start": "00:00", "temp": 17}, ... ],
            "away_temp": 15.0, "away_enabled": false,
            "preheat": "off", "early_start": false
        }
    }

    Mode "Mon, ..., Sun" (each day independent):
    {
        "ch_lucas": {
            "timetable": "Mon, ..., Sun",
            "Mon": [ ... ], "Tue": [ ... ], "Wed": [ ... ],
            "Thu": [ ... ], "Fri": [ ... ], "Sat": [ ... ], "Sun": [ ... ],
            "away_temp": 15.0, "away_enabled": false,
            "preheat": "off", "early_start": false
        }
    }

Exception file format:
    {
        "_description": "Easter holidays 2026",
        "period": {
            "start": "2026-04-05 00:00",
            "end":   "2026-04-19 23:59"
        },
        "events": [
            { "level": 1, "config": "awaywithkids", "week": "both", "day": "monday", "time": "00:00" }
        ]
    }

Usage:
    python3.11 tado_planning.py                                    # auto via planning_standard.json
    python3.11 tado_planning.py -p schedules/monplanning.json      # force a planning file
    python3.11 tado_planning.py -c schedules/vacancewithkids.json  # force a weekconfig
    python3.11 tado_planning.py -d 2026-03-10                      # simulate a date
    python3.11 tado_planning.py -v                                 # active config contents
    python3.11 tado_planning.py -vv                                # + cycle candidates
    python3.11 tado_planning.py -vvv                               # + blocks sent to API
    python3.11 tado_planning.py -vvvv                              # + raw PUT/GET requests

Verbosity levels:
    0 (default) : mode, ISO week, day/time, active configs, connection, result
    1 (-v)      : + detailed content of loaded configs (zones, slots, away, early start)
    2 (-vv)     : + all cycle selection candidates with wrap-around
    3 (-vvv)    : + detail of blocks sent to API (start → end : temp)
    4 (-vvvv)   : + raw PUT/GET requests with payload and response

Requirements:
    pip3.11 install "python-tado>=0.18"

NOTE: On first run on HA, the authentication URL is displayed in the logs.
      The token is then saved to /data/tado_refresh_token.
      On macOS, it is saved in the same directory as the script.
"""

import sys
import os
import json
import argparse
import webbrowser
import datetime
import platform
import glob
import time

from PyTado.interface.interface import Tado
from PyTado.http import TadoRequest, Action, Mode, Domain, DeviceActivationStatus

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if platform.system() == "Darwin":
    _DEFAULT_TOKEN_FILE    = os.path.join(_SCRIPT_DIR, "tado_refresh_token")
    _DEFAULT_SCHEDULES_DIR = os.path.join(_SCRIPT_DIR, "schedules")
else:  # Linux / HAOS
    _DEFAULT_TOKEN_FILE    = "/data/tado_refresh_token"
    _DEFAULT_SCHEDULES_DIR = "/data/schedules"

TOKEN_FILE    = os.environ.get("TADO_TOKEN_FILE",    _DEFAULT_TOKEN_FILE)
SCHEDULES_DIR = os.environ.get("TADO_SCHEDULES_DIR", _DEFAULT_SCHEDULES_DIR)

PLANNING_STANDARD = os.path.join(SCHEDULES_DIR, "planning_standard.json")

# ---------------------------------------------------------------------------
# TIMETABLE CONSTANTS
# ---------------------------------------------------------------------------

# Valid values for the "timetable" field in weekconfigs
TIMETABLE_MON_SUN        = "Mon-Sun"
TIMETABLE_MON_FRI_SAT_SUN = "Mon-Fri, Sat, Sun"
TIMETABLE_MON_TO_SUN     = "Mon, ..., Sun"

VALID_TIMETABLES = (TIMETABLE_MON_SUN, TIMETABLE_MON_FRI_SAT_SUN, TIMETABLE_MON_TO_SUN)

# Timetable → Tado API ID mapping
TIMETABLE_IDS = {
    TIMETABLE_MON_SUN:         0,
    TIMETABLE_MON_FRI_SAT_SUN: 1,
    TIMETABLE_MON_TO_SUN:      2,
}

# Expected slot keys in JSON per timetable
TIMETABLE_REQUIRED_KEYS = {
    TIMETABLE_MON_SUN:         ["Mon-Sun"],
    TIMETABLE_MON_FRI_SAT_SUN: ["Mon-Fri", "Sat", "Sun"],
    TIMETABLE_MON_TO_SUN:      ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}

# JSON key → Tado API dayType mapping
DAY_KEY_TO_API = {
    "Mon-Sun":  "MONDAY_TO_SUNDAY",
    "Mon-Fri":  "MONDAY_TO_FRIDAY",
    "Sat":      "SATURDAY",
    "Sun":      "SUNDAY",
    "Mon":      "MONDAY",
    "Tue":      "TUESDAY",
    "Wed":      "WEDNESDAY",
    "Thu":      "THURSDAY",
    "Fri":      "FRIDAY",
}

DAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}

DAY_NAMES_EN = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday",
    3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday",
}

VERBOSITY = 0


def log(msg: str, level: int = 0):
    if VERBOSITY >= level:
        print(msg, flush=True)


# ---------------------------------------------------------------------------
# SLOT VALIDATION
# ---------------------------------------------------------------------------

def _validate_slot(slot: object, zone_name: str, day_key: str, idx: int) -> str | None:
    """
    Validates a {start, temp} slot.
    Returns an error message or None if valid.
    """
    if not isinstance(slot, dict):
        return (f"[VALIDATION] '{zone_name}' / '{day_key}' slot #{idx}: "
                f"expected a dict object, got {type(slot).__name__}")
    for field in ("start", "temp"):
        if field not in slot:
            return (f"[VALIDATION] '{zone_name}' / '{day_key}' slot #{idx}: "
                    f"missing field '{field}'")
    start = slot["start"]
    if not isinstance(start, str) or len(start) != 5 or start[2] != ":":
        return (f"[VALIDATION] '{zone_name}' / '{day_key}' slot #{idx}: "
                f"'start' must be HH:MM format, got '{start}'")
    try:
        h, m = int(start[:2]), int(start[3:])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except ValueError:
        return (f"[VALIDATION] '{zone_name}' / '{day_key}' slot #{idx}: "
                f"invalid time '{start}'")
    try:
        float(slot["temp"])
    except (TypeError, ValueError):
        return (f"[VALIDATION] '{zone_name}' / '{day_key}' slot #{idx}: "
                f"'temp' must be a number, got '{slot['temp']}'")
    return None


# ---------------------------------------------------------------------------
# ZONE VALIDATION
# ---------------------------------------------------------------------------

def validate_zone(zone_name: str, zone_cfg: object) -> list[str]:
    """
    Validates a zone configuration.
    Returns a list of error messages (empty = valid).
    """
    errors = []

    if not isinstance(zone_cfg, dict):
        errors.append(f"[VALIDATION] '{zone_name}': expected a dict object, "
                      f"got {type(zone_cfg).__name__}")
        return errors

    # Optional meta fields — type check if present
    if "away_temp" in zone_cfg:
        try:
            float(zone_cfg["away_temp"])
        except (TypeError, ValueError):
            errors.append(f"[VALIDATION] '{zone_name}': 'away_temp' must be a number, "
                          f"got '{zone_cfg['away_temp']}'")

    if "away_enabled" in zone_cfg and not isinstance(zone_cfg["away_enabled"], bool):
        errors.append(f"[VALIDATION] '{zone_name}': 'away_enabled' must be a boolean, "
                      f"got '{zone_cfg['away_enabled']}'")

    if "early_start" in zone_cfg and not isinstance(zone_cfg["early_start"], bool):
        errors.append(f"[VALIDATION] '{zone_name}': 'early_start' must be a boolean, "
                      f"got '{zone_cfg['early_start']}'")

    VALID_PREHEAT = {"off", "eco", "équilibre", "confort", "balance", "comfort"}
    if "preheat" in zone_cfg:
        if zone_cfg["preheat"].lower() not in VALID_PREHEAT:
            errors.append(f"[VALIDATION] '{zone_name}': invalid 'preheat' value "
                          f"'{zone_cfg['preheat']}'. Valid values: {sorted(VALID_PREHEAT)}")

    # No timetable → "away only" zone (e.g. away_15deg.json) — not an error
    if "timetable" not in zone_cfg:
        return errors

    tt = zone_cfg["timetable"]
    if tt not in VALID_TIMETABLES:
        errors.append(f"[VALIDATION] '{zone_name}': unknown timetable '{tt}'. "
                      f"Valid values: {list(VALID_TIMETABLES)}")
        return errors  # no point validating keys if timetable is unknown

    required_keys = TIMETABLE_REQUIRED_KEYS[tt]

    # Check required keys are present
    for key in required_keys:
        if key not in zone_cfg:
            errors.append(f"[VALIDATION] '{zone_name}': missing key '{key}' "
                          f"for timetable '{tt}' (expected: {required_keys})")
        else:
            slots = zone_cfg[key]
            if not isinstance(slots, list):
                errors.append(f"[VALIDATION] '{zone_name}' / '{key}': "
                               f"expected a list, got {type(slots).__name__}")
            elif len(slots) == 0:
                errors.append(f"[VALIDATION] '{zone_name}' / '{key}': "
                               f"empty slot list")
            else:
                for i, slot in enumerate(slots):
                    err = _validate_slot(slot, zone_name, key, i)
                    if err:
                        errors.append(err)

    # Flag unexpected slot keys (belonging to another timetable)
    all_day_keys = {k for keys in TIMETABLE_REQUIRED_KEYS.values() for k in keys}
    meta_keys    = {"timetable", "away_temp", "away_enabled", "preheat", "early_start"}
    for key in zone_cfg:
        if key in all_day_keys and key not in required_keys:
            errors.append(f"[VALIDATION] '{zone_name}': unexpected key '{key}' "
                          f"for timetable '{tt}' (expected: {required_keys})")

    return errors


# ---------------------------------------------------------------------------
# JSON FILE LOADING
# ---------------------------------------------------------------------------

def load_json(path: str) -> dict:
    if not os.path.exists(path):
        log(f"[ERROR] File not found: {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_weekconfig(path: str) -> dict:
    """
    Loads a weekconfig file, validates each zone.
    Invalid zones are skipped with an error log.
    """
    data = load_json(path)
    config_name = os.path.splitext(os.path.basename(path))[0]
    valid_data  = {}
    skipped     = 0

    for zone_name, zone_cfg in data.items():
        if zone_name.startswith("_"):
            valid_data[zone_name] = zone_cfg
            continue

        errors = validate_zone(zone_name, zone_cfg)
        if errors:
            log(f"[WARN]  '{config_name}': zone '{zone_name}' skipped ({len(errors)} error(s)):")
            for err in errors:
                log(f"         {err}")
            skipped += 1
        else:
            valid_data[zone_name] = zone_cfg

    if skipped:
        log(f"[WARN]  '{config_name}': {skipped} zone(s) skipped out of {len(data)} total.")

    return valid_data


# ---------------------------------------------------------------------------
# WEEKCONFIG SUMMARY DISPLAY (-v)
# ---------------------------------------------------------------------------

def print_weekconfig_summary(config_path: str, weekconfig: dict, level: int = 1):
    config_name = os.path.splitext(os.path.basename(config_path))[0]
    log(f"\n[CONFIG level {level}] '{config_name}'", 1)
    for zone_name, zone_cfg in weekconfig.items():
        if zone_name.startswith("_"):
            continue
        log(f"  {zone_name}:", 1)
        if "timetable" in zone_cfg:
            tt = zone_cfg["timetable"]
            log(f"    Timetable  : {tt}", 1)
            for key in TIMETABLE_REQUIRED_KEYS.get(tt, []):
                if key in zone_cfg:
                    slots = zone_cfg[key]
                    log(f"    {key:12}: {[(s['start'], s['temp']) for s in slots]}", 1)
        if "early_start" in zone_cfg:
            log(f"    Early start: {'enabled' if zone_cfg['early_start'] else 'disabled'}", 1)
        if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
            log(f"    Away       : {zone_cfg.get('away_temp', '?')}°C, "
                f"preheat={zone_cfg.get('preheat', '?')}, "
                f"enabled={zone_cfg.get('away_enabled', True)}", 1)
    log("", 1)


# ---------------------------------------------------------------------------
# AUTOMATIC SELECTION VIA planning_standard.json
# ---------------------------------------------------------------------------

def _sort_key_for_event(event: dict) -> tuple:
    week_order = {"odd": 0, "even": 1, "both": 0}.get(event["week"].lower(), 0)
    day_offset = DAY_NAMES.get(event["day"].lower(), 0)
    h, m = map(int, event["time"].split(":"))
    return (week_order, day_offset, h, m)


def select_config_for_level(events: list, level: int, now: datetime.datetime) -> str | None:
    level_events = [e for e in events if e.get("level") == level]
    if not level_events:
        return None

    level_events_sorted = sorted(level_events, key=_sort_key_for_event)

    iso_week   = now.isocalendar()[1]
    is_odd_now = (iso_week % 2 == 1)

    current_monday = now - datetime.timedelta(days=now.weekday())
    current_monday = current_monday.replace(hour=0, minute=0, second=0, microsecond=0)

    if is_odd_now:
        odd_monday       = current_monday
        even_monday      = current_monday + datetime.timedelta(weeks=1)
        prev_odd_monday  = current_monday - datetime.timedelta(weeks=2)
        prev_even_monday = current_monday - datetime.timedelta(weeks=1)
    else:
        even_monday      = current_monday
        odd_monday       = current_monday + datetime.timedelta(weeks=1)
        prev_even_monday = current_monday - datetime.timedelta(weeks=2)
        prev_odd_monday  = current_monday - datetime.timedelta(weeks=1)

    def build_cycle(o_monday, e_monday):
        cycle = []
        for event in level_events_sorted:
            week       = event["week"].lower()
            day_offset = DAY_NAMES[event["day"].lower()]
            h, m       = map(int, event["time"].split(":"))
            if week in ("odd", "both"):
                dt = o_monday + datetime.timedelta(days=day_offset, hours=h, minutes=m)
                cycle.append((dt, "odd", event["config"]))
            if week in ("even", "both"):
                dt = e_monday + datetime.timedelta(days=day_offset, hours=h, minutes=m)
                cycle.append((dt, "even", event["config"]))
        cycle.sort(key=lambda x: x[0])
        return cycle

    current_cycle = build_cycle(odd_monday,     even_monday)
    prev_cycle    = build_cycle(prev_odd_monday, prev_even_monday)

    log(f"\n[CANDIDATES level {level}] Current cycle:", 2)
    for dt, week_type, config in current_cycle:
        past    = now >= dt
        pointer = " ◄ active" if past else ""
        log(f"  {'✓' if past else '·'} {dt.strftime('%a %d/%m %H:%M')} ({week_type}) → {config}{pointer}", 2)

    chosen_config = None
    chosen_dt     = None
    for dt, week_type, config in current_cycle:
        if now >= dt:
            chosen_config = config
            chosen_dt     = dt

    if chosen_config is None:
        if prev_cycle:
            chosen_config = prev_cycle[-1][2]
            chosen_dt     = prev_cycle[-1][0]
            log(f"  → Wrap-around: no past event found, "
                f"last from previous cycle = {chosen_config} "
                f"({chosen_dt.strftime('%a %d/%m %H:%M')})", 2)
        else:
            chosen_config = current_cycle[0][2]
            chosen_dt     = current_cycle[0][0]

    log(f"  → Selected: {chosen_config} "
        f"(since {chosen_dt.strftime('%a %d/%m %H:%M') if chosen_dt else '?'})", 2)

    return chosen_config


def resolve_configs(planning_file: str, now: datetime.datetime) -> tuple[str, str | None]:
    planning = load_json(planning_file)
    events   = planning.get("events", [])

    if not events:
        log(f"[ERROR] No events found in {planning_file}")
        sys.exit(1)

    iso_week = now.isocalendar()[1]
    parity   = "odd" if iso_week % 2 == 1 else "even"
    week_key = "odd" if iso_week % 2 == 1 else "even"
    day_en   = DAY_NAMES_EN[now.weekday()]

    log(f"[INFO] ISO week #{iso_week} ({parity}, {week_key})")
    log(f"[INFO] Current time: {day_en} {now.strftime('%d/%m/%Y %H:%M')}")

    config_l1 = select_config_for_level(events, 1, now)
    config_l2 = select_config_for_level(events, 2, now)

    if config_l1 is None:
        log(f"[ERROR] No level 1 events found in {planning_file}")
        sys.exit(1)

    return config_l1, config_l2


def resolve_config_path(config_name: str) -> str:
    path = os.path.join(SCHEDULES_DIR, f"{config_name}.json")
    if not os.path.exists(path):
        log(f"[ERROR] Config file not found: {path}")
        log(f"[INFO]   Available configs in {SCHEDULES_DIR}:")
        for f in sorted(os.listdir(SCHEDULES_DIR)):
            if f.endswith(".json") and not os.path.basename(f).startswith("planning_"):
                log(f"           - {os.path.splitext(f)[0]}")
        sys.exit(1)
    return path


# ---------------------------------------------------------------------------
# EXCEPTION PLANNING HANDLING
# ---------------------------------------------------------------------------

def _parse_period_dt(s: str) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        log(f"[ERROR] Invalid date format in exception: '{s}' (expected: YYYY-MM-DD HH:MM)")
        sys.exit(1)


def load_exception_plannings(now: datetime.datetime) -> dict[int, tuple[str, str, datetime.datetime]]:
    pattern         = os.path.join(SCHEDULES_DIR, "planning_*.json")
    all_files       = sorted(glob.glob(pattern))
    exception_files = [
        f for f in all_files
        if os.path.basename(f) != "planning_standard.json"
    ]

    if not exception_files:
        return {}

    log(f"[EXCEPTIONS] {len(exception_files)} file(s) found: "
        f"{[os.path.basename(f) for f in exception_files]}", 1)

    candidates: dict[int, list] = {1: [], 2: []}

    for filepath in exception_files:
        filename = os.path.basename(filepath)
        data     = load_json(filepath)
        period   = data.get("period")
        events   = data.get("events", [])
        desc     = data.get("_description", filename)

        if not period:
            log(f"[EXCEPTION] '{filename}' skipped: missing 'period' key.", 1)
            continue

        period_start = _parse_period_dt(period["start"])
        period_end   = _parse_period_dt(period["end"])

        if not (period_start <= now <= period_end):
            log(f"[EXCEPTION] '{filename}' outside period "
                f"({period['start']} → {period['end']}), skipped.", 1)
            continue

        log(f"[EXCEPTION] '{filename}' ({desc}) — active period "
            f"({period['start']} → {period['end']})")

        for level in (1, 2):
            config = select_config_for_level(events, level, now)
            if config:
                candidates[level].append((period_start, config, filename))
                log(f"[EXCEPTION] '{filename}' level {level} → config '{config}'", 1)

    result: dict[int, tuple[str, str, datetime.datetime]] = {}

    for level in (1, 2):
        level_candidates = candidates[level]
        if not level_candidates:
            continue

        if len(level_candidates) > 1:
            names = [f"'{c[2]}'" for c in level_candidates]
            log(f"[WARNING] Overlap detected (level {level}) between "
                f"{', '.join(names)} — the latest start time is kept.")

        level_candidates.sort(key=lambda x: x[0], reverse=True)
        chosen        = level_candidates[0]
        result[level] = (chosen[1], chosen[2], chosen[0])

    return result


# ---------------------------------------------------------------------------
# TADO API BLOCK BUILDING
# ---------------------------------------------------------------------------

def _make_blocks_for_day(day_type: str, slots: list) -> list:
    times  = [s["start"] for s in slots]
    temps  = [s["temp"]  for s in slots]
    ends   = times[1:] + ["00:00"]
    blocks = []
    for start, end, temp in zip(times, ends, temps):
        blocks.append({
            "dayType": day_type,
            "start":   start,
            "end":     end,
            "geolocationOverride": False,
            "setting": {
                "type":        "HEATING",
                "power":       "ON",
                "temperature": {"celsius": float(temp)}
            }
        })
    return blocks


def build_blocks(zone_cfg: dict) -> dict:
    """
    Builds Tado API blocks from a validated zone_cfg.
    Returns a dict { dayType: [blocks] }.
    """
    tt = zone_cfg["timetable"]
    result = {}
    for day_key in TIMETABLE_REQUIRED_KEYS[tt]:
        api_day = DAY_KEY_TO_API[day_key]
        result[api_day] = _make_blocks_for_day(api_day, zone_cfg[day_key])
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
        req          = TadoRequest()
        req.command  = "me"
        req.action   = Action.GET
        req.domain   = Domain.ME
        req.mode     = Mode.OBJECT
        try:
            me = tado._http.request(req)
            if "homes" not in me:
                raise KeyError("homes")
            tado._http._id    = me["homes"][0]["id"]
            tado._http._x_api = False
        except (KeyError, Exception) as e:
            log(f"[AUTH] Invalid or expired token ({e}), deleting and re-authenticating...")
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
            tado  = Tado(token_file_path=TOKEN_FILE)
            status = tado.device_activation_status()
            # Fall through to PENDING handling below
            if status.value == "PENDING":
                url = tado.device_verification_url()
                log(f"\n[AUTH] ╔══════════════════════════════════════════════════════╗")
                log(f"[AUTH] ║         FIRST CONNECTION REQUIRED                   ║")
                log(f"[AUTH] ╠══════════════════════════════════════════════════════╣")
                log(f"[AUTH] ║ Open this URL in your browser:                      ║")
                log(f"[AUTH] ║                                                      ║")
                log(f"[AUTH] ║  {url}")
                log(f"[AUTH] ║                                                      ║")
                log(f"[AUTH] ║ Then validate with your Tado account.               ║")
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
            else:
                log(f"[AUTH] Unexpected status after token reset: {status}")
                sys.exit(1)

    elif status.value == "PENDING":
        url = tado.device_verification_url()
        log(f"\n[AUTH] ╔══════════════════════════════════════════════════════╗")
        log(f"[AUTH] ║         FIRST CONNECTION REQUIRED                   ║")
        log(f"[AUTH] ╠══════════════════════════════════════════════════════╣")
        log(f"[AUTH] ║ Open this URL in your browser:                      ║")
        log(f"[AUTH] ║                                                      ║")
        log(f"[AUTH] ║  {url}")
        log(f"[AUTH] ║                                                      ║")
        log(f"[AUTH] ║ Then validate with your Tado account.               ║")
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
            except Exception as e:
                log(f"[AUTH] Not validated yet, retrying in 10s... ({e})")
                time.sleep(10)
                tado = Tado(token_file_path=TOKEN_FILE)

    elif status.value == "COMPLETED":
        pass

    else:
        log(f"[AUTH] Unexpected status: {status}")
        sys.exit(1)

    log("[AUTH] Authentication successful.")
    return tado


# ---------------------------------------------------------------------------
# ZONE LOOKUP
# ---------------------------------------------------------------------------

def find_zones(tado: Tado, target_names: list) -> dict:
    all_zones = tado.get_zones()
    found     = {}
    for zone in all_zones:
        zone_name_lower = zone["name"].lower().replace(" ", "_")
        zone_id         = zone["id"]
        for target in target_names:
            if target.lower() in zone_name_lower or zone_name_lower in target.lower():
                found[target] = zone_id
                log(f"[ZONES] Found: '{zone['name']}' (ID={zone_id})", 1)
                break
    return found


# ---------------------------------------------------------------------------
# PRE-APPLICATION COMPARISON
# ---------------------------------------------------------------------------

def _blocks_equal(expected: list, received: list) -> bool:
    if len(expected) != len(received):
        return False
    for e, r in zip(expected, received):
        if e["start"] != r.get("start"):
            return False
        if e["end"] != r.get("end"):
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
            log(f"[DIFF]   '{zone_key}' timetable : actif={active_id}, voulu={timetable_id}", 1)
            return True

        expected_blocks = build_blocks(zone_cfg)
        for day_type, exp_blocks in expected_blocks.items():
            result   = tado_get(tado, f"zones/{zone_id}/schedule/timetables/{timetable_id}/blocks/{day_type}")
            received = result if isinstance(result, list) else result.get("blocks", [])
            if not _blocks_equal(exp_blocks, received):
                log(f"[DIFF]   '{zone_key}' blocks {day_type} differ", 1)
                return True

    if "early_start" in zone_cfg:
        result = tado_get(tado, f"zones/{zone_id}/earlyStart")
        actual = result.get("enabled") if isinstance(result, dict) else None
        if actual != zone_cfg["early_start"]:
            log(f"[DIFF]   '{zone_key}' early_start : actif={actual}, voulu={zone_cfg['early_start']}", 1)
            return True

    if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
        preheat_map = {
            "off": "OFF", "eco": "ECO",
            "équilibre": "BALANCE", "balance": "BALANCE",
            "confort": "COMFORT",  "comfort": "COMFORT",
        }
        preheat_raw   = zone_cfg.get("preheat", "ECO").lower()
        preheat_level = preheat_map.get(preheat_raw, preheat_raw.upper())
        away_temp     = float(zone_cfg.get("away_temp", 15.0))
        away_enabled  = zone_cfg.get("away_enabled", True)
        if not away_enabled:
            preheat_level = "OFF"

        result   = tado_get(tado, f"zones/{zone_id}/awayConfiguration")
        actual_t = result.get("minimumAwayTemperature", {}).get("celsius") if isinstance(result, dict) else None
        actual_p = result.get("preheatingLevel") if isinstance(result, dict) else None

        if actual_t is None or abs(float(actual_t) - away_temp) > 0.01:
            log(f"[DIFF]   '{zone_key}' away_temp : actif={actual_t}, voulu={away_temp}", 1)
            return True
        if actual_p != preheat_level:
            log(f"[DIFF]   '{zone_key}' preheat : actif={actual_p}, voulu={preheat_level}", 1)
            return True

    return False


# ---------------------------------------------------------------------------
# PLANNING APPLICATION
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


def verify_weekconfig(tado: Tado, zones: dict, weekconfig: dict, label: str = ""):
    log(f"\n[VERIFY{' ' + label if label else ''}] Re-reading from Tado...", 1)
    all_ok = True

    for zone_key, zone_id in zones.items():
        zone_cfg = weekconfig[zone_key]

        if "timetable" in zone_cfg:
            tt           = zone_cfg["timetable"]
            timetable_id = TIMETABLE_IDS[tt]

            active    = tado_get(tado, f"zones/{zone_id}/schedule/activeTimetable")
            active_id = active.get("id") if isinstance(active, dict) else None
            if active_id != timetable_id:
                log(f"[DIFF] '{zone_key}' active timetable: {active_id} (expected {timetable_id})")
                all_ok = False
            else:
                log(f"[OK]   '{zone_key}' active timetable: {tt} ✓", 1)

            expected_blocks = build_blocks(zone_cfg)
            for day_type in expected_blocks:
                result   = tado_get(tado, f"zones/{zone_id}/schedule/timetables/{timetable_id}/blocks/{day_type}")
                received = result if isinstance(result, list) else result.get("blocks", [])
                log(f"\n  [{zone_key}] {day_type} — read from Tado ({len(received)} blocks):", 1)
                for b in received:
                    start = b.get("start", "?")
                    end   = b.get("end",   "?")
                    temp  = b.get("setting", {}).get("temperature", {}).get("celsius", "?")
                    log(f"    {start} → {end} : {temp}°C", 1)
                if len(received) != len(expected_blocks[day_type]):
                    log(f"  [DIFF] Blocks: received {len(received)}, sent {len(expected_blocks[day_type])}")
                    all_ok = False

        if "early_start" in zone_cfg:
            result = tado_get(tado, f"zones/{zone_id}/earlyStart")
            actual = result.get("enabled") if isinstance(result, dict) else None
            if actual != zone_cfg["early_start"]:
                log(f"[DIFF] '{zone_key}' early_start: {actual} (expected {zone_cfg['early_start']})")
                all_ok = False
            else:
                log(f"[OK]   '{zone_key}' early_start : {actual} ✓", 1)

        if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
            result   = tado_get(tado, f"zones/{zone_id}/awayConfiguration")
            actual_t = result.get("minimumAwayTemperature", {}).get("celsius") if isinstance(result, dict) else None
            actual_p = result.get("preheatingLevel") if isinstance(result, dict) else None
            log(f"[OK]   '{zone_key}' away: {actual_t}°C, preheat: {actual_p} ✓", 1)

    if all_ok:
        log(f"[✓] Verification OK — schedule is compliant.")
    else:
        log(f"[!] Differences detected.")


def apply_weekconfig(tado: Tado, weekconfig: dict, config_name: str, level: int = 1,
                     source: str = "standard"):
    zone_targets = [k for k in weekconfig.keys() if not k.startswith("_")]

    log(f"\n[APPLY level {level}] '{config_name}' "
        f"(source: {source}) — {len(zone_targets)} zone(s)...")
    zones = find_zones(tado, zone_targets)

    if not zones:
        log(f"[ERROR] No zones from level {level} weekconfig found in Tado!")
        log(f"[INFO]   Zones searched: {zone_targets}")
        sys.exit(1)

    updated_count = 0
    skipped_count = 0

    for zone_key, zone_id in zones.items():
        zone_cfg = weekconfig[zone_key]

        log(f"[CHECK] '{zone_key}' — reading current config...", 1)

        if not zone_needs_update(tado, zone_id, zone_cfg, zone_key):
            log(f"[SKIP]  '{zone_key}' — already compliant, no changes needed.")
            skipped_count += 1
            continue

        log(f"[UPDATE] '{zone_key}' — update required.")

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
                tado_put(tado,
                         f"zones/{zone_id}/schedule/timetables/{timetable_id}/blocks/{day_type}",
                         day_blocks)

        if "early_start" in zone_cfg:
            tado_put(tado, f"zones/{zone_id}/earlyStart", {"enabled": zone_cfg["early_start"]})
            log(f"[OK]   '{zone_key}' early start: "
                f"{'enabled' if zone_cfg['early_start'] else 'disabled'}", 1)

        if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
            preheat_map = {
                "off": "OFF", "eco": "ECO",
                "équilibre": "BALANCE", "balance": "BALANCE",
                "confort": "COMFORT",  "comfort": "COMFORT",
            }
            preheat_raw   = zone_cfg.get("preheat", "ECO").lower()
            preheat_level = preheat_map.get(preheat_raw, preheat_raw.upper())
            away_temp     = zone_cfg.get("away_temp", 15.0)
            away_enabled  = zone_cfg.get("away_enabled", True)
            if not away_enabled:
                preheat_level = "OFF"

            tado_put(tado, f"zones/{zone_id}/awayConfiguration", {
                "type":              "HEATING",
                "preheatingLevel":   preheat_level,
                "minimumAwayTemperature": {"celsius": float(away_temp)}
            })
            log(f"[OK]   '{zone_key}' away: {away_temp}°C, preheat: {preheat_level}"
                f"{' (disabled)' if not away_enabled else ''}", 1)

        updated_count += 1

    log(f"[✓] Level {level} '{config_name}': "
        f"{updated_count} zone(s) updated, {skipped_count} unchanged.")

    verify_weekconfig(tado, zones, weekconfig, label=f"level {level}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    global VERBOSITY

    parser = argparse.ArgumentParser(
        description="Applies a Tado heating schedule via JSON files.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("-p", "--planning", metavar="planning.json",
                        help="Planning file to use (default: planning_standard.json)")
    parser.add_argument("-c", "--config",   metavar="weekconfig.json",
                        help="Force a weekconfig file directly (level 1 only)")
    parser.add_argument("-d", "--date",     metavar="YYYY-MM-DD",
                        help="Simulate a specific date (e.g. 2026-04-10)")
    parser.add_argument("-v", "--verbose",  action="count", default=0,
                        help=("-v    : active config contents\n"
                              "-vv   : + cycle candidates\n"
                              "-vvv  : + blocks sent to API\n"
                              "-vvvv : + raw PUT/GET requests"))
    args = parser.parse_args()

    VERBOSITY = min(args.verbose, 4)

    if args.config and args.planning:
        log("[ERROR] -p and -c are mutually exclusive.")
        parser.print_help()
        sys.exit(1)

    if args.config and args.date:
        log("[WARNING] -d is ignored with -c (forced weekconfig).")

    if args.date:
        try:
            sim_now = datetime.datetime.strptime(args.date, "%Y-%m-%d")
            log(f"[MODE] Simulated date: {sim_now.strftime('%d/%m/%Y')}")
        except ValueError:
            log(f"[ERROR] Invalid date format: '{args.date}' (expected: YYYY-MM-DD)")
            sys.exit(1)
    else:
        sim_now = datetime.datetime.now()

    # ------------------------------------------------------------------
    # Mode -c: forced weekconfig
    # ------------------------------------------------------------------
    if args.config:
        config_path = args.config
        if not config_path.endswith(".json"):
            config_path += ".json"
        log(f"[MODE] Forced weekconfig: {config_path}")
        weekconfig_l1  = load_weekconfig(config_path)
        config_name_l1 = os.path.splitext(os.path.basename(config_path))[0]
        print_weekconfig_summary(config_path, weekconfig_l1, level=1)

        log("[TADO] Connexion...")
        tado      = get_tado_client()
        home_name = tado.get_me()["homes"][0]["name"]
        log(f"[TADO] Home: '{home_name}'")

        apply_weekconfig(tado, weekconfig_l1, config_name_l1, level=1, source="forced")
        return

    # ------------------------------------------------------------------
    # Auto mode: planning_standard + exceptions
    # ------------------------------------------------------------------
    planning_file = args.planning if args.planning else PLANNING_STANDARD
    log(f"[MODE] Standard planning: {planning_file}")

    config_name_l1, config_name_l2 = resolve_configs(planning_file, sim_now)
    log(f"[INFO] Standard — level 1: {config_name_l1}")
    log(f"[INFO] Standard — level 2: {config_name_l2 or '(none)'}")

    exceptions = load_exception_plannings(sim_now)

    final_l1_name   = config_name_l1
    final_l1_source = "standard"
    if 1 in exceptions:
        exc_config, exc_file, exc_start = exceptions[1]
        log(f"[INFO] Active exception (level 1): '{exc_file}' → config '{exc_config}' "
            f"(since {exc_start.strftime('%d/%m/%Y %H:%M')})")
        final_l1_name   = exc_config
        final_l1_source = exc_file

    final_l2_name   = config_name_l2
    final_l2_source = "standard"
    if 2 in exceptions:
        exc_config, exc_file, exc_start = exceptions[2]
        log(f"[INFO] Active exception (level 2): '{exc_file}' → config '{exc_config}' "
            f"(since {exc_start.strftime('%d/%m/%Y %H:%M')})")
        final_l2_name   = exc_config
        final_l2_source = exc_file

    config_path_l1 = resolve_config_path(final_l1_name)
    weekconfig_l1  = load_weekconfig(config_path_l1)
    print_weekconfig_summary(config_path_l1, weekconfig_l1, level=1)

    weekconfig_l2  = None
    config_path_l2 = None
    if final_l2_name:
        config_path_l2 = resolve_config_path(final_l2_name)
        weekconfig_l2  = load_weekconfig(config_path_l2)
        print_weekconfig_summary(config_path_l2, weekconfig_l2, level=2)

    log("[TADO] Connexion...")
    tado      = get_tado_client()
    home_name = tado.get_me()["homes"][0]["name"]
    log(f"[TADO] Maison : '{home_name}'")

    apply_weekconfig(tado, weekconfig_l1, final_l1_name, level=1, source=final_l1_source)

    if weekconfig_l2 and final_l2_name:
        apply_weekconfig(tado, weekconfig_l2, final_l2_name, level=2, source=final_l2_source)


if __name__ == "__main__":
    main()
