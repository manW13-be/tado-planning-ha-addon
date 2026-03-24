# tado-planning

Gestion automatique des plannings de chauffage Tado selon un cycle de garde partagée (semaines paires/impaires), avec support de configurations superposées en niveaux.

---

## Concept

Le planning est organisé en **deux niveaux** appliqués successivement :

- **Niveau 1** — config principale selon le cycle de garde : `kidspresent` ou `kidsabsent`
- **Niveau 2** — surcharge partielle par-dessus le niveau 1 : `away_15deg`, `away_18deg`, etc.

Le niveau 2 ne touche que les zones qu'il définit. Le niveau 1 est toujours appliqué en entier en premier.

La sélection des configs repose sur un **cycle de deux semaines** (odd = impaire, even = paire). Le dernier événement passé dans le cycle détermine la config active. Si aucun événement n'est encore passé dans le cycle courant, le dernier événement du cycle précédent s'applique (wrap-around).

---

## Structure des fichiers

```
tado-shared-custody/
├── tado_planning.py          → script principal
├── schedules/
│   ├── planning.json         → définition des événements (niveaux 1 et 2)
│   ├── kidspresent.json      → weekconfig niveau 1
│   ├── kidsabsent.json       → weekconfig niveau 1
│   ├── vacancewithkids.json  → weekconfig niveau 1
│   ├── away_15deg.json       → weekconfig niveau 2
│   └── away_18deg.json       → weekconfig niveau 2
└── logs/
    ├── tado.log
    └── tado_error.log
```

---

## Format de planning.json

```json
{
  "_comment": "Cycle de 2 semaines : odd (impaire) et even (paire)",
  "events": [
    {
      "day": "friday",
      "time": "12:00",
      "week": "odd",
      "level": 1,
      "config": "kidsabsent"
    },
    {
      "day": "friday",
      "time": "12:00",
      "week": "even",
      "level": 1,
      "config": "kidspresent"
    },
    {
      "day": "tuesday",
      "time": "07:00",
      "week": "even",
      "level": 2,
      "config": "away_18deg"
    }
  ]
}
```

| Champ | Valeurs |
|-------|---------|
| `day` | `monday` `tuesday` `wednesday` `thursday` `friday` `saturday` `sunday` |
| `time` | `HH:MM` |
| `week` | `odd` `even` `both` |
| `level` | `1` `2` |
| `config` | nom du fichier weekconfig sans extension |

---

## Format d'un weekconfig

Chaque fichier weekconfig définit une ou plusieurs zones. Seules les zones présentes dans le fichier sont mises à jour lors de l'application.

```json
{
  "ch_lucas": {
    "timetable": "THREE_DAY",
    "week": [
      { "start": "00:00", "temp": 15 },
      { "start": "07:00", "temp": 19 },
      { "start": "22:00", "temp": 15 }
    ],
    "weekend": [
      { "start": "00:00", "temp": 15 },
      { "start": "08:00", "temp": 19 },
      { "start": "22:00", "temp": 15 }
    ],
    "away_temp": 15.0,
    "away_enabled": true,
    "preheat": "ECO",
    "early_start": true
  }
}
```

### Timetables disponibles

| Valeur | Description |
|--------|-------------|
| `ONE_DAY` | Un seul programme pour tous les jours (`MONDAY_TO_SUNDAY`) |
| `THREE_DAY` | Semaine (`MONDAY_TO_FRIDAY`) + samedi + dimanche |
| `SEVEN_DAY` | Un programme par jour — possibilité de surcharger des jours spécifiques avec `"monday": [...]` etc. |

### Champs optionnels

