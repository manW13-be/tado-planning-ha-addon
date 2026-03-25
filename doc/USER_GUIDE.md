# User Guide — tado-planning

---

## Choosing your platform

tado-planning can run on **Home Assistant** (as an add-on) or on **macOS** (via launchd). Both platforms run the same script on the same hourly schedule and produce identical results.

> **Run it on one platform only.** Running both simultaneously would push conflicting configurations to Tado and double the API calls for no benefit.

Choose based on what is most convenient for you:
- **Home Assistant** — recommended if HA is always running and you want everything managed in one place
- **macOS** — useful if you don't have a permanent HA setup, or prefer managing the config from your Mac

---

## Key concepts

Before setting up, it helps to understand the two types of files you will be working with.

### Planning files

A **planning file** defines *when* to switch heating configurations. It contains a list of events, each specifying a day of the week, a time, and which Tado configuration to apply. Events are defined within a two-week cycle (odd/even ISO week).

There are two kinds of planning files:

- **`planning_standard.json`** — the baseline planning, always active
- **Exception plannings** (e.g. `planning_paques2026.json`) — define a specific date/time period during which they take precedence over the standard planning. Once the period ends, the standard planning resumes automatically.

### Tado configuration files (weekconfigs)

A **weekconfig file** defines *what* to apply to Tado: temperatures, time slots, timetable type, away mode settings, and preheat — for each zone it covers.

Weekconfig files are referenced by name in planning events. Only the zones listed in a weekconfig file are updated when it is applied.

### Configuration levels

Planning events carry a **level** (1 or 2):

- **Level 1** — defines the main heating configuration for a set of zones
- **Level 2** — defines its own configuration for a set of zones, independently of level 1

If level 1 and level 2 reference different zones, they are fully independent. If they reference the same zone, level 2 is applied on top of what level 1 has already set — modifying only the settings it specifies and leaving the rest intact.

### How the script selects the active configuration

Every hour, the script looks at the current date and time, finds the most recent past event in the current two-week cycle, and applies the corresponding weekconfig if it has changed. If no event has occurred yet in the current cycle, it wraps around to the last event of the previous cycle.

---

## Prerequisites

- A Tado account with at least one heating zone
- For Home Assistant: HAOS running on aarch64 (tested on ODROID N2+), SSH access
- For macOS: Python 3.11+ via Homebrew, `jq`

---

## Part 1 — Home Assistant

### Step 1 — Add the repository and install

1. Go to **Settings → Add-ons → Add-on Store**
2. Click **⋮** (top right) → **Repositories**
3. Add: `https://github.com/manW13-be/tado-planning-ha-addon`
4. Click **tado-planning → Install** — the Docker image builds on your device (3–5 min on ODROID N2+)
5. Do **not** start the add-on yet

### Step 2 — Initialize your schedule files

Connect via SSH and run:

```bash
cd /root/tado-planning
./scripts/init_schedules.sh
```

This copies `schedules.tmpl/` to `/config/tado-planning/schedules/` if it doesn't exist yet. The files are then accessible via Samba at `smb://homeassistant.local/config/tado-planning/schedules/`.

### Step 3 — List your Tado zones

```bash
./scripts/list_zones.sh
```

This connects to Tado, authenticates if needed, and lists all your zones with their names and IDs. Note the exact zone names — you will need them in your weekconfig files.

### Step 4 — Configure your schedule files

Edit the files in `/config/tado-planning/schedules/` (via Samba or SSH):

1. **`planning_standard.json`** — adapt the days and times to your actual custody handover schedule. The template includes level 1 and level 2 events as examples.
2. **`kidspresent.json`** and **`kidsabsent.json`** — set temperatures and time slots for each zone, using the zone names returned by `list_zones.sh`.
3. **`away_15deg.json`** and **`away_18deg.json`** — keep, adapt, or remove depending on whether your `planning_standard.json` references them.
4. **Exception plannings** — create additional `planning_*.json` files for school holidays or other periods as needed.

