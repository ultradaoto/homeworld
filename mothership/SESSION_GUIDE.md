# Fleet Command — Session Guide

## Boot

```bash
cd D:\Projects\Homeworld\mothership
python start.py
```

`start.py` will:
- Initialize both SQLite databases
- Start the bridge file writer (1 Hz, keeps C engine fed)
- Start the build queue, Taiidan AI, combat watcher, sector entry watcher
- Start the Entropy System scanner, enemy commander, and research substation
- Launch the FastAPI server on :3000
- Offer to open the game in windowed mode
- Open the commander TUI

Or run the health check first to verify prerequisites:

```bash
python health.py
```

---

## First time — index the map

```
> mapindex D:/Projects/Homeworld
```

Builds the 3D codebase map — assigns sectors and asteroid coordinates.
Run once per project, or again when the project structure changes significantly.

---

## Set your objective

```
> obj Fix the authentication module and patch the JWT vulnerability
```

---

## Build your initial fleet

```
> ru 1000            ← start with some resources
> scout              ← Kharak Eye (recon)
> scout              ← Dust Runner (second recon)
> resource           ← Ore Seeker (harvests web data)
> interceptor        ← Khar Blade (combat air patrol)
```

---

## Explore the map in-game

Fly your scouts toward dark sectors in-game. Each one you cross triggers
`sendscout` automatically (sector entry watcher), illuminating the files.

Or send scouts from the TUI directly:

```
> sendscout kharak-eye src/auth
> sendscout dust-runner frontend/components
```

---

## Harvest intel

```
> harvest https://owasp.org/www-project-top-ten/
> research
> destroy
```

---

## Watch for enemies

Taiidan appear after ~45 seconds once sectors have been indexed.
Entropy fleet spawns when the vulnerability scanner finds real issues.

```
> enemies            ← list Taiidan inbound
> entropy            ← list active entropy units
> techtree           ← research tree progress
> intercept khar-blade vs TAI_A3F2B1
> duel my-destroyer vs ENT-A1B2C3
```

---

## Research tech tree

```
> techtree
> research ast_mapping
> research database_schema_understanding
```

Completing nodes unlocks new ship classes (frigates, carriers, destroyers).

---

## When ready to commit

```
> run tests
> hyperspace fix: JWT vulnerability patched, auth module hardened
```

---

## Recovery if a ship dies

```
> pods                                    ← list unrecovered escape pods
> salvage                                 ← build a salvage corvette
> recover salvage-corvette-name POD_XXXX  ← recover the pod
```

---

## Useful quick commands

| Command | What it does |
|---------|-------------|
| `fleet` | All active ships |
| `entropy` | Active entropy contacts with HP bars |
| `techtree` | Research tree status |
| `capital status` | Destroyer unlock + enemy destroyers |
| `postbattle <sector>` | Review cleared sector artifact |
| `specialize <callsign>` | Ship domain context |
| `station <callsign> <dir>` | Assign ship home sector |
| `dashboard` | Live fleet status (15 s refresh) |
| `health` | Quick subsystem check |

---

## Shut down

```
> exit
```

`start.py` will automatically terminate the FastAPI server on exit.
All other threads are daemons and stop with the process.
