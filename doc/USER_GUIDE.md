# User Guide — tado-planning

This guide covers how to install and configure tado-planning, both as a Home Assistant add-on and as a macOS companion script, including schedule file format and token setup.

---

## Prerequisites

- A Tado account with at least one heating zone configured
- Home Assistant OS running on an aarch64 device (tested on ODROID N2+)
- SSH access to your HA host (via the SSH add-on or terminal)
- For macOS: Python 3.11+ installed via Homebrew, and `jq`

---

## Part 1 — Home Assistant add-on

### 1.1 Add the repository

In Home Assistant:

1. Go to **Settings → Add-ons → Add-on Store**
2. Click the **⋮ menu** (top right) → **Repositories**
3. Add: `https://github.com/manW13-be/tado-planning-ha-addon`
4. **tado-planning** will appear in the store

### 1.2 Install

Click **tado-planning → Install**. The Docker image builds on your device (3–5 minutes on an ODROID N2+). Do **not** start it yet.

### 1.3 Place your schedule files

Before starting the add-on, copy your schedule files to HA via Samba (`smb://homeassistant.local/config`) or SSH:

```
/homeassistant/tado-planning/schedules/
├── planning.json
├── kidspresent.json
├── kidsabsent.json
├── away_15deg.json
└── away_18deg.json
```

