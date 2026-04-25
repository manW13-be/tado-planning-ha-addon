# tado-planning

> **Version 1.2.53**

Automated Tado heating schedule management for households with a **shared custody cycle** — and beyond.

---

## Table of contents

- [Why tado-planning](#why-tado-planning)
- [How it works](#how-it-works)
- [Key concepts](#key-concepts)
- [Example scenario](#example-scenario)
- [Repository structure](#repository-structure)
- [Choosing your platform](#choosing-your-platform)
- [Prerequisites](#prerequisites)
- [Setup — Home Assistant](#setup--home-assistant)
- [Setup — macOS](#setup--macos)
- [Web configurator](#web-configurator)
- [Data file format](#data-file-format)
- [Manual runs and testing](#manual-runs-and-testing)
- [Verbosity levels](#verbosity-levels)
- [Home Assistant entities](#home-assistant-entities)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [For developers](#for-developers)
  - [Architecture](#architecture)
  - [Scripts reference](#scripts-reference)
  - [Development workflow](#development-workflow)
  - [Versioning](#versioning)
  - [Known issues and quirks](#known-issues-and-quirks)
- [Credits](#credits)
- [License](#license)

---

## Why tado-planning

Tado is a smart heating system, and Home Assistant has solid Tado integration — but both share the same fundamental limitation: **schedules are based on a seven-day week**. There is no built-in way to express a pattern that repeats every two weeks, or to define exceptions that span arbitrary periods.

tado-planning is not an alternative interface to control Tado. It is a purpose-built layer that sits on top of Tado's API to work around this limitation, adding:

- **Two-week cycle support** — odd and even ISO weeks (or a sequential two-week count from a reference date) can carry different heating configurations
- **Exception periods** — any planning can define a start/end window during which it overrides the standard cycle, for any duration
- **Two independent configuration levels** — allowing two separate sets of heating rules to coexist on the same zones simultaneously
- **Web configurator** — a built-in Flask UI to manage all configurations without editing JSON files by hand

---

## How it works

```
┌─────────────────────────────────────────────────────┐
│                   GitHub Repository                  │
│              manW13-be/tado-planning-ha-addon        │
└───────────────┬─────────────────────┬───────────────┘
                │                     │
         git pull/push           git pull/push
                │                     │
┌───────────────▼───────┐   ┌─────────▼───────────────┐
│      macOS (Mac)      │   │  Home Assistant (ODROID) │
│                       │   │                          │
│  tado-planning-run.py │   │  tado-planning add-on    │
│  via launchd (hourly) │   │  Docker container        │
│                       │   │  --loop mode             │
│  tado-planning-cfg.py │   │                          │
│  on demand (--cfg)    │   │  tado-planning-cfg.py    │
│                       │   │  always running          │
└───────────────────────┘   └──────────────────────────┘
                │                     │
                └──────────┬──────────┘
                           │
                    ┌──────▼──────┐
                    │  Tado API   │
                    └─────────────┘
```

Every hour the scheduler checks which configuration should be active, compares it to what is already set on Tado, and only pushes changes when something has actually changed.

---

## Key concepts

### Plannings

A **planning** defines *when* to switch heating configurations. It contains a list of events, each specifying a weekday, a time, a week parity, a level, and which weekconfig to apply.

There are two kinds of plannings:

- **Standard planning** — the baseline cycle (`start: null`, `end: null`), always active. Exactly one must exist.
- **Exception plannings** — define a `start` and/or `end` date/time. During that window they take precedence over the standard planning. Once the period ends, the standard planning resumes automatically. Multiple exceptions can coexist — the one whose `start` is most recent wins.

### Weekconfigs

A **weekconfig** defines *what* to apply to Tado: temperatures, time events, timetable type, away mode, preheat — per zone. Only the zones listed in a weekconfig are touched when it is applied.

All weekconfigs are stored together in `weekconfigs.json`.

### Configuration levels

Planning events carry a **level** (1 or 2):

- **Level 1** — defines the main heating configuration for a set of zones
- **Level 2** — defines its own configuration independently of level 1

Level 1 and level 2 can cover **different zones** (fully independent) or the **same zones** (level 2 applied on top of level 1, only modifying what it specifies).

### Cycle types

| Cycle | Description |
|-------|-------------|
| `one-week` | 7-day repeating cycle — `week` field on events is ignored |
| `two-weeks-iso` | Alternates on odd/even ISO week number |
| `two-weeks-seq` | Alternates on a two-week count from a `ref_date` |

### Selection logic

Every hour, for each level independently:
1. Find all plannings active at the current time, ordered by precedence
2. In the winning planning, find the last past event in the current cycle
3. If no past event exists yet, wrap around to the last event of the previous cycle
4. Load the corresponding weekconfig and compare zone by zone with Tado's current state
5. Push only the zones that have actually changed

---

## Example scenario

**Standard two-week cycle (two-weeks-iso):**
- **Odd weeks**: kids are away → apply `kidsabsent` (lower temperatures in kids' rooms)
- **Even weeks**: kids are home → apply `kidspresent` (normal temperatures throughout)

**Level 2 example — cleaning day (even weeks):**
The cleaning lady arrives Tuesday morning. The house is in away mode, but the minimum temperature needs to be 18°C while she works.
- **Tuesday 06:30** → apply `away_18deg` (level 2): raises away temperature on all zones
- **Tuesday 11:30** → apply `away_15deg` (level 2): resets away temperature

Since `kidspresent` (level 1) and `away_18deg` (level 2) cover the same zones, the level 2 setting is applied on top of the level 1 schedule already set on Tado — without touching time events or other settings.

**Exception planning — exam period:**
- `preblocus` has `start: "2026-05-01 00:00"`, `end: "2026-05-08 00:00"`, cycle `one-week`
- During that week, `kidspresent` is applied regardless of odd/even ISO week
- After the end date, `standard` resumes automatically

---

## Repository structure

```
tado-planning/
├── tado_planning/
│   ├── run.sh                    # Universal entry point
│   ├── tado-planning-run.py      # Scheduler — reads plannings, applies to Tado
│   ├── tado-planning-cfg.py      # Web configurator (Flask)
│   ├── config.json               # HA add-on manifest (version, schema, ports)
│   ├── Dockerfile                # aarch64 container for ODROID N2+
│   ├── static/                   # Web UI static assets
│   ├── templates/
│   │   └── index.html            # Web UI single-page app
│   └── tado_refresh_token        # OAuth token (gitignored)
├── schedules/                    # Personal schedule data (gitignored)
│   ├── weekconfigs.json          # All zone configurations
│   ├── plannings.json            # All plannings (standard + exceptions)
│   ├── settings.json             # loop_interval, default_zone template
│   ├── loop_status.json          # Current loop state (runtime)
│   └── tado-planning.log         # Rotated log (500 KB max)
├── dist/                         # macOS app build output (gitignored)
│   └── TadoPlanning.app          # Built by macos_app_build.sh
├── scripts/
│   ├── list_zones.sh             # List Tado zones (Mac + HA SSH)
│   ├── git_fetch.sh              # Pull from GitHub
│   ├── git_push.sh               # Commit + push to GitHub
│   ├── launchd_install.sh        # macOS: install & activate launchd agent
│   ├── launchd_uninstall.sh      # macOS: deactivate & remove launchd agent
│   ├── macos_app_build.sh        # macOS: build TadoPlanning.app + DMG
│   ├── docker_test_build.sh      # Build test Docker image
│   ├── docker_test_start.sh      # Start test container
│   ├── docker_test_stop.sh       # Stop test container
│   └── docker_test_remove.sh     # Remove test image
└── repository.json               # HA repository metadata
```

---

## Choosing your platform

tado-planning runs on **Home Assistant** (as an add-on) or on **macOS** (via launchd). Both platforms run the same scheduler and produce identical results.

> **Run it on one platform only.** Running both simultaneously risks pushing conflicting configurations to Tado.

- **Home Assistant** — recommended if HA is always running and you want everything in one place. The web configurator is accessible as an HA panel.
- **macOS** — useful if you prefer managing config from your Mac. Use `TadoPlanning.app` for a one-click experience, or `run.sh --cfg` from the terminal.

---

## Prerequisites

- A Tado account with at least one heating zone
- For Home Assistant: HAOS on aarch64 (tested on ODROID N2+), SSH add-on access
- For macOS: Python 3.11+ (`brew install python@3.11`), `jq` (`brew install jq`)

---

## Setup — Home Assistant

### Step 1 — Add the repository and install

1. Go to **Settings → Add-ons → Add-on Store**
2. Click **⋮** (top right) → **Repositories**
3. Add: `https://github.com/manW13-be/tado-planning-ha-addon`
4. Click **tado-planning → Install** — the Docker image builds on your device (3–5 min on ODROID N2+)
5. Do **not** start the add-on yet

### Step 2 — Authenticate with Tado

```bash
./scripts/list_zones.sh
```

On first run, a URL is printed — open it in a browser, log in with your Tado account, and authorize the device. The refresh token is saved to `/config/tado-planning/tado_refresh_token` and reused automatically.

Note the exact zone names — you will need them when configuring weekconfigs.

### Step 3 — Start the add-on

In HA: **Settings → Add-ons → tado-planning → Start**

The add-on starts in `--loop` mode: the scheduler runs on its configured interval, and the web configurator is available immediately.

### Step 4 — Configure your schedules via the web UI

The add-on panel is accessible in the HA sidebar at **Tado Planning**, or directly at `http://ha2.local:8099`.

Use the web configurator to:
1. Create your **weekconfigs** (zone temperature profiles)
2. Create your **plannings** (standard cycle + any exception periods)
3. Check the **Status** tab to verify the current resolved configuration per zone

See [Web configurator](#web-configurator) below.

### Step 5 — Verify manually

```bash
./tado_planning/run.sh -vv
```

Check that the correct configurations are selected and zones are updated as expected.

### Step 6 — Set verbosity (optional)

In the add-on **Configuration** tab, set `verbosity` (0–4). See [Verbosity levels](#verbosity-levels) below.

---

## Setup — macOS

### Step 1 — Install prerequisites and clone

```bash
brew install python@3.11 jq
git clone https://github.com/manW13-be/tado-planning-ha-addon.git tado-planning
cd tado-planning
```

### Step 2 — Authenticate with Tado

```bash
./scripts/list_zones.sh
```

Opens a browser tab to authenticate with Tado. The token is saved to `tado_planning/tado_refresh_token` and reused automatically. Note the exact zone names.

### Step 3 — Configure your schedules via the web UI

**Option A — macOS app (recommended)**

```bash
./scripts/macos_app_build.sh --app-only
open dist/TadoPlanning.app
```

Builds and launches `TadoPlanning.app`. The app starts Flask in the background and opens the web configurator at `http://localhost:8099` automatically. On subsequent launches it detects Flask already running and reopens the browser directly.

**Option B — terminal**

```bash
./tado_planning/run.sh --cfg
```

Opens the web configurator at `http://localhost:8099`. Stop with Ctrl+C when done.

Use the configurator to:
1. Create your **weekconfigs** (zone temperature profiles)
2. Create your **plannings** (standard cycle + exceptions)
3. Check the **Status** tab to verify the current state

See [Web configurator](#web-configurator) below.

### Step 4 — Verify manually

```bash
./tado_planning/run.sh -vv
```

Use `-d YYYY-MM-DD` to simulate a specific date. Check that configurations are applied correctly.

### Step 5 — Install the launchd agent

Once everything works correctly, install the launchd agent — either from the **Service** tab in the web configurator, or from the terminal:

```bash
./scripts/launchd_install.sh          # interactive install (supports --dry-run)
./scripts/launchd_uninstall.sh        # remove agent
```

The agent starts `run.sh --loop` at login: the scheduler runs on its configured interval and the web configurator runs in parallel.

```bash
launchctl print gui/$(id -u)/com.tado-planning  # check agent status
launchctl kickstart -k gui/$(id -u)/com.tado-planning  # force immediate run
```

---

## Web configurator

The web configurator manages all schedule data through a browser UI. It is available:

- **Home Assistant**: as a panel in the HA sidebar (ingress, no port needed), or at `http://ha2.local:8099`
- **macOS**: via `TadoPlanning.app` or `./tado_planning/run.sh --cfg` at `http://localhost:8099`

### Sections

| Section | Description |
|---------|-------------|
| **Status** | Current resolved configuration per zone and per level, active planning, 14-day timeline |
| **Weekconfig (actual)** | Live Tado state per zone — timetable type, time events, away and preheat settings. The current day's tab is pre-selected automatically |
| **Weekconfig (analysis)** | Side-by-side comparison of expected (simulated) vs actual (Tado live) configuration per zone, with L1/L2 provenance per field |
| **Weekconfigs** | Create, edit, copy, rename, delete zone configuration profiles |
| **Plannings** | Create, edit, copy, rename, delete plannings (standard + exceptions) |
| **Settings** | Scheduler loop interval, default zone template for new weekconfigs |
| **Logs** | Live log viewer with colour-coded entries, auto-refresh, manual clear |
| **Add-on** | HA only — installed and latest version, check for updates, one-click update, verbosity control, API call counters (GET/PUT) |
| **Service** | macOS only — install or uninstall the launchd agent from the UI, with live status |

The Status section also has a **▶ Run now** button to trigger the scheduler immediately, whether or not the loop is running.

---

## Data file format

All schedule data lives in two files in the `schedules/` directory, managed by the web configurator.

### `plannings.json`

A JSON array of planning objects.

```json
[
  {
    "name":   "standard",
    "cycle":  "two-weeks-iso",
    "start":  null,
    "end":    null,
    "events": [
      { "day": "friday",  "time": "18:00", "week": "odd",  "level": 1, "config": "kidsabsent"  },
      { "day": "friday",  "time": "15:00", "week": "even", "level": 1, "config": "kidspresent" },
      { "day": "tuesday", "time": "06:30", "week": "even", "level": 2, "config": "away_18deg"  },
      { "day": "tuesday", "time": "11:30", "week": "even", "level": 2, "config": "away_15deg"  }
    ]
  },
  {
    "name":     "easter2026",
    "cycle":    "one-week",
    "start":    "2026-04-05 00:00",
    "end":      "2026-04-19 00:00",
    "events": [
      { "day": "sunday", "time": "00:00", "week": "both", "level": 1, "config": "vacancewithkids" }
    ]
  }
]
```

**Planning fields:**

| Field | Values | Description |
|-------|--------|-------------|
| `name` | string | Unique identifier |
| `cycle` | `one-week`, `two-weeks-iso`, `two-weeks-seq` | Cycle type |
| `ref_date` | `YYYY-MM-DD` | Required for `two-weeks-seq` — defines week 1 |
| `start` | `YYYY-MM-DD HH:MM` or `null` | Exception start (null = no start bound) |
| `end` | `YYYY-MM-DD HH:MM` or `null` | Exception end (null = no end bound) |
| `events` | array | List of schedule switch events |

**Event fields:**

| Field | Values | Description |
|-------|--------|-------------|
| `day` | `monday` … `sunday` | Day the configuration switches |
| `time` | `HH:MM` | Time the configuration switches |
| `week` | `odd`, `even`, `both` | ISO week parity (ignored for `one-week` cycle) |
| `level` | `1`, `2` | Configuration level |
| `config` | weekconfig name | Weekconfig to apply at this event |

**Conflict rules** (enforced at validation):
- Exactly one planning with `start: null` and `end: null` (the standard)
- No two plannings with identical `(start, end)` pair

---

### `weekconfigs.json`

A JSON object keyed by configuration name. Each value is an object keyed by zone name.

```json
{
  "kidspresent": {
    "living_room": {
      "timetable":    "Mon-Fri, Sat, Sun",
      "Mon-Fri":      [{ "start": "00:00", "temp": 15 }, { "start": "07:00", "temp": 20 }, { "start": "22:30", "temp": 15 }],
      "Sat":          [{ "start": "00:00", "temp": 15 }, { "start": "08:00", "temp": 20 }, { "start": "23:00", "temp": 15 }],
      "Sun":          [{ "start": "00:00", "temp": 15 }, { "start": "08:00", "temp": 20 }, { "start": "22:00", "temp": 15 }],
      "away_temp":    15.0,
      "away_enabled": true,
      "preheat":      "ECO",
      "early_start":  true
    }
  },
  "away_15deg": {
    "living_room": { "away_temp": 15.0, "away_enabled": true },
    "bedroom":     { "away_temp": 15.0, "away_enabled": true }
  }
}
```

Zone names must match your Tado zone names exactly. Run `./scripts/list_zones.sh` to get them.

**Timetable types:**

| Value | Day keys required | Description |
|-------|-------------------|-------------|
| `Mon-Sun` | `Mon-Sun` | Same schedule every day |
| `Mon-Fri, Sat, Sun` | `Mon-Fri`, `Sat`, `Sun` | Weekdays + Saturday + Sunday |
| `Mon, ..., Sun` | `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun` | One schedule per day |

**Zone config fields** (all optional except `timetable` when defining time events):

| Field | Type | Description |
|-------|------|-------------|
| `timetable` | string | Timetable type — required if defining time events |
| `Mon-Sun` / `Mon-Fri` / etc. | array | Time blocks: `[{ "start": "HH:MM", "temp": N }, ...]` |
| `away_temp` | number | Minimum temperature in away mode (°C) |
| `away_enabled` | boolean | Enable or disable Tado away mode |
| `preheat` | `off`, `ECO`, `BALANCE`, `COMFORT` | Tado preheat setting |
| `early_start` | boolean | Tado early start feature |

A weekconfig that only sets `away_temp` / `away_enabled` (like `away_15deg`) does not need a `timetable` — it will only touch those specific settings on Tado.

---

## Manual runs and testing

```bash
./tado_planning/run.sh                     # single run, current date, verbosity 0
./tado_planning/run.sh -vv                 # verbosity 2
./tado_planning/run.sh -d 2026-04-10 -vv  # simulate a specific date
./tado_planning/run.sh --cfg               # start web configurator only
```

---

## Verbosity levels

| Flag | What is shown |
|------|---------------|
| *(none)* | ISO week, active planning, active configs, result summary |
| `-v` | + Weekconfig zone details (events, away, preheat) |
| `-vv` | + Cycle candidates with selection trace and wrap-around info |
| `-vvv` | + API blocks sent to Tado (start → end : temp) |
| `-vvvv` | + Raw PUT/GET requests with payload and response |

For **scheduled runs on HA**: set `verbosity` in the add-on **Configuration** tab (0–4).  
For **macOS launchd**: scheduled runs always run at verbosity 0 — use manual runs to diagnose.

---

## Home Assistant entities

After each run, tado-planning pushes the following states to the HA API (HA add-on only):

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.tado_planning_last_run` | timestamp | Date/time of the last scheduler run |
| `sensor.tado_planning_last_put` | timestamp | Date/time of the last write to the Tado API |
| `sensor.tado_planning_api_get_calls` | number | Cumulative Tado API GET calls since install |
| `sensor.tado_planning_api_put_calls` | number | Cumulative Tado API PUT calls since install |
| `sensor.tado_planning_version` | string | Installed add-on version |
| `sensor.tado_planning_latest_version` | string | Latest available version (when an update is available) |
| `binary_sensor.tado_planning_update_available` | boolean | `on` when a newer version exists in the store |

These entities can be used in HA dashboards, automations, or alerts.

---

## Updating

### Home Assistant

Use the **Add-on** tab in the web configurator: click **🔍 Check for updates** to force the store to reload, then **Update** if a new version is available. Alternatively, HA shows an **Update** button on the add-on page automatically when a new version is detected.

### macOS

```bash
./scripts/git_fetch.sh
```

The launchd agent picks up the updated scripts at the next scheduled run automatically.

---

## Troubleshooting

| Symptom | Action |
|---------|--------|
| Token missing or expired | Run `./scripts/list_zones.sh` — OAuth flow restarts automatically |
| Wrong configuration applied | `./tado_planning/run.sh -d YYYY-MM-DD -vv` to simulate and inspect |
| Zone names not matching | Run `./scripts/list_zones.sh` and compare with weekconfig zone keys |
| Schedules not applied | Check logs in web UI or with `-vv`; verify JSON in the configurator |
| launchd agent not running (Mac) | `launchctl print gui/$(id -u)/com.tado-planning` — re-run `launchd_install.sh` or use the Service tab in the web UI |
| HA add-on crash on start | `./tado_planning/run.sh -vv` from HA SSH for full output |
| Web UI stuck on spinner | Check browser console for JS errors; verify Flask is running on the expected port |

---

## For developers

### Architecture

#### Scheduler — `tado-planning-run.py`

Reads `plannings.json` and `weekconfigs.json`, resolves the active configuration for each zone and level at the current time, and pushes only the zones that differ from Tado's current state.

Environment variables override default paths:

| Variable | Default (macOS) | Default (HAOS) |
|----------|-----------------|----------------|
| `TADO_TOKEN_FILE` | `tado_planning/tado_refresh_token` | `/config/tado-planning/tado_refresh_token` |
| `TADO_SCHEDULES_DIR` | `tado_planning/schedules` | `/config/tado-planning/schedules` |

`run.sh` sets these before calling the Python script, so direct Python calls and `run.sh` calls are equivalent.

#### Web configurator — `tado-planning-cfg.py`

Flask app serving a single-page UI. Exposes a REST API (`/api/*`) consumed by the frontend. In `--loop` mode (HA container), it runs as a background process alongside the scheduler loop. In `--cfg` mode (standalone), it runs alone.

On macOS, it opens the browser automatically and binds to `127.0.0.1:8099`. On HA, it binds to `0.0.0.0:8099` with ingress support.

`run.sh` passes `TADO_CONTEXT` to the Flask process so the UI can adapt per platform — for example showing the **Service** tab only on macOS.

#### `run.sh`

Universal entry point that auto-detects the deployment context:

| Context | Where | Conflict check |
|---------|-------|----------------|
| `mac-launchd` | macOS, started by launchd | None (launchd serialises) |
| `mac-shell` | macOS, interactive shell | Rejects if launchd agent loaded |
| `ha-shell` | HA Linux SSH | Rejects if any container running |
| `ha-docker-prod` | Inside prod HA container | Rejects if test container running |
| `ha-docker-test` | Inside test container | Rejects if prod container running |

Modes:

| Flag | Behaviour |
|------|-----------|
| *(none)* | Single scheduler run, then exit |
| `--loop` | Scheduler loop + Flask configurator (container only) |
| `--cfg` | Flask configurator only |

#### Token management

Authentication uses Tado's OAuth2 device flow via `PyTado` (≥ 0.18). On first run, a URL is printed for the user to authorize in a browser. The refresh token is persisted to disk and reused on every subsequent run. If missing or expired, the flow restarts automatically.

#### HA add-on container

- Base image: `ghcr.io/home-assistant/aarch64-base` (aarch64, ODROID N2+)
- Entrypoint: `CMD ["/run.sh", "--loop"]`
- `--loop` reads `verbosity` from `/data/options.json`, runs the scheduler on the configured interval (default 60 min, aligned to clock hour), and keeps Flask running in parallel
- A `loop_trigger` file in the schedules directory causes an immediate scheduler run (used by the **▶ Run now** button in the UI)
- Loop state is written to `loop_status.json` (PID, last run, next run)

---

### Scripts reference

#### `tado_planning/run.sh`

```bash
./tado_planning/run.sh                     # single run
./tado_planning/run.sh --loop             # scheduler loop + configurator (container only)
./tado_planning/run.sh --cfg              # web configurator only
./tado_planning/run.sh -vv                # single run, verbosity 2
./tado_planning/run.sh -d 2026-04-10 -vv # simulate a specific date
```

#### `scripts/list_zones.sh`

Lists all Tado zones with names, IDs, and types. Triggers OAuth on first run.

#### `scripts/git_fetch.sh` / `scripts/git_push.sh`

Pull from / push to GitHub. Used to sync code between Mac and HA.

#### `scripts/launchd_install.sh` / `scripts/launchd_uninstall.sh`

macOS only. Installs or removes the launchd agent (`com.tado-planning`). `launchd_install.sh` supports `--dry-run` to simulate the full install without making any changes. Both scripts are also callable from the web configurator's **Service** tab.

#### `scripts/macos_app_build.sh`

Builds `TadoPlanning.app` (and optionally a DMG) from the current project directory. The project path and port are baked into the launcher at build time.

```bash
./scripts/macos_app_build.sh            # build app + DMG
./scripts/macos_app_build.sh --app-only # build app only (faster, for local testing)
```

#### `scripts/docker_test_build.sh` / `docker_test_start.sh` / `docker_test_stop.sh` / `docker_test_remove.sh`

Build, start, stop, and remove a local test container (`addon_test_tado_planning`). Used to test Docker builds without touching the production add-on. `docker_test_start.sh` forwards all arguments to `run.sh` inside the container.

---

### Development workflow

#### On macOS

```bash
./tado_planning/run.sh --cfg              # edit schedules via web UI
./tado_planning/run.sh -vv               # test the scheduler
./scripts/git_push.sh "fix: description" # push to GitHub
```

#### On HA

Code changes in `tado_planning/` require rebuilding the Docker image:

```bash
# From HA SSH:
./scripts/git_fetch.sh
# then restart the add-on from the HA UI (or via ha supervisor restart add-on)
```

For a quick test without rebuilding:

```bash
docker cp tado_planning/tado-planning-run.py addon_fc4e2b3e_tado_planning:/tado-planning-run.py
./tado_planning/run.sh -vv
```

This change is temporary — it will be overwritten at the next rebuild.

---

### Versioning

The version in `config.json` follows `MAJOR.MINOR.PATCH`:

| Change type | Bump |
|------------|------|
| Bug fix, small improvement | Patch: `1.1.x → 1.1.x+1` |
| New feature, backward compatible | Minor: `1.x.x → 1.x+1.0` |
| Breaking change, data migration required | Major: `x.x.x → x+1.0.0` |

The version is used by the HA store to detect available updates.

---

### Known issues and quirks

| Issue | Details |
|-------|---------|
| PyTado enum comparisons | Zone type comparisons require `.value`; patched in `tado-planning-run.py` |
| OAuth URL trailing slash | PyTado ≥ 0.18 requires trailing slash on token URL; patched |
| Token file format | Must be `{"refresh_token": "..."}` JSON, not plain text |
| HAOS overlay filesystem | `docker build --no-cache` required to avoid stale layers |
| launchd env isolation | launchd agents don't inherit shell env vars; all paths declared explicitly in the plist |
| `launchctl list` in subprocesses | `launchctl list` does not see GUI-domain services from Python/bash subprocesses; use `launchctl print gui/<uid>/<label>` instead (return code 0 = active) |
| `run.sh` baked in image | Changes to `run.sh` require a container rebuild. Use `docker cp` for quick tests. |
| `settings.json` not in git | Created on first save in the UI. Defaults: `loop_interval: 60`, `default_zone` template hardcoded in the UI. |

---

## Credits

This project was built in collaboration:
- **Concept, specification and domain expertise** — [manW13-be](https://github.com/manW13-be)
- **Implementation, debugging and documentation** — [Claude](https://claude.ai) (Anthropic)

---

## License

MIT
