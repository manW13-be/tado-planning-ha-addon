# tado-planning

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
- [Schedule file format](#schedule-file-format)
- [Manual runs and testing](#manual-runs-and-testing)
- [Verbosity levels](#verbosity-levels)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [For developers](#for-developers)
  - [Architecture](#architecture)
  - [Scripts reference](#scripts-reference)
  - [Development workflow](#development-workflow)
  - [Versioning](#versioning)
  - [Adding a new schedule config](#adding-a-new-schedule-config)
  - [Known issues and quirks](#known-issues-and-quirks)
- [Credits](#credits)
- [License](#license)

---

## Why tado-planning

Tado is a smart heating system, and Home Assistant has solid Tado integration — but both share the same fundamental limitation: **schedules are based on a seven-day week**. There is no built-in way to express a pattern that repeats every two weeks, or to define exceptions that span arbitrary periods.

tado-planning is not an alternative interface to control Tado. It is a purpose-built layer that sits on top of Tado's API to work around this limitation, adding:

- **Two-week cycle support** — odd and even ISO weeks can carry different heating configurations
- **Exception periods** — any planning file can define a date/time range during which it overrides the standard two-week cycle, for any duration (a day, a week, a school holiday period)
- **Two independent configuration levels** — allowing two separate sets of heating rules to coexist and interact on the same zones at the same time

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
│  tado_planning.py     │   │  tado-planning add-on    │
│  via launchd          │   │  via HA scheduler        │
│  (hourly)             │   │  (hourly)                │
│                       │   │                          │
│  tado_refresh_token   │   │  tado_refresh_token      │
│  schedules/           │   │  /config/tado-planning/  │
│                       │   │  schedules/              │
└───────────────────────┘   └──────────────────────────┘
                │                     │
                └──────────┬──────────┘
                           │
                    ┌──────▼──────┐
                    │  Tado API   │
                    └─────────────┘
```

The same Python script (`tado_planning.py`) runs in both environments. Every hour it checks which configuration should be active, compares it to what is already set on Tado, and only pushes changes when something has actually changed.

---

## Key concepts

### Planning files

A **planning file** defines *when* to switch heating configurations. It contains a list of events, each specifying a weekday, a time, an ISO week parity, and which Tado configuration to apply.

> `day` and `time` in a planning event define *when the configuration switches* — not the heating time slots inside the zones. Heating time slots are defined in the weekconfig file itself.

There are two kinds of planning files:

- **`planning_standard.json`** — the baseline two-week cycle, always active
- **Exception plannings** (e.g. `planning_paques2026.json`) — define a `period` with a start and end date/time. During that window they take precedence over the standard planning. Once the period ends, the standard planning resumes automatically. Multiple exceptions can coexist — if two overlap, the one that started most recently wins.

### Tado configuration files (weekconfigs)

A **weekconfig file** defines *what* to apply to Tado: temperatures, time slots, timetable type, away mode, and preheat — per zone. Only the zones listed in a weekconfig are updated when it is applied.

### Configuration levels

Planning events carry a **level** (1 or 2):

- **Level 1** — defines the main heating configuration for a set of zones
- **Level 2** — defines its own configuration for a set of zones, independently of level 1

Level 1 and level 2 can reference **completely different zones** — in that case they are fully independent. If they reference **the same zone**, level 2 is applied on top of what level 1 has already set on Tado, modifying only the settings it specifies and leaving the rest intact.

### Selection logic

Every hour, the script finds the most recent past event in the current two-week cycle (odd + even weeks combined) and applies the corresponding weekconfig. If no event has occurred yet in the current cycle, it wraps around to the last event of the previous cycle.

---

## Example scenario

**Standard two-week cycle:**
- **Odd weeks**: kids are away → apply `kidsabsent` (lower temperatures in kids' rooms)
- **Even weeks**: kids are home → apply `kidspresent` (normal temperatures throughout)

**Level 2 example — cleaning day (even weeks):**
The cleaning lady arrives Tuesday morning. Tado doesn't detect her as present, so the house stays in away mode — but the minimum temperature needs to be 18°C while she works.
- **Tuesday 06:30** → apply `away_18deg` (level 2): raises away temperature to 18°C on all zones
- **Tuesday 11:30** → apply `away_15deg` (level 2): resets away temperature to 15°C once she leaves

Since `kidspresent` (level 1) and `away_18deg` (level 2) cover the same zones, the level 2 away temperature is applied on top of the level 1 schedule already set on Tado — without touching the time slots or any other settings.

**Exception planning — Easter holidays:**
- `planning_paques2026.json` defines a period from 2026-04-05 to 2026-04-19
- During that period, `vacancewithkids` is applied regardless of odd/even week
- After 2026-04-19, `planning_standard.json` resumes automatically

---

## Repository structure

```
tado-planning/
├── tado_planning.py              # Main script (runs on both Mac and HA)
├── config.json                   # HA add-on manifest (version, schema, etc.)
├── Dockerfile                    # aarch64 container for ODROID N2+
├── run.sh                        # Universal entrypoint (Mac, HA SSH, Docker)
├── gitignore                     # Finder/Samba-visible copy of .gitignore
├── schedules/                    # Your personal schedule files (gitignored)
│   ├── planning_standard.json    # Standard two-week cycle definition
│   ├── planning_*.json           # Exception plannings (specific periods)
│   ├── kidspresent.json          # Level 1 weekconfig — kids at home
│   ├── kidsabsent.json           # Level 1 weekconfig — kids away
│   ├── vacancewithkids.json      # Level 1 weekconfig — school holidays
│   ├── away_15deg.json           # Level 2 weekconfig — away mode at 15°C
│   └── away_18deg.json           # Level 2 weekconfig — away mode at 18°C
├── logs/                         # Log files (gitignored)
└── scripts/
    ├── list_zones.sh             # List Tado zones (Mac + HA SSH)
    ├── fetch.sh                  # Pull from GitHub (Mac + HA SSH)
    ├── push.sh                   # Commit + push to GitHub (Mac + HA SSH)
    ├── ha_deploy.sh              # Rebuild Docker image + restart add-on (HA SSH)
    ├── install_launchd.sh        # macOS: install & activate launchd agent
    └── uninstall_launchd.sh      # macOS: deactivate & remove launchd agent
```

---

## Choosing your platform

tado-planning runs on **Home Assistant** (as an add-on) or on **macOS** (via launchd). Both platforms run the same script on the same hourly schedule and produce identical results.

> **Run it on one platform only.** Running both simultaneously risks pushing conflicting configurations to Tado.

- **Home Assistant** — recommended if HA is always running and you want everything in one place
- **macOS** — useful if you don't have a permanent HA setup, or prefer managing config from your Mac

---

## Prerequisites

- A Tado account with at least one heating zone
- For Home Assistant: HAOS on aarch64 (tested on ODROID N2+), SSH access
- For macOS: Python 3.11+ via Homebrew, `jq`

---

## Setup — Home Assistant

### Step 1 — Add the repository and install

1. Go to **Settings → Add-ons → Add-on Store**
2. Click **⋮** (top right) → **Repositories**
3. Add: `https://github.com/manW13-be/tado-planning-ha-addon`
4. Click **tado-planning → Install** — the Docker image builds on your device (3–5 min on ODROID N2+)
5. Do **not** start the add-on yet

### Step 2 — Create your schedule directory

The schedules directory is created automatically on first run. You can also create it manually:

```bash
mkdir -p /config/tado-planning/schedules
```

The files are then accessible via Samba at `smb://homeassistant.local/config/tado-planning/schedules/`.

### Step 3 — List your Tado zones

```bash
./scripts/list_zones.sh
```

Connects to Tado, authenticates if needed, and lists all your zones with their exact names. Note them — you will need them in your weekconfig files.

### Step 4 — Configure your schedule files

Edit the files in `/config/tado-planning/schedules/` via Samba or SSH:

1. **`planning_standard.json`** — adapt days and times to your actual custody handover schedule
2. **`kidspresent.json`** and **`kidsabsent.json`** — set temperatures and time slots using the zone names from step 3
3. **`away_15deg.json`** and **`away_18deg.json`** — keep, adapt, or remove depending on your planning
4. **Exception plannings** — add `planning_*.json` files for holidays or other periods as needed

See [Schedule file format](#schedule-file-format) below for the full syntax.

### Step 5 — Verify manually

```bash
./run.sh -vv
```

On first run a URL is printed — open it in your browser, log in with your Tado account, and authorize the device. The token is saved automatically. Check that the correct configurations are selected and zones are updated as expected.

### Step 6 — Start the add-on

In HA: **Settings → Add-ons → tado-planning → Start**

Check the **Logs** tab to confirm normal operation. To follow logs via SSH:

```bash
ha apps logs fc4e2b3e_tado_planning
```

### Step 7 — Set verbosity (optional)

In the add-on **Configuration** tab, set `verbosity` (0–4). See [Verbosity levels](#verbosity-levels) below.

---

## Setup — macOS

### Step 1 — Install prerequisites and clone

```bash
brew install python@3.11 jq
git clone https://github.com/manW13-be/tado-planning-ha-addon.git tado-planning
cd tado-planning
```

### Step 2 — Create your schedule directory

The `schedules/` directory is created automatically on first run. You can also create it manually:

```bash
mkdir -p schedules
```

### Step 3 — List your Tado zones

```bash
./scripts/list_zones.sh
```

On first run this opens a browser tab to authenticate with Tado. The token is saved to `tado_refresh_token` in the project root and reused automatically. Note the exact zone names.

### Step 4 — Configure your schedule files

Edit the files in `schedules/`:

1. **`planning_standard.json`** — adapt days and times to your custody schedule
2. **`kidspresent.json`** and **`kidsabsent.json`** — set temperatures per zone
3. **`away_15deg.json`** and **`away_18deg.json`** — keep, adapt, or remove as needed
4. **Exception plannings** — add `planning_*.json` files for holidays as needed

### Step 5 — Verify manually

```bash
./run.sh -vv
```

The token is already saved from step 3. Use `-vvv` for API-level detail, or `-d YYYY-MM-DD` to simulate a specific date.

### Step 6 — Install the launchd agent

Once everything works correctly:

```bash
./scripts/install_launchd.sh
```

The script detects Python 3.11, shows all paths for confirmation, generates the plist, and activates the agent. The script then runs every hour automatically.

```bash
launchctl kickstart -k gui/$(id -u)/com.tado-planning  # force immediate run
launchctl list | grep tado                              # check agent status
tail -f logs/tado.log logs/tado_error.log              # follow logs
./scripts/uninstall_launchd.sh                         # remove agent
```

---

## Schedule file format

### planning_standard.json

```json
{
  "_comment1": "2-week cycle: odd and even ISO weeks",
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
| `day` | `monday` … `sunday` | Day the configuration switches |
| `time` | `HH:MM` | Time the configuration switches |
| `week` | `odd`, `even`, `both` | ISO week parity |
| `level` | `1`, `2` | Configuration level |
| `config` | filename without `.json` | Weekconfig to apply |

### Exception planning files

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

All `planning_*.json` files other than `planning_standard.json` are treated as exceptions. When the current date/time falls within `period`, the exception takes precedence.

### Weekconfig files

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

Zone names must match your Tado zone names (lowercased, spaces → underscores). Run `list_zones.sh` to get exact names.

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
| `away_enabled` | `true` / `false` — enables or disables away mode |
| `preheat` | `off`, `ECO`, `BALANCE`, `COMFORT` |
| `early_start` | `true` / `false` — Tado early start feature |

---

## Manual runs and testing

`run.sh` can be called directly on both platforms for manual runs and testing, without starting the scheduler.

```bash
./run.sh                        # run with current date, verbosity 0
./run.sh -vv                    # run with verbosity 2
./run.sh -d 2026-04-10 -vv      # simulate a specific date
./run.sh -p schedules/planning_paques2026.json  # test a specific planning file
./run.sh -c schedules/vacancewithkids.json      # force a specific weekconfig (level 1 only)
```

On **HA SSH**, `run.sh` automatically delegates to the container — no need to use `docker exec` manually. If the container is not running, it starts the add-on first.

> On HA, `run.sh` runs inside the deployed container. If you have modified `run.sh` locally without deploying, it will warn you and ask for confirmation before proceeding.

---

## Verbosity levels

For **manual runs**, use flags:

| Flag | What is shown |
|------|---------------|
| *(none)* | ISO week, active configs, connection status, result |
| `-v` | + Content of active configs (zones, slots, away, early start) |
| `-vv` | + All cycle candidates with active marker and wrap-around info |
| `-vvv` | + API blocks sent to Tado (start → end : temp) |
| `-vvvv` | + Raw PUT/GET requests with payload and response |

For **scheduled runs**:
- **On HA**: add-on **Configuration** tab → `verbosity` field (0–4)
- **On macOS**: use manual runs with flags to diagnose — scheduled runs always run at verbosity 0

---

## Updating

### Home Assistant

When a new version is available, HA shows an **Update** button on the add-on page. Click it — HA pulls the new version from GitHub and rebuilds the image automatically.

To force an immediate update without waiting for store detection:

```bash
./scripts/fetch.sh
./scripts/ha_deploy.sh
```

### macOS

```bash
./scripts/fetch.sh
```

The launchd agent picks up the updated script at the next scheduled run automatically.

---

## Troubleshooting

| Symptom | Action |
|---------|--------|
| Token missing or expired | Run `./scripts/list_zones.sh` — OAuth flow restarts automatically |
| Wrong configuration applied | `./run.sh -d YYYY-MM-DD -vv` to simulate the date and inspect selection |
| Zone names not matching | Run `./scripts/list_zones.sh` and compare with weekconfig file keys |
| Schedules not applied | Check logs for errors; verify JSON syntax in config files |
| launchd agent not running (Mac) | `launchctl list \| grep tado` — re-run `install_launchd.sh` if missing |
| HA add-on crash on start | `./run.sh -vv` from SSH for full output |
| run.sh warns about version mismatch | Run `./scripts/ha_deploy.sh` then retry |

---

## For developers

### Architecture

#### Core script — `tado_planning.py`

The main script runs identically on macOS and Home Assistant. Platform differences are handled at runtime via `platform.system()`. Environment variables override all default paths:

| Variable | Default (macOS) | Default (HAOS) |
|----------|-----------------|----------------|
| `TADO_TOKEN_FILE` | `<project>/tado_refresh_token` | `/data/tado_refresh_token` |
| `TADO_SCHEDULES_DIR` | `<project>/schedules` | `/config/tado-planning/schedules` |

`run.sh` sets these variables before calling `tado_planning.py`, so direct Python calls and `run.sh` calls are fully equivalent.

#### Selection logic in detail

1. Load `planning_standard.json` from `TADO_SCHEDULES_DIR`
2. Scan all other `planning_*.json` files for active exception periods
3. If an exception covers the current date/time, its events override the standard planning for the affected level(s)
4. For each level, build the two-week cycle (odd + even weeks), find the last past event, apply wrap-around if needed
5. Load the corresponding weekconfig(s) and compare zone by zone with what is currently set on Tado
6. Push only the zones that have actually changed

#### Two-level config system

Level 1 and level 2 are resolved independently. They can cover completely different zones (fully independent) or the same zones (level 2 applied on top of level 1). The script does not "stack" configs — it compares the final desired state against Tado's current state and only pushes differences.

#### Token management

Authentication uses Tado's OAuth2 device flow via `PyTado` (≥ 0.18). On first run, a URL is printed for the user to authorize in a browser. The refresh token is persisted to disk and reused. If missing or expired, the flow restarts automatically.

#### HA add-on container

- Base image: `ghcr.io/home-assistant/aarch64-base` (aarch64, ODROID N2+)
- Entrypoint: `CMD ["/run.sh", "--loop"]` in the Dockerfile
- `run.sh --loop` reads verbosity from `/data/options.json`, initializes schedules if absent, then runs `tado_planning.py` in an hourly loop aligned to the clock hour
- `run.sh` without `--loop` = single run (manual/test mode)
- On HA SSH (outside the container), `run.sh` detects the Linux context and delegates automatically via `docker exec`, starting the container first if needed. It also checks that the deployed `run.sh` matches the local version before proceeding.

---

### Scripts reference

All scripts live in `scripts/` and auto-detect the project root (one level up).

#### `run.sh` (project root, not in scripts/)

Universal entrypoint. Handles all three contexts automatically.

```bash
./run.sh                            # single run, verbosity 0
./run.sh --loop                     # hourly loop (Dockerfile only)
./run.sh -vv                        # single run, verbosity 2
./run.sh -d 2026-04-10 -vv          # simulate a specific date
./run.sh -p planning_paques2026.json  # test a specific planning file
./run.sh -c vacancewithkids.json    # force a weekconfig (level 1 only)
```

On HA SSH, all arguments are forwarded transparently to the container.

#### `scripts/fetch.sh`

Pulls from GitHub and syncs `.gitignore → gitignore`. Universal (Mac + HA SSH).

```bash
./scripts/fetch.sh
```

#### `scripts/push.sh`

Commits and pushes to GitHub. Universal (Mac + HA SSH).

```bash
./scripts/push.sh "commit message"          # push, version unchanged
./scripts/push.sh --bump "commit message"   # bump patch version, then push
```

`--bump` reads the current version from GitHub (`git show origin/main:config.json`), increments the patch number, and writes it back to `config.json` before committing. This ensures the version is always based on the remote state, avoiding conflicts when pushing from multiple machines.

#### `scripts/ha_deploy.sh`

Rebuilds the Docker image and restarts the add-on. HA SSH only.

```bash
./scripts/ha_deploy.sh
```

Cleans old images, rebuilds with `--no-cache` (required due to HAOS overlay filesystem), restarts the add-on, and tails the last 20 log lines.

Use this after `fetch.sh` when you want HA to pick up changes immediately without waiting for the store to detect the new version.

#### `scripts/list_zones.sh`

Lists all Tado zones with their names, IDs, and types. Authenticates if needed. On HA SSH, executes inside the container automatically. Universal (Mac + HA SSH).

#### `scripts/install_launchd.sh` / `uninstall_launchd.sh`

macOS only. Installs or removes the launchd agent that runs `run.sh` every hour.

---

### Development workflow

#### Working on HA

```bash
# Edit files on HA or via Samba
./run.sh -vv                        # test manually
./scripts/push.sh --bump "fix: ..." # push with version bump
# HA store will detect new version, or:
./scripts/ha_deploy.sh              # force immediate rebuild
```

#### Working on Mac

```bash
# Edit files locally
./run.sh -vv                        # test locally
./scripts/push.sh --bump "fix: ..." # push with version bump
# Then on HA SSH:
./scripts/fetch.sh && ./scripts/ha_deploy.sh
```

#### Quick test without deploy (HA)

To test a modified `run.sh` without rebuilding the image:

```bash
docker cp run.sh addon_fc4e2b3e_tado_planning:/run.sh
./run.sh -vv
```

This change is temporary — it will be overwritten at the next deploy.

---

### Versioning

The version in `config.json` follows `MAJOR.MINOR.PATCH`:

| Change type | Version bump | How |
|------------|--------------|-----|
| Any fix, improvement, new file | Patch: `1.0.x → 1.0.x+1` | `push.sh --bump` |
| New significant feature, backward compatible | Minor: `1.0.x → 1.1.0` | Edit `config.json` manually, then `push.sh` |
| Breaking change, format change, migration required | Major: `1.x.x → 2.0.0` | Edit `config.json` manually, then `push.sh` |

For minor/major bumps, edit `config.json` manually first:

```bash
jq '.version = "1.1.0"' config.json > config.json.tmp && mv config.json.tmp config.json
./scripts/push.sh "release: v1.1.0 — description"
```

The version is used by the HA store to detect available updates. A bump is only required when you want HA to propose an update — for development iterations you can push without bumping and use `ha_deploy.sh` to force a rebuild.

---

### Adding a new schedule config

1. Create `schedules/myconfig.json` with the zones and slots you want
2. Reference it in `planning_standard.json` with `"config": "myconfig"`
3. For a level 2 config, only define the zones you want to override
4. Test: `./run.sh -c myconfig.json` or `./run.sh -d YYYY-MM-DD -vvv`
5. Push and deploy

---

### Known issues and quirks

| Issue | Details |
|-------|---------|
| PyTado enum comparisons | Zone type comparisons require `.value`; patched in `tado_planning.py` |
| OAuth URL trailing slash | PyTado ≥ 0.18 requires trailing slash on token URL; patched |
| Token file format | Must be `{"refresh_token": "..."}` JSON, not plain text |
| HAOS overlay filesystem | `docker build --no-cache` required in `ha_deploy.sh` to avoid stale layers |
| launchd env isolation | launchd agents don't inherit shell env vars; all paths declared explicitly in the plist via `EnvironmentVariables` |
| `run.sh` in container | `run.sh` is baked into the Docker image at build time. Changes to `run.sh` require `ha_deploy.sh` to take effect. Use `docker cp` for quick tests. |

---

## Credits

This project was built in collaboration:
- **Concept, specification and domain expertise** — [manW13-be](https://github.com/manW13-be)
- **Implementation, debugging and documentation** — [Claude](https://claude.ai) (Anthropic)

---

## License

MIT