See [Schedule file format](#schedule-file-format) below to create your own files.

### 1.4 Tado token setup on Home Assistant

The add-on authenticates with Tado using OAuth2 device flow. On first run you need to generate the token manually via SSH.

**Connect via SSH and run:**

```bash
docker exec -it addon_fc4e2b3e_tado_planning sh -c "timeout 120 python3 -c \"
from PyTado.interface.interface import Tado
import sys, json

t = Tado(token_file_path='/data/tado_refresh_token')
url = t._http._device_flow_data['verification_uri_complete']
print('Open this URL in your browser:', url)
sys.stdout.flush()
t.device_activation()
token = t.get_refresh_token()
with open('/data/tado_refresh_token', 'w') as f:
    json.dump({'refresh_token': token}, f)
print('Token saved!')
\" 2>&1"
```

1. A URL is printed — open it in your browser
2. Log in with your Tado account and authorize the device
3. The terminal will complete automatically and confirm the token is saved

**Then persist the token:**

```bash
docker cp addon_fc4e2b3e_tado_planning:/data/tado_refresh_token \
  /mnt/data/supervisor/addons/data/fc4e2b3e_tado_planning/tado_refresh_token
```

The token file survives restarts and updates. You only need to repeat this if the token expires or you run `ha_clean.sh` without `--keep-token`.

### 1.5 Configure verbosity (optional)

In the add-on **Configuration** tab, set `verbosity` (0–4). See [Verbosity levels](#verbosity-levels) below.

### 1.6 Start and verify

Start the add-on from the HA UI. Check the **Logs** tab, or via SSH:

```bash
ha apps logs fc4e2b3e_tado_planning
```

---

## Part 2 — macOS companion (launchd)

The macOS setup runs the same `tado_planning.py` script locally, triggered every hour via launchd.

### 2.1 Requirements

```bash
brew install python@3.11 jq
```

### 2.2 Clone the repository

```bash
git clone https://github.com/manW13-be/tado-planning-ha-addon.git tado-planning
cd tado-planning
```

### 2.3 Install the launchd agent

```bash
./scripts/install_launchd.sh
```

The script detects Python 3.11, displays all paths for confirmation, generates the plist, places it in `~/Library/LaunchAgents/com.tado-planning.plist`, and activates the agent.

### 2.4 Tado token setup on macOS

On first run, the script opens a browser URL for you to authorize Tado access. The token is then saved to `<project-root>/.tado_token` (gitignored, never committed) and reused automatically.

If authentication fails, run the script manually to see the error:

```bash
/opt/homebrew/bin/python3.11 tado_planning.py
```

### 2.5 Place your schedule files

```
<project-root>/schedules/
├── planning.json
├── kidspresent.json
├── kidsabsent.json
├── away_15deg.json
└── away_18deg.json
```

These are the same files as on HA — keep them in sync via GitHub.

### 2.6 Staying in sync with GitHub

Pull latest changes:
```bash
./scripts/mac_fetch.sh
```

Push local changes (bumps patch version automatically):
```bash
./scripts/mac_push.sh "your commit message"
```

### 2.7 Monitor and control

Follow logs in real time:
```bash
tail -f logs/tado.log logs/tado_error.log
```

Force an immediate run without waiting for the next hour:
```bash
launchctl kickstart -k gui/$(id -u)/com.tado-planning
```

Check service status:
```bash
launchctl list | grep tado
```

### 2.8 Uninstall

```bash
./scripts/uninstall_launchd.sh
```

Removes the launchd agent only — project files, schedules, logs and token are not touched.

---

## Schedule file format

### planning.json

Defines the list of events that trigger config changes. Each event says: *"on this weekday, at this time, during odd or even weeks, apply this config at this level"*.

```json
{
  "_comment": "2-week cycle: odd and even ISO weeks",
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
| `level` | `1`, `2` | Level 1 = full replace, Level 2 = partial override |
| `config` | filename without `.json` | Schedule file to apply |

**Selection logic:** The add-on finds the last past event in the current two-week cycle (odd + even). If none is found, it wraps around to the previous cycle.

### Weekconfig files

Each file defines heating schedules for one or more zones. Only the zones present in the file are updated — level 2 files typically define just one or two zones.

Zone names must exactly match your Tado zone names. To list your zones:

```bash
# On HA via SSH:
docker exec addon_fc4e2b3e_tado_planning python3 -c "
from PyTado.interface.interface import Tado
t = Tado(token_file_path='/data/tado_refresh_token')
for z in t.getZones(): print(z['name'])
"
```

**Example weekconfig:**

```json
{
  "living_room": {
    "timetable": "THREE_DAY",
    "week": [
      { "start": "00:00", "temp": 15 },
      { "start": "07:00", "temp": 20 },
      { "start": "22:30", "temp": 15 }
    ],
    "weekend": [
      { "start": "00:00", "temp": 15 },
      { "start": "08:00", "temp": 20 },
      { "start": "23:00", "temp": 15 }
    ],
    "away_temp": 15.0,
    "away_enabled": true,
    "preheat": "ECO",
    "early_start": true
  }
}
```

**Timetable types:**

| Value | Description |
|-------|-------------|
| `ONE_DAY` | Single schedule for all days (`MONDAY_TO_SUNDAY`) |
| `THREE_DAY` | Weekdays (`MONDAY_TO_FRIDAY`) + Saturday + Sunday |
| `SEVEN_DAY` | One schedule per day — specific days overridden with `"monday": [...]` etc. |

**Optional fields:**

| Field | Description |
|-------|-------------|
| `weekend` | Weekend slots (if absent, `week` also applies on weekends) |
| `away_temp` | Minimum temperature in away mode (°C) |
| `away_enabled` | `true` / `false` — enables or disables away mode |
| `preheat` | `OFF`, `ECO`, `BALANCE`, `COMFORT` |
| `early_start` | `true` / `false` — Tado early start feature |

---

## Verbosity levels

| Level | What is shown |
|-------|---------------|
| `0` | Mode, ISO week, active configs, result |
| `1` (`-v`) | + Content of active configs (zones, slots, away, early start) |
| `2` (`-vv`) | + All cycle candidates with active marker and wrap-around info |
| `3` (`-vvv`) | + API blocks sent (start → end : temp) |
| `4` (`-vvvv`) | + Raw PUT/GET requests with payload and response |

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Schedules not applied | Check logs for auth errors; verify schedule JSON files exist |
| Token expired | Delete the token file and re-run the OAuth setup |
| Wrong config applied | Simulate the date with `-d YYYY-MM-DD` and check with `-vv` |
| launchd agent not running | `launchctl list \| grep tado` — re-run `install_launchd.sh` if missing |
| HA add-on crash on start | Run `./scripts/ha_debug.sh -vv` from SSH for detailed output |
| Zone names not matching | Run the `getZones()` command above to list exact zone names |
