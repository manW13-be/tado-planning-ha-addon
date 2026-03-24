#!/usr/bin/env python3
"""
tado_planning.py
================
Gestion des plannings de chauffage Tado via fichiers JSON.

Structure des fichiers :
    schedules/
        planning_standard.json     → planning de base (odd/even, niveau 1 et 2)
        planning_vacances.json      → exception avec période fixe ou cycle odd/even
        planning_noel.json          → autre exception
        normalwithkids.json        → weekconfig (températures par pièce)
        normalwithoutkids.json
        away15.json
        away18.json
        ...

Format d'un fichier exception :
    {
        "_description": "Vacances de Pâques 2026",
        "period": {
            "start": "2026-04-05 00:00",
            "end":   "2026-04-19 23:59"
        },
        "events": [
            { "level": 1, "config": "awaywithkids", "week": "both", "day": "monday", "time": "00:00" }
        ]
    }

Utilisation :
    python3.11 tado_planning.py                                    # auto via planning_standard.json
    python3.11 tado_planning.py -p schedules/monplanning.json      # forcer un planning
    python3.11 tado_planning.py -c schedules/vacancewithkids.json  # forcer un weekconfig
    python3.11 tado_planning.py -d 2026-03-10                      # simuler une date
    python3.11 tado_planning.py -v                                 # contenu des configs actives
    python3.11 tado_planning.py -vv                                # + candidats du cycle
    python3.11 tado_planning.py -vvv                               # + blocs envoyés à l'API
    python3.11 tado_planning.py -vvvv                              # + requêtes PUT/GET brutes

Niveaux de verbosité :
    0 (défaut) : mode, semaine ISO, jour/heure, configs actives, connexion, résultat
    1 (-v)     : + contenu détaillé des configs chargées (zones, créneaux, away, early start)
    2 (-vv)    : + tous les candidats du cycle de sélection avec wrap-around
    3 (-vvv)   : + détail des blocs envoyés à l'API (start → end : temp)
    4 (-vvvv)  : + requêtes PUT/GET brutes avec payload et réponse

Prérequis :
    pip3.11 install "python-tado>=0.18"

NOTE : À la première exécution sur HA, l'URL d'authentification s'affiche dans les logs.
       Le token est ensuite sauvegardé dans /data/tado_refresh_token.
       Sur macOS, il est sauvegardé dans le même répertoire que le script.
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

TIMETABLE_IDS = {
    "ONE_DAY":   0,
    "THREE_DAY": 1,
    "SEVEN_DAY": 2,
}

DAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}

DAY_NAMES_FR = {
    0: "lundi", 1: "mardi", 2: "mercredi",
    3: "jeudi", 4: "vendredi", 5: "samedi", 6: "dimanche",
}

VERBOSITY = 0


def log(msg: str, level: int = 0):
    if VERBOSITY >= level:
        print(msg, flush=True)


# ---------------------------------------------------------------------------
# CHARGEMENT DES FICHIERS JSON
# ---------------------------------------------------------------------------

def load_json(path: str) -> dict:
    if not os.path.exists(path):
        log(f"[ERREUR] Fichier introuvable : {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_weekconfig(path: str) -> dict:
    data = load_json(path)
    for zone_name, zone_cfg in data.items():
        if zone_name.startswith("_"):
            continue
        if "timetable" in zone_cfg:
            if zone_cfg["timetable"] not in TIMETABLE_IDS:
                log(f"[ERREUR] '{zone_name}' : timetable '{zone_cfg['timetable']}' inconnu. "
                    f"Valeurs valides : {list(TIMETABLE_IDS.keys())}")
                sys.exit(1)
            if "week" not in zone_cfg:
                log(f"[ERREUR] '{zone_name}' : 'timetable' défini mais 'week' manquant.")
                sys.exit(1)
    return data


# ---------------------------------------------------------------------------
# AFFICHAGE DU CONTENU D'UN WEEKCONFIG (-v)
# ---------------------------------------------------------------------------

def print_weekconfig_summary(config_path: str, weekconfig: dict, level: int = 1):
    config_name = os.path.splitext(os.path.basename(config_path))[0]
    log(f"\n[CONFIG niveau {level}] '{config_name}'", 1)
    for zone_name, zone_cfg in weekconfig.items():
        if zone_name.startswith("_"):
            continue
        log(f"  {zone_name} :", 1)
        if "timetable" in zone_cfg:
            log(f"    Timetable  : {zone_cfg['timetable']}", 1)
            log(f"    Semaine    : {[(s['start'], s['temp']) for s in zone_cfg['week']]}", 1)
            if "weekend" in zone_cfg:
                log(f"    Week-end   : {[(s['start'], s['temp']) for s in zone_cfg['weekend']]}", 1)
        if "early_start" in zone_cfg:
            log(f"    Early start: {'activé' if zone_cfg['early_start'] else 'désactivé'}", 1)
        if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
            log(f"    Away       : {zone_cfg.get('away_temp', '?')}°C, "
                f"préchauffage={zone_cfg.get('preheat', '?')}, "
                f"activé={zone_cfg.get('away_enabled', True)}", 1)
    log("", 1)


# ---------------------------------------------------------------------------
# SÉLECTION AUTOMATIQUE VIA planning_standard.json
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

    log(f"\n[CANDIDATS niveau {level}] Cycle courant :", 2)
    for dt, week_type, config in current_cycle:
        past    = now >= dt
        pointer = " ◄ actif" if past else ""
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
            log(f"  → Wrap-around : aucun événement passé, "
                f"dernier du cycle précédent = {chosen_config} "
                f"({chosen_dt.strftime('%a %d/%m %H:%M')})", 2)
        else:
            chosen_config = current_cycle[0][2]
            chosen_dt     = current_cycle[0][0]

    log(f"  → Retenu : {chosen_config} "
        f"(depuis {chosen_dt.strftime('%a %d/%m %H:%M') if chosen_dt else '?'})", 2)

    return chosen_config


def resolve_configs(planning_file: str, now: datetime.datetime) -> tuple[str, str | None]:
    planning = load_json(planning_file)
    events   = planning.get("events", [])

    if not events:
        log(f"[ERREUR] Aucun événement dans {planning_file}")
        sys.exit(1)

    iso_week = now.isocalendar()[1]
    parity   = "impaire" if iso_week % 2 == 1 else "paire"
    week_key = "odd" if iso_week % 2 == 1 else "even"
    day_fr   = DAY_NAMES_FR[now.weekday()]

    log(f"[INFO] Semaine ISO n°{iso_week} ({parity}, {week_key})")
    log(f"[INFO] Nous sommes : {day_fr} {now.strftime('%d/%m/%Y à %H:%M')}")

    config_l1 = select_config_for_level(events, 1, now)
    config_l2 = select_config_for_level(events, 2, now)

    if config_l1 is None:
        log(f"[ERREUR] Aucun événement de niveau 1 dans {planning_file}")
        sys.exit(1)

    return config_l1, config_l2


def resolve_config_path(config_name: str) -> str:
    path = os.path.join(SCHEDULES_DIR, f"{config_name}.json")
    if not os.path.exists(path):
        log(f"[ERREUR] Fichier de config introuvable : {path}")
        log(f"[INFO]   Configs disponibles dans {SCHEDULES_DIR} :")
        for f in sorted(os.listdir(SCHEDULES_DIR)):
            if f.endswith(".json") and not os.path.basename(f).startswith("planning_"):
                log(f"           • {os.path.splitext(f)[0]}")
        sys.exit(1)
    return path


# ---------------------------------------------------------------------------
# GESTION DES EXCEPTIONS
# ---------------------------------------------------------------------------

def _parse_period_dt(s: str) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        log(f"[ERREUR] Format de date invalide dans une exception : '{s}' (attendu : YYYY-MM-DD HH:MM)")
        sys.exit(1)


def load_exception_plannings(now: datetime.datetime) -> dict[int, tuple[str, str, datetime.datetime]]:
    """
    Scanne tous les planning_*.json (sauf planning_standard.json) dans SCHEDULES_DIR.
    Pour chaque niveau (1 et 2), retourne la config active dont la période débute le plus tard.
    """
    pattern         = os.path.join(SCHEDULES_DIR, "planning_*.json")
    all_files       = sorted(glob.glob(pattern))
    exception_files = [
        f for f in all_files
        if os.path.basename(f) != "planning_standard.json"
    ]

    if not exception_files:
        return {}

    log(f"[EXCEPTIONS] {len(exception_files)} fichier(s) trouvé(s) : "
        f"{[os.path.basename(f) for f in exception_files]}", 1)

    candidates: dict[int, list] = {1: [], 2: []}

    for filepath in exception_files:
        filename = os.path.basename(filepath)
        data     = load_json(filepath)
        period   = data.get("period")
        events   = data.get("events", [])
        desc     = data.get("_description", filename)

        if not period:
            log(f"[EXCEPTION] '{filename}' ignoré : pas de clé 'period'.", 1)
            continue

        period_start = _parse_period_dt(period["start"])
        period_end   = _parse_period_dt(period["end"])

        if not (period_start <= now <= period_end):
            log(f"[EXCEPTION] '{filename}' hors période "
                f"({period['start']} → {period['end']}), ignoré.", 1)
            continue

        log(f"[EXCEPTION] '{filename}' ({desc}) — période active "
            f"({period['start']} → {period['end']})")

        for level in (1, 2):
            config = select_config_for_level(events, level, now)
            if config:
                candidates[level].append((period_start, config, filename))
                log(f"[EXCEPTION] '{filename}' niveau {level} → config '{config}'", 1)

    result: dict[int, tuple[str, str, datetime.datetime]] = {}

    for level in (1, 2):
        level_candidates = candidates[level]
        if not level_candidates:
            continue

        if len(level_candidates) > 1:
            names = [f"'{c[2]}'" for c in level_candidates]
            log(f"[WARNING] Chevauchement détecté (niveau {level}) entre "
                f"{', '.join(names)} — celle qui commence le plus tard est retenue.")

        level_candidates.sort(key=lambda x: x[0], reverse=True)
        chosen        = level_candidates[0]
        result[level] = (chosen[1], chosen[2], chosen[0])

    return result


# ---------------------------------------------------------------------------
# CONSTRUCTION DES BLOCS API TADO
# ---------------------------------------------------------------------------

def time_to_tado(hhmm: str) -> str:
    return hhmm


def _make_blocks_for_day(day_type: str, slots: list) -> list:
    times  = [s["start"] for s in slots]
    temps  = [s["temp"]  for s in slots]
    ends   = times[1:] + ["00:00"]
    blocks = []
    for start, end, temp in zip(times, ends, temps):
        blocks.append({
            "dayType": day_type,
            "start":   time_to_tado(start),
            "end":     time_to_tado(end),
            "geolocationOverride": False,
            "setting": {
                "type":        "HEATING",
                "power":       "ON",
                "temperature": {"celsius": float(temp)}
            }
        })
    return blocks


def build_blocks(zone_cfg: dict) -> dict:
    timetable = zone_cfg["timetable"]
    week      = zone_cfg["week"]
    weekend   = zone_cfg.get("weekend", week)

    if timetable == "ONE_DAY":
        return {"MONDAY_TO_SUNDAY": _make_blocks_for_day("MONDAY_TO_SUNDAY", week)}
    elif timetable == "THREE_DAY":
        return {
            "MONDAY_TO_FRIDAY": _make_blocks_for_day("MONDAY_TO_FRIDAY", week),
            "SATURDAY":         _make_blocks_for_day("SATURDAY",         weekend),
            "SUNDAY":           _make_blocks_for_day("SUNDAY",           weekend),
        }
    elif timetable == "SEVEN_DAY":
        day_types     = ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"]
        slots_per_day = {
            "MONDAY": week, "TUESDAY": week, "WEDNESDAY": week,
            "THURSDAY": week, "FRIDAY": week,
            "SATURDAY": weekend, "SUNDAY": weekend,
        }
        for day in day_types:
            if day.lower() in zone_cfg:
                slots_per_day[day] = zone_cfg[day.lower()]
        return {day: _make_blocks_for_day(day, slots_per_day[day]) for day in day_types}
    else:
        log(f"[ERREUR] Timetable inconnu : {timetable}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# AUTHENTIFICATION
# ---------------------------------------------------------------------------

def get_tado_client() -> Tado:
    """
    Crée et retourne un client Tado authentifié.

    Gère trois cas :
    - NOT_STARTED : token existant chargé depuis fichier, initialisation forcée de l'API
    - PENDING     : première connexion, affiche l'URL et attend la validation (avec polling)
    - COMPLETED   : token frais, connexion normale
    """
    tado   = Tado(token_file_path=TOKEN_FILE)
    status = tado.device_activation_status()

    if status.value == "NOT_STARTED":
        # Token chargé depuis fichier — PyTado ne met pas le statut à COMPLETED dans ce cas.
        # On force manuellement l'initialisation de l'API.
        log("[AUTH] Token existant détecté, initialisation...")
        tado._http._device_activation_status = DeviceActivationStatus.COMPLETED
        # Récupérer l'ID de la maison pour initialiser _id
        req          = TadoRequest()
        req.command  = "me"
        req.action   = Action.GET
        req.domain   = Domain.ME
        req.mode     = Mode.OBJECT
        me           = tado._http.request(req)
        tado._http._id   = me["homes"][0]["id"]
        tado._http._x_api = False  # Standard Tado (non X-line)

    elif status.value == "PENDING":
        # Première connexion : afficher l'URL et attendre la validation
        url = tado.device_verification_url()
        log(f"\n[AUTH] ╔══════════════════════════════════════════════════════╗")
        log(f"[AUTH] ║         PREMIÈRE CONNEXION REQUISE                  ║")
        log(f"[AUTH] ╠══════════════════════════════════════════════════════╣")
        log(f"[AUTH] ║ Ouvrez cette URL dans votre navigateur :            ║")
        log(f"[AUTH] ║                                                      ║")
        log(f"[AUTH] ║  {url}")
        log(f"[AUTH] ║                                                      ║")
        log(f"[AUTH] ║ Puis validez avec votre compte Tado.                ║")
        log(f"[AUTH] ║ Le token sera sauvegardé automatiquement.           ║")
        log(f"[AUTH] ╚══════════════════════════════════════════════════════╝\n")
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            pass

        log("[AUTH] En attente de validation...")
        while True:
            try:
                tado.device_activation()
                break
            except Exception as e:
                log(f"[AUTH] Pas encore validé, retry dans 10s... ({e})")
                time.sleep(10)
                tado = Tado(token_file_path=TOKEN_FILE)

    elif status.value == "COMPLETED":
        pass  # Token frais, tout va bien

    else:
        log(f"[AUTH] Statut inattendu : {status}")
        sys.exit(1)

    log("[AUTH] Authentification réussie.")
    return tado


# ---------------------------------------------------------------------------
# RECHERCHE DES ZONES
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
                log(f"[ZONES] Trouvée : '{zone['name']}' (ID={zone_id})", 1)
                break
    return found


# ---------------------------------------------------------------------------
# COMPARAISON AVANT APPLICATION
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
    if "timetable" in zone_cfg and "week" in zone_cfg:
        timetable    = zone_cfg["timetable"]
        timetable_id = TIMETABLE_IDS[timetable]

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
                log(f"[DIFF]   '{zone_key}' blocs {day_type} différents", 1)
                return True

    if "early_start" in zone_cfg:
        result = tado_get(tado, f"zones/{zone_id}/earlyStart")
        actual = result.get("enabled") if isinstance(result, dict) else None
        if actual != zone_cfg["early_start"]:
            log(f"[DIFF]   '{zone_key}' early_start : actif={actual}, voulu={zone_cfg['early_start']}", 1)
            return True

    if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
        preheat_map = {
            "off": "OFF", "eco": "ECO", "équilibre": "BALANCE", "confort": "COMFORT",
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
# APPLICATION DU PLANNING
# ---------------------------------------------------------------------------

def tado_put(tado: Tado, command: str, payload):
    log(f"[API]  PUT {command}", 4)
    log(f"       payload : {json.dumps(payload, ensure_ascii=False)}", 4)
    req    = TadoRequest(command=command, action=Action.CHANGE, payload=payload, mode=Mode.OBJECT)
    result = tado._http.request(req)
    log(f"       réponse : {result}", 4)
    return result


def tado_get(tado: Tado, command: str):
    log(f"[API]  GET {command}", 4)
    req    = TadoRequest(command=command, action=Action.GET, mode=Mode.OBJECT)
    result = tado._http.request(req)
    log(f"       réponse : {result}", 4)
    return result


def verify_weekconfig(tado: Tado, zones: dict, weekconfig: dict, label: str = ""):
    log(f"\n[VÉRIFICATION{' ' + label if label else ''}] Relecture depuis Tado...", 1)
    all_ok = True

    for zone_key, zone_id in zones.items():
        zone_cfg = weekconfig[zone_key]

        if "timetable" in zone_cfg and "week" in zone_cfg:
            timetable    = zone_cfg["timetable"]
            timetable_id = TIMETABLE_IDS[timetable]

            active    = tado_get(tado, f"zones/{zone_id}/schedule/activeTimetable")
            active_id = active.get("id") if isinstance(active, dict) else None
            if active_id != timetable_id:
                log(f"[DIFF] '{zone_key}' timetable actif : {active_id} (attendu {timetable_id})")
                all_ok = False
            else:
                log(f"[OK]   '{zone_key}' timetable actif : {timetable} ✓", 1)

            expected_blocks = build_blocks(zone_cfg)
            for day_type in expected_blocks:
                result   = tado_get(tado, f"zones/{zone_id}/schedule/timetables/{timetable_id}/blocks/{day_type}")
                received = result if isinstance(result, list) else result.get("blocks", [])
                log(f"\n  [{zone_key}] {day_type} — lu depuis Tado ({len(received)} blocs) :", 1)
                for b in received:
                    start = b.get("start", "?")
                    end   = b.get("end",   "?")
                    temp  = b.get("setting", {}).get("temperature", {}).get("celsius", "?")
                    log(f"    {start} → {end} : {temp}°C", 1)
                if len(received) != len(expected_blocks[day_type]):
                    log(f"  [DIFF] Blocs : reçu {len(received)}, envoyé {len(expected_blocks[day_type])}")
                    all_ok = False

        if "early_start" in zone_cfg:
            result = tado_get(tado, f"zones/{zone_id}/earlyStart")
            actual = result.get("enabled") if isinstance(result, dict) else None
            if actual != zone_cfg["early_start"]:
                log(f"[DIFF] '{zone_key}' early_start : {actual} (attendu {zone_cfg['early_start']})")
                all_ok = False
            else:
                log(f"[OK]   '{zone_key}' early_start : {actual} ✓", 1)

        if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
            result   = tado_get(tado, f"zones/{zone_id}/awayConfiguration")
            actual_t = result.get("minimumAwayTemperature", {}).get("celsius") if isinstance(result, dict) else None
            actual_p = result.get("preheatingLevel") if isinstance(result, dict) else None
            log(f"[OK]   '{zone_key}' away : {actual_t}°C, préchauffage : {actual_p} ✓", 1)

    if all_ok:
        log(f"[✓] Vérification OK — planning conforme.")
    else:
        log(f"[!] Des différences ont été détectées.")


def apply_weekconfig(tado: Tado, weekconfig: dict, config_name: str, level: int = 1,
                     source: str = "standard"):
    zone_targets = [k for k in weekconfig.keys() if not k.startswith("_")]

    log(f"\n[APPLICATION niveau {level}] '{config_name}' "
        f"(source: {source}) — {len(zone_targets)} zone(s)...")
    zones = find_zones(tado, zone_targets)

    if not zones:
        log(f"[ERREUR] Aucune zone du weekconfig niveau {level} trouvée dans Tado !")
        log(f"[INFO]   Zones cherchées : {zone_targets}")
        sys.exit(1)

    updated_count = 0
    skipped_count = 0

    for zone_key, zone_id in zones.items():
        zone_cfg = weekconfig[zone_key]

        log(f"[CHECK] '{zone_key}' — lecture config en place...", 1)

        if not zone_needs_update(tado, zone_id, zone_cfg, zone_key):
            log(f"[SKIP]  '{zone_key}' — déjà conforme, aucune modification.")
            skipped_count += 1
            continue

        log(f"[UPDATE] '{zone_key}' — mise à jour nécessaire.", 1)

        if "timetable" in zone_cfg and "week" in zone_cfg:
            timetable     = zone_cfg["timetable"]
            timetable_id  = TIMETABLE_IDS[timetable]
            blocks_by_day = build_blocks(zone_cfg)

            tado_put(tado, f"zones/{zone_id}/schedule/activeTimetable", {"id": timetable_id})
            log(f"[OK]   '{zone_key}' timetable {timetable} activé", 1)

            for day_type, day_blocks in blocks_by_day.items():
                log(f"[OK]   '{zone_key}' {day_type} → {len(day_blocks)} blocs", 1)
                for b in day_blocks:
                    log(f"         {b['start']} → {b['end']} : "
                        f"{b['setting']['temperature']['celsius']}°C", 3)
                tado_put(tado,
                         f"zones/{zone_id}/schedule/timetables/{timetable_id}/blocks/{day_type}",
                         day_blocks)

        if "early_start" in zone_cfg:
            tado_put(tado, f"zones/{zone_id}/earlyStart", {"enabled": zone_cfg["early_start"]})
            log(f"[OK]   '{zone_key}' early start : "
                f"{'activé' if zone_cfg['early_start'] else 'désactivé'}", 1)

        if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
            preheat_map = {
                "off": "OFF", "eco": "ECO", "équilibre": "BALANCE", "confort": "COMFORT",
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
            log(f"[OK]   '{zone_key}' away : {away_temp}°C, préchauffage : {preheat_level}"
                f"{' (désactivé)' if not away_enabled else ''}", 1)

        updated_count += 1

    log(f"[✓] Niveau {level} '{config_name}' : "
        f"{updated_count} zone(s) mise(s) à jour, {skipped_count} inchangée(s).")

    verify_weekconfig(tado, zones, weekconfig, label=f"niveau {level}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    global VERBOSITY

    parser = argparse.ArgumentParser(
        description="Applique un planning de chauffage Tado via fichiers JSON.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("-p", "--planning", metavar="planning.json",
                        help="Fichier planning à utiliser (défaut : planning_standard.json)")
    parser.add_argument("-c", "--config",   metavar="weekconfig.json",
                        help="Forcer un fichier weekconfig directement (niveau 1 uniquement)")
    parser.add_argument("-d", "--date",     metavar="YYYY-MM-DD",
                        help="Simuler une date spécifique (ex: 2026-04-10)")
    parser.add_argument("-v", "--verbose",  action="count", default=0,
                        help=("-v    : contenu des configs actives\n"
                              "-vv   : + candidats du cycle\n"
                              "-vvv  : + blocs envoyés à l'API\n"
                              "-vvvv : + requêtes PUT/GET brutes"))
    args = parser.parse_args()

    VERBOSITY = min(args.verbose, 4)

    if args.config and args.planning:
        log("[ERREUR] -p et -c sont mutuellement exclusifs.")
        parser.print_help()
        sys.exit(1)

    if args.config and args.date:
        log("[AVERTISSEMENT] -d est ignoré avec -c (weekconfig forcé).")

    if args.date:
        try:
            sim_now = datetime.datetime.strptime(args.date, "%Y-%m-%d")
            log(f"[MODE] Date simulée : {sim_now.strftime('%d/%m/%Y')}")
        except ValueError:
            log(f"[ERREUR] Format de date invalide : '{args.date}' (attendu : YYYY-MM-DD)")
            sys.exit(1)
    else:
        sim_now = datetime.datetime.now()

    # ------------------------------------------------------------------
    # Mode -c : weekconfig forcé
    # ------------------------------------------------------------------
    if args.config:
        config_path = args.config
        if not config_path.endswith(".json"):
            config_path += ".json"
        log(f"[MODE] Weekconfig forcé : {config_path}")
        weekconfig_l1  = load_weekconfig(config_path)
        config_name_l1 = os.path.splitext(os.path.basename(config_path))[0]
        print_weekconfig_summary(config_path, weekconfig_l1, level=1)

        log("[TADO] Connexion...")
        tado      = get_tado_client()
        home_name = tado.get_me()["homes"][0]["name"]
        log(f"[TADO] Maison : '{home_name}'")

        apply_weekconfig(tado, weekconfig_l1, config_name_l1, level=1, source="forcé")
        return

    # ------------------------------------------------------------------
    # Mode auto : planning_standard + exceptions
    # ------------------------------------------------------------------
    planning_file = args.planning if args.planning else PLANNING_STANDARD
    log(f"[MODE] Planning standard : {planning_file}")

    # 1. Résoudre le planning standard
    config_name_l1, config_name_l2 = resolve_configs(planning_file, sim_now)
    log(f"[INFO] Standard — niveau 1 : {config_name_l1}")
    log(f"[INFO] Standard — niveau 2 : {config_name_l2 or '(aucun)'}")

    # 2. Chercher les exceptions actives
    exceptions = load_exception_plannings(sim_now)

    # 3. Appliquer les exceptions par-dessus le standard
    final_l1_name   = config_name_l1
    final_l1_source = "standard"
    if 1 in exceptions:
        exc_config, exc_file, exc_start = exceptions[1]
        log(f"[INFO] Exception active (niveau 1) : '{exc_file}' → config '{exc_config}' "
            f"(depuis {exc_start.strftime('%d/%m/%Y %H:%M')})")
        final_l1_name   = exc_config
        final_l1_source = exc_file

    final_l2_name   = config_name_l2
    final_l2_source = "standard"
    if 2 in exceptions:
        exc_config, exc_file, exc_start = exceptions[2]
        log(f"[INFO] Exception active (niveau 2) : '{exc_file}' → config '{exc_config}' "
            f"(depuis {exc_start.strftime('%d/%m/%Y %H:%M')})")
        final_l2_name   = exc_config
        final_l2_source = exc_file

    # 4. Charger et afficher les weekconfigs finaux
    config_path_l1 = resolve_config_path(final_l1_name)
    weekconfig_l1  = load_weekconfig(config_path_l1)
    print_weekconfig_summary(config_path_l1, weekconfig_l1, level=1)

    weekconfig_l2  = None
    config_path_l2 = None
    if final_l2_name:
        config_path_l2 = resolve_config_path(final_l2_name)
        weekconfig_l2  = load_weekconfig(config_path_l2)
        print_weekconfig_summary(config_path_l2, weekconfig_l2, level=2)

    # 5. Connexion et application
    log("[TADO] Connexion...")
    tado      = get_tado_client()
    home_name = tado.get_me()["homes"][0]["name"]
    log(f"[TADO] Maison : '{home_name}'")

    apply_weekconfig(tado, weekconfig_l1, final_l1_name, level=1, source=final_l1_source)

    if weekconfig_l2 and final_l2_name:
        apply_weekconfig(tado, weekconfig_l2, final_l2_name, level=2, source=final_l2_source)


if __name__ == "__main__":
    main()