| Champ | Description |
|-------|-------------|
| `weekend` | Créneaux week-end (si absent, `week` s'applique aussi le week-end) |
| `away_temp` | Température minimale en mode absence (°C) |
| `away_enabled` | `true` / `false` — active ou désactive le mode absence |
| `preheat` | `off` `eco` `équilibre` `confort` |
| `early_start` | `true` / `false` — démarrage anticipé Tado |

---

## Utilisation

```bash
# Sélection automatique via planning.json
python3.11 tado_planning.py

# Forcer un fichier planning alternatif
python3.11 tado_planning.py -p schedules/monplanning.json

# Forcer un weekconfig directement (niveau 1 uniquement, ignore le planning)
python3.11 tado_planning.py -c schedules/vacancewithkids.json

# Simuler une date spécifique
python3.11 tado_planning.py -d 2026-03-10

# Verbosité
python3.11 tado_planning.py -v      # contenu des configs actives
python3.11 tado_planning.py -vv     # + candidats du cycle de sélection
python3.11 tado_planning.py -vvv    # + blocs envoyés à l'API
python3.11 tado_planning.py -vvvv   # + requêtes PUT/GET brutes
```

---

## Installation

### Prérequis

```bash
pip3.11 install "python-tado>=0.18"
```

### Authentification

À la première exécution, une URL s'affiche pour autoriser l'accès à votre compte Tado dans le navigateur. Le token est ensuite sauvegardé dans `~/.tado_refresh_token` et réutilisé automatiquement.

---

## Automatisation sur macOS (launchd)

Créer le fichier `~/Library/LaunchAgents/com.emmanuel.tado-planning.plist` :

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.emmanuel.tado-planning</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/python3.11</string>
        <string>/Users/emmanuel/Documents/TadoProject/tado-shared-custody-p1/tado_planning.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
    </array>
    <key>StandardOutPath</key>
    <string>/Users/emmanuel/Documents/TadoProject/tado-shared-custody-p1/logs/tado.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/emmanuel/Documents/TadoProject/tado-shared-custody-p1/logs/tado_error.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

Activer (une seule fois, persistant après redémarrage) :

```bash
mkdir -p ~/Documents/TadoProject/tado-shared-custody-p1/logs
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.emmanuel.tado-planning.plist
```

Tester immédiatement :

```bash
launchctl kickstart gui/$(id -u)/com.emmanuel.tado-planning
tail -f ~/Documents/TadoProject/tado-shared-custody-p1/logs/tado.log
```

Vérifier l'état :

```bash
launchctl list | grep tado
```

Désactiver :

```bash
launchctl bootout gui/$(id -u) com.emmanuel.tado-planning
```

---

## Automatisation sur Home Assistant OS (add-on local)

Structure de l'add-on dans `/config/addons/tado_planning/` :

```
addons/tado_planning/
├── config.json
├── Dockerfile
└── run.sh
```

**`config.json`**
```json
{
  "name": "Tado Planning",
  "version": "1.0.0",
  "slug": "tado_planning",
  "description": "Applique les plannings de chauffage Tado selon le cycle de garde",
  "arch": ["aarch64"],
  "startup": "application",
  "boot": "auto",
  "options": { "verbosity": 0 },
  "schema": { "verbosity": "int" },
  "map": ["config:rw"]
}
```

**`Dockerfile`**
```dockerfile
FROM python:3.11-slim
RUN pip install "python-tado>=0.18"
COPY run.sh /
RUN chmod +x /run.sh
CMD ["/run.sh"]
```

**`run.sh`**
```bash
#!/bin/bash
SCRIPT="/config/tado/tado_planning.py"
VERBOSITY=$(jq -r '.verbosity' /data/options.json)
VFLAG=""
if [ "$VERBOSITY" -gt 0 ]; then
    VFLAG=$(printf '%0.sv' $(seq 1 $VERBOSITY))
    VFLAG="-$VFLAG"
fi
while true; do
    echo "[TADO] Exécution à $(date '+%d/%m/%Y %H:%M')"
    python3 "$SCRIPT" $VFLAG
    sleep $(( 3600 - $(date +%s) % 3600 ))
done
```

Les fichiers `tado_planning.py` et `schedules/` sont placés dans `/config/tado/` sur le système HA, séparément du code de l'add-on.

---

## Dépendances

- Python 3.11+
- [python-tado](https://github.com/wmalgadey/PyTado) >= 0.18
