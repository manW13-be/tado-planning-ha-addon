# Developer Guide — tado-planning

This guide covers the internal architecture, CLI reference, scripts reference, and contribution workflow.

---

## Architecture

### Core script — `tado_planning.py`

The main script runs identically on both macOS and Home Assistant. Platform differences are handled at runtime:

```python
if platform.system() == "Darwin":
    # macOS paths
else:
    # Linux / HAOS paths
```

Environment variables override defaults on both platforms:

| Variable | Default (macOS) | Default (HAOS) |
|----------|-----------------|----------------|
| `TADO_TOKEN_FILE` | `<project>/.tado_token` | `/data/tado_refresh_token` |
| `TADO_SCHEDULES_DIR` | `<project>/schedules` | `/homeassistant/tado-planning/schedules` |

### Schedule selection logic

1. Load `planning.json` from `TADO_SCHEDULES_DIR`
2. Compute the current ISO week number and determine odd/even
3. Build the list of events for the current two-week cycle (odd + even weeks combined)
4. Find the last past event — i.e. the most recent event whose `(week_type, day, time)` is before now
5. If no event is found in the current cycle → wrap around to the previous cycle
6. Apply level 1 config first (full zone replacement), then level 2 config on top (partial override)
7. Compare with the last applied state (stored in a state file) — push to Tado only if changed

### Two-level config system

- **Level 1** events replace the full schedule for all zones they define
- **Level 2** events overlay only the zones they define, leaving all other zones at their level 1 state
- When a new level 1 event is reached, it replaces everything; any previous level 2 override is discarded

### Token management

Authentication uses Tado's OAuth2 device flow via the `PyTado` library (≥ 0.18). The refresh token is persisted to disk and reused across runs. If the token is missing or expired, the script re-authenticates interactively (prints a browser URL).

**Known PyTado 0.18/0.19 quirks patched in `tado_planning.py`:**
- Zone type enum comparisons require `.value` explicitly
- OAuth token URL requires a trailing slash
- Token file format: `{"refresh_token": "..."}` (JSON, not plain text)

### HA add-on container

- Base image: `ghcr.io/home-assistant/aarch64-base`
- Built for aarch64 (ODROID N2+)
- Entrypoint: `run.sh` — installs Python dependencies then launches `tado_planning.py` in a loop
- The add-on runs hourly using an internal sleep loop aligned to the clock hour

---

## CLI reference

```bash
# Automatic selection via planning.json
python3.11 tado_planning.py

# Force an alternate planning file
python3.11 tado_planning.py -p schedules/myplanning.json

# Force a weekconfig directly (level 1 only, bypasses planning.json entirely)
python3.11 tado_planning.py -c schedules/vacancewithkids.json

# Simulate a specific date (useful for testing wrap-around and edge cases)
python3.11 tado_planning.py -d 2026-03-10

# Verbosity (stackable)
python3.11 tado_planning.py -v      # active config contents
python3.11 tado_planning.py -vv     # + all cycle candidates
python3.11 tado_planning.py -vvv    # + API blocks sent
python3.11 tado_planning.py -vvvv   # + raw PUT/GET requests
```

---

## Scripts reference

All scripts live in `scripts/` and auto-detect the project root (one level up from their own location).

### macOS scripts

#### `mac_fetch.sh`

Pulls the latest changes from GitHub and syncs `.gitignore → gitignore` so the Finder/Samba-visible copy stays up to date.

```bash
./scripts/mac_fetch.sh
```

#### `mac_push.sh`

Fetches the current version from GitHub, bumps the patch version in `config.json`, commits all local changes, and pushes to `origin/main`. Syncs `gitignore → .gitignore` before committing.

```bash
./scripts/mac_push.sh "your commit message"
```

- Version bump: `1.0.8` → `1.0.9` (always based on the remote version, not local)
- Default commit message: `update vX.Y.Z`

---

### Home Assistant scripts

> All HA scripts must be run from an SSH session on the HA host, from inside the project directory.

#### `ha_fetch_and_deploy.sh`

