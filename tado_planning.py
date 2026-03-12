#!/usr/bin/env python3
"""
tado_planning.py
================
Gestion des plannings de chauffage Tado via fichiers JSON.

Structure des fichiers :
    schedules/
        planning.json          → liste d'événements avec level 1 et 2
        normalwithkids.json    → weekconfig (températures par pièce)
        normalwithoutkids.json
        away15.json
        away18.json
        ...

Utilisation :
    python3.11 tado_planning.py                                    # auto via planning.json
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

NOTE : À la première exécution, une URL s'affichera pour vous authentifier
       dans votre navigateur. Le token est ensuite sauvegardé dans
       ~/.tado_refresh_token pour les prochaines utilisations.
"""

import sys
import os
import json
import argparse
import webbrowser
import datetime

from PyTado.interface.interface import Tado
from PyTado.http import TadoRequest, Action, Mode

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
TOKEN_FILE    = os.environ.get("TADO_TOKEN_FILE",    "/data/tado_refresh_token")
SCHEDULES_DIR = os.environ.get("TADO_SCHEDULES_DIR", "/config/tado/schedules")

PLANNING_FILE = os.path.join(SCHEDULES_DIR, "planning.json")

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

# Niveau de verbosité global (0=défaut, 1=-v, 2=-vv, 3=-vvv, 4=-vvvv)
VERBOSITY = 0


def log(msg: str, level: int = 0):
    """Affiche un message si le niveau de verbosité est suffisant."""
    if VERBOSITY >= level:
        print(msg)


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
    """Charge un fichier weekconfig JSON et valide sa structure."""
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
    """Affiche le contenu d'un weekconfig — verbosité 1 (-v)."""
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
# SÉLECTION AUTOMATIQUE VIA planning.json
# ---------------------------------------------------------------------------

def _sort_key_for_event(event: dict) -> tuple:
    """
    Clé de tri pour ordonner les événements dans le cycle odd→even.
    week_order : 0 = odd, 1 = even (both traité comme odd pour le tri)
    """
    week_order = {"odd": 0, "even": 1, "both": 0}.get(event["week"].lower(), 0)
    day_offset = DAY_NAMES.get(event["day"].lower(), 0)
    h, m = map(int, event["time"].split(":"))
    return (week_order, day_offset, h, m)


def select_config_for_level(events: list, level: int, now: datetime.datetime) -> str | None:
    """
    Sélectionne la config active pour un niveau donné.

    Logique :
    - Filtrer les événements du niveau
    - Construire le cycle odd+even sur les deux semaines autour de 'now'
    - Trouver le dernier événement passé (avec wrap-around si nécessaire)
    - Retourner le nom de la config (sans extension)
    """
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

    def build_cycle(o_monday: datetime.datetime, e_monday: datetime.datetime) -> list:
        """Construit la liste triée de (datetime, week_type, config) pour un cycle odd+even."""
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

    # Affichage des candidats (-vv)
    log(f"\n[CANDIDATS niveau {level}] Cycle courant :", 2)
    for dt, week_type, config in current_cycle:
        past    = now >= dt
        pointer = " ◄ actif" if past else ""
        log(f"  {'✓' if past else '·'} {dt.strftime('%a %d/%m %H:%M')} ({week_type}) → {config}{pointer}", 2)

    # Chercher le dernier événement passé dans le cycle courant
    chosen_config = None
    chosen_dt     = None
    for dt, week_type, config in current_cycle:
        if now >= dt:
            chosen_config = config
            chosen_dt     = dt

    # Wrap-around : si aucun événement passé, prendre le dernier du cycle précédent
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
    """
    Retourne (config_level1, config_level2_or_None) depuis planning.json.
    """
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

    log(f"[INFO] Config niveau 1 active : {config_l1}")
    if config_l2:
        log(f"[INFO] Config niveau 2 active : {config_l2}")
    else:
        log(f"[INFO] Pas de config niveau 2 active")

    return config_l1, config_l2


