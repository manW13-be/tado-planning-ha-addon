# tado-planning

Automated Tado heating schedule management based on a **shared custody cycle** (alternating odd/even ISO weeks), with support for layered configuration overrides.

---

## Concept

If you share custody of your children and want your heating to automatically reflect their presence or absence — without touching the Tado app every week — this add-on is for you.

The schedule is organized in **two levels** applied sequentially:

- **Level 1** — full config applied to all defined zones (e.g. `kidspresent`, `kidsabsent`)
- **Level 2** — partial override on top of level 1, only for the zones it defines (e.g. `away_18deg` when you leave for work)

Config selection is based on a **two-week cycle** (odd/even ISO week number). The last past event in the current cycle determines the active config. If no event has occurred yet in the current cycle, the last event from the previous cycle applies (wrap-around).

Schedules are only pushed to Tado when the active config actually changes — manual overrides in the Tado app survive until the next event triggers a change.

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
│  .tado_token          │   │  tado_refresh_token      │
│  schedules/           │   │  /homeassistant/         │
│                       │   │  tado-planning/schedules/│
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

- **Odd weeks**: kids are away → apply `kidsabsent` (lower temperatures in kids' rooms)
- **Even weeks**: kids are home → apply `kidspresent` (normal temperatures)
- **Tuesday 07:00 (even weeks)**: you leave for work → apply `away_18deg` override on your office only
- **Tuesday 11:00 (even weeks)**: you're back → level 2 override expires, level 1 remains active

---

## Repository structure

```
tado-planning/
├── tado_planning.py            # Main script (runs on both Mac and HA)
├── config.json                 # HA add-on manifest (version, schema, etc.)
├── Dockerfile                  # aarch64 container for ODROID N2+
├── run.sh                      # Container entrypoint
├── gitignore                   # Finder/Samba-visible copy of .gitignore
├── schedules/                  # JSON schedule files (gitignored)
│   ├── planning.json           # Event definitions (levels 1 and 2)
│   ├── kidspresent.json        # Level 1 weekconfig
│   ├── kidsabsent.json         # Level 1 weekconfig
│   ├── away_15deg.json         # Level 2 weekconfig (partial override)
│   └── away_18deg.json         # Level 2 weekconfig (partial override)
├── logs/                       # Log files (gitignored)
└── scripts/
    ├── install_launchd.sh      # macOS: install & activate launchd agent
    ├── uninstall_launchd.sh    # macOS: deactivate & remove launchd agent
    ├── mac_fetch.sh            # macOS: pull from GitHub
    ├── mac_push.sh             # macOS: bump version + push to GitHub
    ├── ha_fetch_and_deploy.sh  # HA: pull + rebuild Docker + restart addon
    ├── ha_push.sh              # HA: bump version + push to GitHub
    ├── ha_debug.sh             # HA: run script manually in container
    └── ha_clean.sh             # HA: full reset for fresh install
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

## License

MIT
