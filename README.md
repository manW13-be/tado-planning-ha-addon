# Tado Planning — HA Add-on

Automate your Tado heating schedules based on a **shared custody cycle** (alternating odd/even ISO weeks). Apply different heating profiles depending on whether children are present or absent, with optional partial overrides for specific zones.

---

## How it works

The add-on reads a `planning.json` file that defines **events** — each event says: *"on this weekday, at this time, during odd or even weeks, apply this heating config"*.

Two levels of config are supported:

- **Level 1** — full schedule applied to all defined zones (e.g. `kidspresent`, `kidsabsent`)
- **Level 2** — partial override applied on top of level 1, only for the zones it defines (e.g. `away_15deg` when you're at work)

The add-on runs every hour and automatically picks the correct config based on the current date and ISO week number.

### Example scenario

- **Odd weeks**: kids are away → apply `kidsabsent` (lower temperatures in kids' rooms)
- **Even weeks**: kids are home → apply `kidspresent` (normal temperatures)
- **Tuesday morning (even weeks)**: you leave for work → apply `away_18deg` override on your office
- **Tuesday midday (even weeks)**: you're back → apply `away_15deg` override (or remove it)

---

## Requirements

- Home Assistant OS (tested on HAOS 2026.x, aarch64/ODROID N2+)
- A Tado account with at least one zone configured
- Your Tado zone names must match the keys in your schedule JSON files

---

## Installation

### 1 — Add the repository to Home Assistant

In Home Assistant:

**Settings → Add-ons → Add-on Store → ⋮ (top right) → Repositories**

Add this URL:

```
https://github.com/manW13-be/tado-planning-ha-addon
```

Click **Add**, then refresh the page. **Tado Planning** will appear in the store.

### 2 — Install and build

Click **Tado Planning → Install**. The Docker image will build on your device (3–5 minutes on an ODROID N2+).

### 3 — Copy your files to Home Assistant

Before starting the add-on, copy your files to `/config/tado/` on your HA instance (accessible via Samba at `smb://homeassistant.local`):

```
/config/tado/
└── schedules/
    ├── planning.json
    ├── kidspresent.json
    ├── kidsabsent.json
    ├── away_15deg.json
    └── away_18deg.json
```

See the [Schedules format](#schedules-format) section below to create your own files.

### 4 — Copy the schedules to the add-on data directory

The add-on reads schedules from its internal `/data/schedules/` directory. You need to copy your files there once via SSH:

```bash
# Connect via SSH add-on, then:
mkdir -p /mnt/data/supervisor/addons/data/fc4e2b3e_tado_planning/schedules
cp /config/tado/schedules/*.json /mnt/data/supervisor/addons/data/fc4e2b3e_tado_planning/schedules/
```

> **Note:** The slug `fc4e2b3e_tado_planning` is the identifier HA assigns to this add-on. Verify it with `docker ps | grep tado` if needed.

### 5 — Authenticate with Tado (first run)

The add-on uses Tado's OAuth2 device flow. On first run, you need to generate and save a token manually via SSH:

```bash
docker exec -it addon_fc4e2b3e_tado_planning sh -c "timeout 120 python3 -c \"
from PyTado.interface.interface import Tado
import sys

t = Tado(token_file_path='/data/tado_refresh_token')
url = t._http._device_flow_data['verification_uri_complete']
print('Open this URL in your browser:', url)
sys.stdout.flush()
t.device_activation()
token = t.get_refresh_token()
import json
with open('/data/tado_refresh_token', 'w') as f:
    json.dump({'refresh_token': token}, f)
print('Token saved!')
\" 2>&1"
```

1. The command will print a URL — open it in your browser
2. Log in with your Tado account and authorize the device
3. Return to the terminal — it will complete automatically and save the token

Then copy the token to the persistent filesystem:

```bash
docker cp addon_fc4e2b3e_tado_planning:/data/tado_refresh_token \
  /mnt/data/supervisor/addons/data/fc4e2b3e_tado_planning/tado_refresh_token
```

### 6 — Start the add-on

In HA → **Settings → Add-ons → Tado Planning → Start**

Check the **Logs** tab to confirm it's running correctly.

---

## Schedules format

### planning.json

Defines the list of events that trigger config changes.

```json
{
  "events": [
    { "day": "friday",  "time": "12:00", "week": "odd",  "level": 1, "config": "kidsabsent"  },
    { "day": "friday",  "time": "12:00", "week": "even", "level": 1, "config": "kidspresent" },
    { "day": "tuesday", "time": "07:00", "week": "even", "level": 2, "config": "away_18deg"  },
    { "day": "tuesday", "time": "11:00", "week": "even", "level": 2, "config": "away_15deg"  }
  ]
}
```

| Field | Values | Description |
|-------|--------|-------------|
| `day` | `monday` … `sunday` | Day of the week |
| `time` | `HH:MM` | Time the event takes effect |
| `week` | `odd`, `even`, `both` | ISO week parity |
| `level` | `1`, `2` | Config level (1 = full, 2 = partial override) |
| `config` | filename without `.json` | Name of the schedule file to apply |

**Selection logic:** The add-on finds the last past event in the current odd+even cycle. If none is found, it wraps around to the previous cycle.

### Weekconfig files (kidspresent.json, kidsabsent.json, etc.)

Each file defines heating schedules per zone. Zone names must exactly match your Tado zone names.

```json
{
  "zones": {
    "living_room": {
      "timetable": "THREE_DAY",
      "slots": {
        "MONDAY_TO_FRIDAY": [
          {"start": "00:00", "end": "07:00", "temp": 15.0},
          {"start": "07:00", "end": "09:00", "temp": 20.0},
          {"start": "09:00", "end": "17:00", "temp": 18.0},
          {"start": "17:00", "end": "22:30", "temp": 20.0},
          {"start": "22:30", "end": "00:00", "temp": 15.0}
        ],
        "SATURDAY": [
          {"start": "00:00", "end": "08:00", "temp": 15.0},
          {"start": "08:00", "end": "23:00", "temp": 20.0},
          {"start": "23:00", "end": "00:00", "temp": 15.0}
        ],
        "SUNDAY": [
          {"start": "00:00", "end": "08:00", "temp": 15.0},
          {"start": "08:00", "end": "23:00", "temp": 20.0},
          {"start": "23:00", "end": "00:00", "temp": 15.0}
        ]
      },
      "away_temp": 15.0,
      "away_enabled": true,
      "preheat": "COMFORT",
      "early_start": true
    }
  }
}
```

| Field | Values | Description |
|-------|--------|-------------|
| `timetable` | `THREE_DAY` | Tado timetable type (THREE_DAY is the only supported type) |
| `slots` | object | Time slots per day group |
| `start` / `end` | `HH:MM` | Slot boundaries (must cover 00:00–00:00 completely) |
| `temp` | float | Target temperature in °C |
| `away_temp` | float | Temperature when Tado is in away mode |
| `away_enabled` | bool | Whether away mode is enabled for this zone |
| `preheat` | `ECO`, `COMFORT` | Preheat mode |
| `early_start` | bool | Whether early start is enabled |

**Level 2 files** only need to define the zones they override — all other zones keep their level 1 schedule.

### Finding your zone names

Your Tado zone names are visible in the Tado app. You can also retrieve them by running:

```bash
docker exec addon_fc4e2b3e_tado_planning python3 -c "
from PyTado.interface.interface import Tado
import json
t = Tado(token_file_path='/data/tado_refresh_token')
zones = t.getZones()
for z in zones:
    print(z['name'])
"
```

---

## Add-on options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `verbosity` | int | `0` | Log verbosity level (0–4) |

### Verbosity levels

| Level | What is shown |
|-------|---------------|
| `0` | Mode, ISO week, active configs, connection status, result |
| `1` (`-v`) | + Content of active configs (zones, slots, away, early start) |
| `2` (`-vv`) | + All cycle candidates with active marker and wrap-around info |
| `3` (`-vvv`) | + API blocks sent (start → end : temp) |
| `4` (`-vvvv`) | + Raw PUT/GET requests with payload and response |

---

## Standalone usage (without Home Assistant)

You can run the script directly on macOS or Linux:

```bash
pip3 install "python-tado>=0.18"

# Run with default planning.json
TADO_SCHEDULES_DIR=/path/to/schedules python3 tado_planning.py

# Force a specific planning file
TADO_SCHEDULES_DIR=/path/to/schedules python3 tado_planning.py -p planning.json

# Force a specific weekconfig (level 1 only)
TADO_SCHEDULES_DIR=/path/to/schedules python3 tado_planning.py -c kidspresent.json

# Simulate a specific date
TADO_SCHEDULES_DIR=/path/to/schedules python3 tado_planning.py -d 2026-03-10

# Verbose output
TADO_SCHEDULES_DIR=/path/to/schedules python3 tado_planning.py -vv
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TADO_TOKEN_FILE` | `/data/tado_refresh_token` | Path to the token file |
| `TADO_SCHEDULES_DIR` | `/data/schedules` | Path to the schedules directory |

### macOS launchd (run every hour)

Create `~/Library/LaunchAgents/com.yourname.tado-planning.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.yourname.tado-planning</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/tado_planning.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>TADO_SCHEDULES_DIR</key>
        <string>/path/to/schedules</string>
        <key>TADO_TOKEN_FILE</key>
        <string>/path/to/tado_refresh_token</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/path/to/logs/tado_planning.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/logs/tado_planning.err</string>
</dict>
</plist>
```

Load it:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.yourname.tado-planning.plist
```

---

## Project structure

```
tado-planning-ha-addon/
├── repository.json          # HA add-on repository metadata
├── config.json              # HA add-on configuration
├── Dockerfile               # Docker image definition
├── run.sh                   # Add-on startup script
├── tado_planning.py         # Main Python script
└── schedules/               # Example schedule files
    ├── planning.json        # Example planning (odd/even events)
    ├── kidspresent.json     # Example: kids at home
    ├── kidsabsent.json      # Example: kids away
    ├── away_15deg.json      # Example: level 2 override (15°)
    └── away_18deg.json      # Example: level 2 override (18°)
```

---

## License

MIT