def resolve_config_path(config_name: str) -> str:
    """Retourne le chemin complet d'un fichier config et vérifie son existence."""
    path = os.path.join(SCHEDULES_DIR, f"{config_name}.json")
    if not os.path.exists(path):
        log(f"[ERREUR] Fichier de config introuvable : {path}")
        log(f"[INFO]   Configs disponibles dans {SCHEDULES_DIR} :")
        for f in sorted(os.listdir(SCHEDULES_DIR)):
            if f.endswith(".json") and f != os.path.basename(PLANNING_FILE):
                log(f"           • {os.path.splitext(f)[0]}")
        sys.exit(1)
    return path


# ---------------------------------------------------------------------------
# CONSTRUCTION DES BLOCS API TADO
# ---------------------------------------------------------------------------

def time_to_tado(hhmm: str) -> str:
    return hhmm


def _make_blocks_for_day(day_type: str, slots: list) -> list:
    times = [s["start"] for s in slots]
    temps = [s["temp"]  for s in slots]
    ends  = times[1:] + ["00:00"]
    blocks = []
    for start, end, temp in zip(times, ends, temps):
        blocks.append({
            "dayType": day_type,
            "start": time_to_tado(start),
            "end":   time_to_tado(end),
            "geolocationOverride": False,
            "setting": {
                "type": "HEATING",
                "power": "ON",
                "temperature": {"celsius": float(temp)}
            }
        })
    return blocks