Full redeploy pipeline: pulls from GitHub, cleans old Docker images, rebuilds the image, restarts the add-on, and tails the last 15 log lines.

```bash
./scripts/ha_fetch_and_deploy.sh
```

Steps:
1. `git pull origin main`
2. Sync `.gitignore → gitignore`
3. Remove old Docker images for this add-on
4. `docker build --no-cache` with the new version tag
5. `ha apps restart`
6. `ha apps logs | tail -15`

Use this after pushing from Mac when you want HA to pick up changes immediately, without waiting for the store update.

> `--no-cache` is required due to HAOS overlay filesystem behaviour — without it, Docker may reuse stale layers.

#### `ha_push.sh`

Same as `mac_push.sh` but runs from the HA host.

```bash
./scripts/ha_push.sh "your commit message"
```

#### `ha_debug.sh`

Runs `tado_planning.py` manually inside the running container, forwarding all arguments to the script. If the container is not running, starts the add-on first automatically.

```bash
./scripts/ha_debug.sh -v
./scripts/ha_debug.sh -vvv
./scripts/ha_debug.sh -d 2026-01-06        # simulate a specific date
./scripts/ha_debug.sh -c kidspresent       # force a specific config
./scripts/ha_debug.sh -p myplanning.json   # use an alternate planning file
```

#### `ha_clean.sh`

Full reset for testing a fresh install. Stops the add-on, removes Docker containers and images, and deletes add-on data.

```bash
./scripts/ha_clean.sh                          # removes everything
./scripts/ha_clean.sh --keep-schedules         # preserves schedule JSON files
./scripts/ha_clean.sh --keep-token             # preserves the refresh token
./scripts/ha_clean.sh --keep-schedules --keep-token
```

After cleaning, reinstall from the HA UI or run `ha_fetch_and_deploy.sh`.

---

## The `gitignore` / `.gitignore` duality

`.gitignore` starts with a dot and is invisible in Finder and via Samba. To allow editing it without a terminal, the scripts maintain a visible copy called `gitignore` (no dot). Push scripts sync `gitignore → .gitignore` before committing; fetch scripts sync `.gitignore → gitignore` after pulling.

---

## Development workflow

### Mac → HA

```
1. Edit code on Mac
2. Test locally:   python3.11 tado_planning.py -vv
3. Push:           ./scripts/mac_push.sh "fix: something"
4. On HA (SSH):    ./scripts/ha_fetch_and_deploy.sh
5. Check logs:     ha apps logs fc4e2b3e_tado_planning
```

### HA → Mac

```
1. Edit files on HA or via Samba
2. Push from HA:   ./scripts/ha_push.sh "update schedules"
3. On Mac:         ./scripts/mac_fetch.sh
```

### Testing a specific scenario

```bash
# Simulate odd week, Thursday morning (should be in kidsabsent)
python3.11 tado_planning.py -d 2026-01-08 -vvv

# Simulate wrap-around: Monday of an odd week before any event has passed
python3.11 tado_planning.py -d 2026-01-05 -vv
```

---

## Adding a new schedule config

1. Create `schedules/myconfig.json` with the zones and slots you want
2. Add events referencing `"config": "myconfig"` in `planning.json`
3. If it's a level 2 config, only define the zones you want to override
4. Test with `-c myconfig.json` and `-d` to simulate the target date
5. Push and redeploy

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `PyTado` | ≥ 0.18 | Tado API client (OAuth, schedule push/fetch) |

Install locally:
```bash
pip install "python-tado>=0.18"
```

---

## Known issues and quirks

| Issue | Details |
|-------|---------|
| PyTado enum comparisons | Zone type comparisons require `.value`; patched in `tado_planning.py` |
| OAuth URL trailing slash | PyTado ≥ 0.18 requires trailing slash on token URL; patched |
| Token file format | Must be `{"refresh_token": "..."}` JSON, not plain text |
| HAOS overlay filesystem | `docker build --no-cache` required to avoid stale layers |
| launchd env isolation | launchd agents don't inherit shell env vars; all paths declared in plist via `EnvironmentVariables` |
