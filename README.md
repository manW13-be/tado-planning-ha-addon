# tado-planning

Automated Tado heating schedule management based on a **shared custody cycle** (alternating odd/even ISO weeks), with support for independent or layered configurations and exception periods.

---

## Concept

If you share custody of your children and want your heating to automatically reflect their presence or absence — without touching the Tado app every week — this add-on is for you.

The schedule is organized in **two configuration levels**:

- **Level 1** — defines the baseline heating config for a set of zones (e.g. `kidspresent`, `kidsabsent`)
- **Level 2** — defines its own config for a set of zones, independently of level 1

Level 1 and level 2 can reference **completely different zones** — in that case they are fully independent and both apply without interaction. If they reference **the same zone**, level 2 is applied on top of the level 1 settings already pushed to Tado, modifying only what it defines and leaving the rest intact.

Config selection is based on a **two-week cycle** (odd/even ISO week number). The last past event in the current cycle determines the active config. If no event has occurred yet in the current cycle, the last event from the previous cycle applies (wrap-around).

Schedules are only pushed to Tado when the active config actually changes — manual overrides in the Tado app survive until the next event triggers a change.

### Exception plannings

In addition to the standard planning, you can define **exception planning files** that cover a specific date/time period (e.g. school holidays). During that period, the exception planning takes precedence over `planning_standard.json`. Once the period ends, the standard planning resumes automatically.

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

The same Python script (`tado_planning.py`) runs in both environments. Platform detection (`platform.system()`) and environment variables (`TADO_TOKEN_FILE`, `TADO_SCHEDULES_DIR`) handle the path differences between macOS and Linux/HAOS automatically.

---

## Example scenario

**Standard weekly cycle:**
- **Odd weeks**: kids are away → apply `kidsabsent` (lower temperatures in kids' rooms)
- **Even weeks**: kids are home → apply `kidspresent` (normal temperatures in all rooms)

**Level 2 example — cleaning day (even weeks):**
- The cleaning lady arrives on Tuesday morning. Tado doesn't detect her as present, so the house stays in away mode — but the minimum temperature must be raised to 18°C while she works.
- **Tuesday 06:30**: apply `away_18deg` (level 2) — raises the away temperature to 18°C across all zones
- **Tuesday 11:30**: apply `away_15deg` (level 2) — resets the away temperature back to 15°C once she leaves
- Since both level 2 configs and the active level 1 config (`kidspresent`) cover the same zones, the level 2 settings are applied on top of what level 1 already set on Tado.

**Exception planning — Easter holidays:**
- A `planning_paques2026.json` file defines a period from 2026-04-05 to 2026-04-19
- During that period, the standard planning is replaced by `vacancewithkids` applied all week
- After 2026-04-19, `planning_standard.json` resumes automatically

---

## Repository structure

```
tado-planning/
├── tado_planning.py              # Main script (runs on both Mac and HA)
├── config.json                   # HA add-on manifest (version, schema, etc.)
├── Dockerfile                    # aarch64 container for ODROID N2+
├── run.sh                        # Container entrypoint (universal)
├── gitignore                     # Finder/Samba-visible copy of .gitignore
├── schedules/                    # Your personal schedule files (gitignored)
│   ├── planning_standard.json    # Standard two-week cycle definition
│   ├── planning_paques2026.json  # Exception planning (specific period)
│   ├── kidspresent.json          # Level 1 weekconfig — kids at home
│   ├── kidsabsent.json           # Level 1 weekconfig — kids away
│   ├── vacancewithkids.json      # Level 1 weekconfig — school holidays
│   ├── away_15deg.json           # Level 2 weekconfig — away mode at 15°C
│   └── away_18deg.json           # Level 2 weekconfig — away mode at 18°C
├── schedules.tmpl/               # Template files — copied to schedules/ on first run
├── logs/                         # Log files (gitignored)
└── scripts/
    ├── install_launchd.sh        # macOS: install & activate launchd agent
    ├── uninstall_launchd.sh      # macOS: deactivate & remove launchd agent
    ├── mac_fetch.sh              # macOS: pull from GitHub
    ├── mac_push.sh               # macOS: bump version + push to GitHub
    ├── ha_fetch_and_deploy.sh    # HA: pull + rebuild Docker + restart addon
    ├── ha_push.sh                # HA: bump version + push to GitHub
    ├── ha_debug.sh               # HA: run script manually in container
    └── ha_clean.sh               # HA: full reset for fresh install
```

---

## Requirements

- Home Assistant OS (tested on ODROID N2+, aarch64)
- A Tado account with at least one heating zone
- macOS with Python 3.11+ and Homebrew (for the Mac companion setup)
- `jq` installed on both environments

---

## Related documentation

- [User Guide](docs/USER_GUIDE.md) — installation, configuration, token setup, schedule format
- [Developer Guide](docs/DEVELOPER.md) — architecture, scripts reference, contributing

---

## Credits

This project was built in collaboration:
- **Concept, specification and domain expertise** — [manW13-be](https://github.com/manW13-be)
- **Implementation, debugging and documentation** — [Claude](https://claude.ai) (Anthropic)

---

## License

MIT
