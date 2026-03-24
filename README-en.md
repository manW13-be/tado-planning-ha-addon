# tado-planning

Automated Tado heating schedule management based on a shared custody cycle (odd/even weeks), with support for layered configuration overrides.

---

## Concept

The schedule is organized in **two levels** applied sequentially:

- **Level 1** — main config based on the custody cycle: `kidspresent` or `kidsabsent`
- **Level 2** — partial override on top of level 1: `away_15deg`, `away_18deg`, etc.

Level 2 only updates the zones it defines. Level 1 is always applied in full first.

Config selection is based on a **two-week cycle** (odd = ISO odd week, even = ISO even week). The last past event in the cycle determines the active config. If no event has occurred yet in the current cycle, the last event from the previous cycle applies (wrap-around).

---

## File Structure

```
tado-shared-custody/
├── tado_planning.py          → main script
├── schedules/
│   ├── planning.json         → event definitions (levels 1 and 2)
│   ├── kidspresent.json      → level 1 weekconfig
│   ├── kidsabsent.json       → level 1 weekconfig
│   ├── vacancewithkids.json  → level 1 weekconfig
│   ├── away_15deg.json       → level 2 weekconfig
│   └── away_18deg.json       → level 2 weekconfig
└── logs/
    ├── tado.log
    └── tado_error.log
```

---

## planning.json Format

```json
{
  "_comment": "2-week cycle: odd and even weeks",
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

| Field | Values |
|-------|--------|
| `day` | `monday` `tuesday` `wednesday` `thursday` `friday` `saturday` `sunday` |
| `time` | `HH:MM` |
| `week` | `odd` `even` `both` |
| `level` | `1` `2` |
| `config` | weekconfig filename without extension |

---

## Weekconfig Format

Each weekconfig file defines one or more zones. Only the zones present in the file are updated when applied.

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

### Available Timetables

| Value | Description |
|-------|-------------|
| `ONE_DAY` | Single schedule for all days (`MONDAY_TO_SUNDAY`) |
| `THREE_DAY` | Weekdays (`MONDAY_TO_FRIDAY`) + Saturday + Sunday |
| `SEVEN_DAY` | One schedule per day — specific days can be overridden with `"monday": [...]` etc. |

### Optional Fields

| Field | Description |
|-------|-------------|
| `weekend` | Weekend time slots (if absent, `week` also applies on weekends) |
| `away_temp` | Minimum temperature in away mode (°C) |
| `away_enabled` | `true` / `false` — enables or disables away mode |
| `preheat` | `off` `eco` `équilibre` `confort` |
| `early_start` | `true` / `false` — Tado early start feature |

---

## Usage

```bash
# Automatic selection via planning.json
python3.11 tado_planning.py

# Force an alternate planning file
python3.11 tado_planning.py -p schedules/myplanning.json

# Force a weekconfig directly (level 1 only, ignores planning)
python3.11 tado_planning.py -c schedules/vacancewithkids.json

# Simulate a specific date
python3.11 tado_planning.py -d 2026-03-10

# Verbosity
python3.11 tado_planning.py -v      # content of active configs
python3.11 tado_planning.py -vv     # + all candidates in the selection cycle
python3.11 tado_planning.py -vvv    # + blocks sent to the API
python3.11 tado_planning.py -vvvv   # + raw PUT/GET API requests
```

---

## Installation

### Requirements

```bash
pip3.11 install "python-tado>=0.18"
```

### Authentication

On the first run, a URL is displayed to authorize access to your Tado account in the browser. The token is then saved to `~/.tado_refresh_token` and reused automatically on subsequent runs.

---

## Automation on macOS (launchd)

Create the file `~/Library/LaunchAgents/com.emmanuel.tado-planning.plist`:

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

Enable (once — persists across reboots):

```bash
mkdir -p ~/Documents/TadoProject/tado-shared-custody-p1/logs
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.emmanuel.tado-planning.plist
```

Test immediately without waiting for the next hour:

```bash
launchctl kickstart gui/$(id -u)/com.emmanuel.tado-planning
tail -f ~/Documents/TadoProject/tado-shared-custody-p1/logs/tado.log
```

Check status:

```bash
launchctl list | grep tado
```

Disable:

```bash
launchctl bootout gui/$(id -u) com.emmanuel.tado-planning
```

---

## Automation on Home Assistant OS (local add-on)

Place the add-on in `/config/addons/tado_planning/`:

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
  "description": "Applies Tado heating schedules based on custody cycle",
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
    echo "[TADO] Running at $(date '+%d/%m/%Y %H:%M')"
    python3 "$SCRIPT" $VFLAG
    sleep $(( 3600 - $(date +%s) % 3600 ))
done
```

`tado_planning.py` and the `schedules/` folder are placed in `/config/tado/` on the HA filesystem, separately from the add-on code so they can be edited without rebuilding the image.

---

## Dependencies

- Python 3.11+
- [python-tado](https://github.com/wmalgadey/PyTado) >= 0.18