See [Schedule file format](#schedule-file-format) below for the full syntax.

### Step 5 — Generate the Tado token and verify

Run the add-on manually to authenticate and check that everything works:

```bash
./scripts/ha_debug.sh -vv
```

On first run, a URL is printed in the output — open it in your browser, log in with your Tado account, and authorize the device. The token is saved automatically and reused on all subsequent runs.

Check that the correct configurations are selected and that zones are updated as expected. Use `-vvv` for more detail on the API calls.

### Step 6 — Start the add-on

In HA: **Settings → Add-ons → tado-planning → Start**

Check the **Logs** tab to confirm normal operation. The add-on runs every hour and logs each execution.

To check logs via SSH:
```bash
ha apps logs fc4e2b3e_tado_planning
```

### Step 7 — Set verbosity (optional)

In the add-on **Configuration** tab, set `verbosity` to a value between 0 and 4. This controls how much detail is logged on each scheduled run. See [Verbosity levels](#verbosity-levels) below.

### Updating tado-planning on HA

When a new version is available, HA will show an **Update** button in the add-on page. Click it — HA pulls the new version from GitHub and rebuilds the image automatically.

---

## Part 2 — macOS

### Step 1 — Install prerequisites and clone

```bash
brew install python@3.11 jq
git clone https://github.com/manW13-be/tado-planning-ha-addon.git tado-planning
cd tado-planning
```

### Step 2 — Initialize your schedule files

```bash
./scripts/init_schedules.sh
```

This copies `schedules.tmpl/` to `schedules/` if it doesn't exist yet.

### Step 3 — List your Tado zones

```bash
./scripts/list_zones.sh
```

On first run this will open a browser tab to authenticate with Tado. The token is saved to `tado_refresh_token` in the project root and reused automatically.

Note the exact zone names — you will need them in your weekconfig files.

### Step 4 — Configure your schedule files

Edit the files in `schedules/`:

1. **`planning_standard.json`** — adapt days and times to your custody schedule
2. **`kidspresent.json`** and **`kidsabsent.json`** — set temperatures per zone
3. **`away_15deg.json`** and **`away_18deg.json`** — keep, adapt, or remove as needed
4. **Exception plannings** — add `planning_*.json` files for holidays as needed

See [Schedule file format](#schedule-file-format) below for the full syntax.

### Step 5 — Verify manually

Run the script once to check that the correct configuration is selected and applied:

```bash
./run.sh -vv
```

The token is already saved from step 3. Use `-vvv` for API-level detail, or `-d YYYY-MM-DD` to simulate a specific date.

### Step 6 — Install the launchd agent

Once you are satisfied that everything works correctly:

```bash
./scripts/install_launchd.sh
```

The script detects Python 3.11, shows all paths for confirmation, generates the plist, and activates the agent. The script runs every hour from that point on.

To force an immediate run without waiting for the next hour:
```bash
launchctl kickstart -k gui/$(id -u)/com.tado-planning
```

To check that the agent is active:
```bash
launchctl list | grep tado
```

To follow logs in real time:
```bash
tail -f logs/tado.log logs/tado_error.log
```

To remove the agent:
```bash
./scripts/uninstall_launchd.sh
```

### Updating tado-planning on macOS

```bash
./scripts/mac_fetch.sh
```

This pulls the latest version from GitHub. The launchd agent will use the updated script at the next scheduled run automatically.

---

## Schedule file format

### planning_standard.json

Defines the two-week cycle of heating configuration changes. Each event specifies *when* a configuration change takes effect — not the heating schedule itself, which is defined in the weekconfig file.

```json
{
  "_comment1": "2-week cycle: odd and even ISO weeks",
  "_comment2": "Level 1 events define the main configuration",
  "_comment3": "Level 2 events define an independent or overlay configuration",
  "events": [
    { "day": "friday",  "time": "18:00", "week": "odd",  "level": 1, "config": "kidsabsent"  },
    { "day": "friday",  "time": "15:00", "week": "even", "level": 1, "config": "kidspresent" },
    { "day": "tuesday", "time": "06:30", "week": "even", "level": 2, "config": "away_18deg"  },
    { "day": "tuesday", "time": "11:30", "week": "even", "level": 2, "config": "away_15deg"  }
  ]
}
```

| Field | Values | Description |
|-------|--------|-------------|
| `day` | `monday` … `sunday` | Day the change takes effect |
| `time` | `HH:MM` | Time the change takes effect |
| `week` | `odd`, `even`, `both` | ISO week parity |
| `level` | `1`, `2` | Configuration level |
| `config` | filename without `.json` | Weekconfig file to apply |

> **Note:** `day` and `time` define *when the configuration switches*, not the heating slots inside the zone. Heating time slots are defined in the weekconfig file itself.

### Exception planning files

An exception planning overrides `planning_standard.json` during a specific date/time period. All `planning_*.json` files other than `planning_standard.json` are treated as exceptions.

```json
{
  "_description": "Easter holidays 2026",
  "period": {
    "start": "2026-04-05 00:00",
    "end":   "2026-04-19 23:59"
  },
  "events": [
    { "level": 1, "config": "vacancewithkids", "week": "both", "day": "monday", "time": "00:00" }
  ]
}
```

When the current date/time falls within `period`, this planning takes precedence. Multiple exception plannings can coexist — if two overlap, the one that started most recently wins.

### Weekconfig files

A weekconfig file defines heating settings per zone. Zone names must match your Tado zone names (lowercased, spaces replaced by underscores). Run `list_zones.sh` to get the exact names.

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
| `ONE_DAY` | Same schedule every day |
| `THREE_DAY` | Weekdays (`MONDAY_TO_FRIDAY`) + Saturday + Sunday |
| `SEVEN_DAY` | One schedule per day — use `"monday": [...]`, `"tuesday": [...]`, etc. |

**Optional fields:**

| Field | Description |
|-------|-------------|
| `weekend` | Weekend slots — if absent, `week` applies on weekends too |
| `away_temp` | Minimum temperature in Tado away mode (°C) |
| `away_enabled` | `true` / `false` — enables or disables away mode for this zone |
| `preheat` | `off`, `ECO`, `BALANCE`, `COMFORT` |
| `early_start` | `true` / `false` — Tado early start feature |

---

## Verbosity levels

Verbosity controls how much detail is logged on each run.

For **manual runs** (both platforms), use flags:

| Flag | What is shown |
|------|---------------|
| *(none)* | ISO week, active configs, connection status, result |
| `-v` | + Content of active configs (zones, slots, away, early start) |
| `-vv` | + All cycle candidates with active marker and wrap-around info |
| `-vvv` | + API blocks sent to Tado (start → end : temp) |
| `-vvvv` | + Raw PUT/GET requests with payload and response |

For **scheduled runs**, verbosity is set as a number:
- **On HA**: in the add-on **Configuration** tab → `verbosity` field (0–4)
- **On macOS**: not configurable for scheduled runs — use manual runs with flags to diagnose issues

---

## Troubleshooting

| Symptom | Action |
|---------|--------|
| Token missing or expired | Run `list_zones.sh` or `ha_debug.sh` — OAuth flow will restart automatically |
| Wrong configuration applied | Run with `-d YYYY-MM-DD -vv` to simulate the date and inspect the selection |
| Zone names not matching | Run `list_zones.sh` and compare with your weekconfig file keys |
| Schedules not applied | Check logs for errors; verify JSON syntax in your config files |
| launchd agent not running (Mac) | `launchctl list \| grep tado` — re-run `install_launchd.sh` if missing |
| HA add-on crash on start | Run `./scripts/ha_debug.sh -vv` from SSH for full output |