def build_blocks(zone_cfg: dict) -> dict:
    """
    Construit les blocs par dayType selon le timetable de la zone.
    Supporte ONE_DAY, THREE_DAY, SEVEN_DAY.
    """
    timetable = zone_cfg["timetable"]
    week      = zone_cfg["week"]
    weekend   = zone_cfg.get("weekend", week)

    if timetable == "ONE_DAY":
        return {
            "MONDAY_TO_SUNDAY": _make_blocks_for_day("MONDAY_TO_SUNDAY", week),
        }
    elif timetable == "THREE_DAY":
        return {
            "MONDAY_TO_FRIDAY": _make_blocks_for_day("MONDAY_TO_FRIDAY", week),
            "SATURDAY":         _make_blocks_for_day("SATURDAY",         weekend),
            "SUNDAY":           _make_blocks_for_day("SUNDAY",           weekend),
        }
    elif timetable == "SEVEN_DAY":
        day_types = ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"]
        slots_per_day = {
            "MONDAY": week, "TUESDAY": week, "WEDNESDAY": week,
            "THURSDAY": week, "FRIDAY": week,
            "SATURDAY": weekend, "SUNDAY": weekend,
        }
        for day in day_types:
            key = day.lower()
            if key in zone_cfg:
                slots_per_day[day] = zone_cfg[key]
        return {day: _make_blocks_for_day(day, slots_per_day[day]) for day in day_types}
    else:
        log(f"[ERREUR] Timetable inconnu : {timetable}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# AUTHENTIFICATION
# ---------------------------------------------------------------------------

def get_tado_client() -> Tado:
    tado   = Tado(token_file_path=TOKEN_FILE)
    status = tado.device_activation_status()

    if status.value == "PENDING":
        url = tado.device_verification_url()
        log(f"\n[AUTH] Première connexion requise.")
        log(f"[AUTH] Ouvrez cette URL dans votre navigateur :\n  {url}\n")
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            pass
        log("[AUTH] En attente de validation... (Ctrl+C pour annuler)")
        tado.device_activation()
        status = tado.device_activation_status()

    if status.value == "COMPLETED":
        log("[AUTH] Authentification réussie.")
    else:
        log(f"[AUTH] Statut inattendu : {status}")
        sys.exit(1)

    return tado


# ---------------------------------------------------------------------------
# RECHERCHE DES ZONES
# ---------------------------------------------------------------------------

def find_zones(tado: Tado, target_names: list) -> dict:
    """Retourne {nom_zone_weekconfig: zone_id} pour les noms correspondant aux cibles."""
    all_zones = tado.get_zones()
    found = {}
    for zone in all_zones:
        zone_name_lower = zone["name"].lower().replace(" ", "_")
        zone_id = zone["id"]
        for target in target_names:
            if target.lower() in zone_name_lower or zone_name_lower in target.lower():
                found[target] = zone_id
                log(f"[ZONES] Trouvée : '{zone['name']}' (ID={zone_id})", 1)
                break
    return found


# ---------------------------------------------------------------------------
# APPLICATION DU PLANNING
# ---------------------------------------------------------------------------

def tado_put(tado: Tado, command: str, payload):
    log(f"[API]  PUT {command}", 4)
    log(f"       payload : {json.dumps(payload, ensure_ascii=False)}", 4)
    req = TadoRequest(
        command=command,
        action=Action.CHANGE,
        payload=payload,
        mode=Mode.OBJECT,
    )
    result = tado._http.request(req)
    log(f"       réponse : {result}", 4)
    return result


def tado_get(tado: Tado, command: str):
    log(f"[API]  GET {command}", 4)
    req = TadoRequest(
        command=command,
        action=Action.GET,
        mode=Mode.OBJECT,
    )
    result = tado._http.request(req)
    log(f"       réponse : {result}", 4)
    return result


def verify_weekconfig(tado: Tado, zones: dict, weekconfig: dict, label: str = ""):
    """Relit le planning depuis Tado et compare avec ce qui a été envoyé."""
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
                    end   = b.get("end", "?")
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


def apply_weekconfig(tado: Tado, weekconfig: dict, config_name: str, level: int = 1):
    """Applique un weekconfig à toutes les zones qu'il définit."""
    zone_targets = [k for k in weekconfig.keys() if not k.startswith("_")]

    log(f"\n[APPLICATION niveau {level}] '{config_name}' — {len(zone_targets)} zone(s)...", 1)
    zones = find_zones(tado, zone_targets)

    if not zones:
        log(f"[ERREUR] Aucune zone du weekconfig niveau {level} trouvée dans Tado !")
        log(f"[INFO]   Zones cherchées : {zone_targets}")
        sys.exit(1)

    success_count = 0

    for zone_key, zone_id in zones.items():
        zone_cfg = weekconfig[zone_key]

        if "timetable" in zone_cfg and "week" in zone_cfg:
            timetable     = zone_cfg["timetable"]
            timetable_id  = TIMETABLE_IDS[timetable]
            blocks_by_day = build_blocks(zone_cfg)

            tado_put(tado,
                     f"zones/{zone_id}/schedule/activeTimetable",
                     {"id": timetable_id})
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
            tado_put(tado,
                     f"zones/{zone_id}/earlyStart",
                     {"enabled": zone_cfg["early_start"]})
            log(f"[OK]   '{zone_key}' early start : "
                f"{'activé' if zone_cfg['early_start'] else 'désactivé'}", 1)

        if any(k in zone_cfg for k in ("away_temp", "away_enabled", "preheat")):
            preheat_map = {
                "off":       "OFF",
                "eco":       "ECO",
                "équilibre": "BALANCE",
                "confort":   "COMFORT",
            }
            preheat_raw   = zone_cfg.get("preheat", "ECO").lower()
            preheat_level = preheat_map.get(preheat_raw, preheat_raw.upper())
            away_temp     = zone_cfg.get("away_temp", 15.0)
            away_enabled  = zone_cfg.get("away_enabled", True)

            if not away_enabled:
                preheat_level = "OFF"

            tado_put(tado, f"zones/{zone_id}/awayConfiguration", {
                "type": "HEATING",
                "preheatingLevel": preheat_level,
                "minimumAwayTemperature": {"celsius": float(away_temp)}
            })
            log(f"[OK]   '{zone_key}' away : {away_temp}°C, préchauffage : {preheat_level}"
                f"{' (désactivé)' if not away_enabled else ''}", 1)

        success_count += 1

    log(f"[✓] Niveau {level} '{config_name}' : {success_count}/{len(zones)} zones appliquées.")

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
    parser.add_argument(
        "-p", "--planning",
        metavar="planning.json",
        help="Fichier planning à utiliser (défaut : schedules/planning.json)"
    )
    parser.add_argument(
        "-c", "--config",
        metavar="weekconfig.json",
        help="Forcer un fichier weekconfig directement (ignore le planning, niveau 1 uniquement)"
    )
    parser.add_argument(
        "-d", "--date",
        metavar="YYYY-MM-DD",
        help="Simuler une date spécifique pour la sélection du planning (ex: 2026-03-10)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help=("-v    : contenu des configs actives\n"
              "-vv   : + candidats du cycle de sélection\n"
              "-vvv  : + blocs envoyés à l'API\n"
              "-vvvv : + requêtes PUT/GET brutes")
    )
    args = parser.parse_args()

    VERBOSITY = min(args.verbose, 4)

    if args.config and args.planning:
        log("[ERREUR] -p et -c sont mutuellement exclusifs.")
        parser.print_help()
        sys.exit(1)

    if args.config and args.date:
        log("[AVERTISSEMENT] -d est ignoré avec -c (weekconfig forcé).")

    # Résolution de la date simulée
    if args.date:
        try:
            sim_now = datetime.datetime.strptime(args.date, "%Y-%m-%d")
            log(f"[MODE] Date simulée : {sim_now.strftime('%d/%m/%Y')}")
        except ValueError:
            log(f"[ERREUR] Format de date invalide : '{args.date}' (attendu : YYYY-MM-DD)")
            sys.exit(1)
    else:
        sim_now = datetime.datetime.now()

    if args.config:
        # Mode -c : appliquer directement un weekconfig (niveau 1 uniquement)
        config_path = args.config
        if not config_path.endswith(".json"):
            config_path += ".json"
        log(f"[MODE] Weekconfig forcé : {config_path}")
        weekconfig_l1  = load_weekconfig(config_path)
        config_name_l1 = os.path.splitext(os.path.basename(config_path))[0]
        print_weekconfig_summary(config_path, weekconfig_l1, level=1)

        log("[TADO] Connexion...")
        tado      = get_tado_client()
        me        = tado.get_me()
        home_name = me["homes"][0]["name"]
        log(f"[TADO] Maison : '{home_name}'")

        apply_weekconfig(tado, weekconfig_l1, config_name_l1, level=1)

    else:
        # Mode auto ou -p : résolution via planning.json
        planning_file = args.planning if args.planning else PLANNING_FILE
        if args.planning:
            log(f"[MODE] Planning forcé : {planning_file}")
        else:
            log(f"[MODE] Planning par défaut : {planning_file}")

        config_name_l1, config_name_l2 = resolve_configs(planning_file, sim_now)

        config_path_l1 = resolve_config_path(config_name_l1)
        weekconfig_l1  = load_weekconfig(config_path_l1)
        print_weekconfig_summary(config_path_l1, weekconfig_l1, level=1)

        weekconfig_l2  = None
        config_path_l2 = None
        if config_name_l2:
            config_path_l2 = resolve_config_path(config_name_l2)
            weekconfig_l2  = load_weekconfig(config_path_l2)
            print_weekconfig_summary(config_path_l2, weekconfig_l2, level=2)

        log("[TADO] Connexion...")
        tado      = get_tado_client()
        me        = tado.get_me()
        home_name = me["homes"][0]["name"]
        log(f"[TADO] Maison : '{home_name}'")

        # Niveau 1 toujours appliqué en premier
        apply_weekconfig(tado, weekconfig_l1, config_name_l1, level=1)

        # Niveau 2 appliqué par-dessus si actif
        if weekconfig_l2 and config_name_l2:
            apply_weekconfig(tado, weekconfig_l2, config_name_l2, level=2)


if __name__ == "__main__":
    main()
