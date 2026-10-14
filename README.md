# omenfan

Linux fan control and system monitor. Works on **any PC** with standard hwmon fan drivers, plus full EC/WMI support for HP OMEN hardware. Pure Python, zero dependencies.

<img width="1705" height="686" alt="Screenshot 2026-02-28 at 10 42 16 AM" src="https://github.com/user-attachments/assets/965063b5-4cdd-4231-9cfe-06119d3c32f7" />

## Install

```bash
# One-command install (detects distro, installs lm-sensors + fan drivers):
git clone https://github.com/voidsstr/omen-fan-control.git
cd omen-fan-control
sudo ./install.sh

# Or just run directly (no install needed):
sudo python3 -m omenfan

# Monitor-only (no root):
python3 -m omenfan

# Standalone binary (no install, just copy and run):
sudo python3 omenfan.pyz
```

## Supported Hardware

### Universal (any Linux PC via hwmon)

Works with any motherboard whose Super I/O chip has a Linux driver:

| Chip Family | Driver | Common Boards |
|-------------|--------|---------------|
| Nuvoton NCT6775-6799D | `nct6775` | ASUS, MSI, Gigabyte |
| ITE IT8688E-87xxF | `it87` | Gigabyte, ASUS |
| Fintek F71xxx | `f71882fg` | ASRock |
| Winbond W836xx | `w83627ehf` | Legacy boards |
| SMSC/Microchip SCH5627 | `sch5627` | Server/embedded |
| Dell laptops | `dell-smm-hwmon` | All Dell with fan control |
| ThinkPad | `thinkpad_acpi` | All ThinkPads |
| Apple | `applesmc` | MacBooks running Linux |

The `install.sh` script runs `sensors-detect` to automatically probe and load the right driver for your motherboard.

### HP OMEN (full EC + WMI support)

Full fan curve editing, performance mode switching, and direct EC register access:

- **Desktops**: OMEN 45L GT22 (8D2C), OMEN 30L GT13 (8703), OMEN 880 (8437)
- **Laptops**: 67+ known board IDs — OMEN 15/16/17 (2019-2023+), Victus, Victus S

## Features

- **Fan control** — Max fan speed, temperature-based aggressive curve. HP OMEN: BIOS profile switching (Quiet/Balanced/Performance), fan curve editing.
- **Universal hwmon backend** — Discovers and controls fans via standard Linux `/sys/class/hwmon` interface. Works on any PC with a supported Super I/O driver.
- **Live monitoring** — CPU usage/frequency/load/per-core, memory/swap, disk usage/I/O, network RX/TX, NVIDIA GPU, thermal zones, battery.
- **Braille graphics** — Smooth sparklines and gradient bars using Unicode braille characters. Animated fan spinner icons scaled by RPM.
- **Adaptive layout** — Three-column (120+), two-column (80+), or stacked (50+) based on terminal width.
- **Safe defaults** — Minimum 25% PWM floor prevents fan stall. Original fan modes saved and restored on exit (including SIGTERM/SIGINT).
- **Graceful degradation** — Runs as system monitor without root. Each sensor independently try/excepted.
- **Game server monitoring** — Auto-detects game servers hosted on the machine and live-updates player counts, map, mod/game mode and the full roster with score and ping. See [Game Servers](#game-servers).
- **Fleet host status** — On a retro-agent host, shows everything the login-screen status wall shows: fleet boxes, services, PXE, remote sessions, AI agents, the favourites agent, the game-server watchdog and web sites. See [Fleet Host](#fleet-host).
- **Zero dependencies** — Pure Python 3.8+ stdlib. No pip packages.

## Usage

```
sudo omenfan [OPTIONS]

Options:
  --max             Start with all fans at 100%
  --aggressive      Start with temperature-based aggressive curve
  --monitor-only    System monitoring only, no fan control
  --dump-ec         Print raw EC register dump (HP OMEN only)
  --version         Show version
```

### Keyboard Controls

| Key | Action | Requires |
|-----|--------|----------|
| `1` | Balanced profile | HP OMEN (WMI) |
| `2` | Quiet profile | HP OMEN (WMI) |
| `3` | Performance profile | HP OMEN (WMI) |
| `4` | Toggle max fan speed | Any controllable fan |
| `5` | Toggle aggressive temp curve | Any controllable fan |
| `6` | Enter/exit fan curve editor | HP OMEN (EC curves) |
| `7` / `8` | All curve points -/+100 RPM | HP OMEN (EC curves) |
| Arrow keys | Navigate/adjust curve points | Edit mode |
| `g` | Game server view (in/out) | Hosted game servers detected |
| `f` | Fleet host view (in/out) | retro-agent dashboard collector running |
| `q` / `Esc` | Quit (restores fans) | |

## Game Servers

If the machine is hosting game servers, omenfan finds them and shows live
status alongside the hardware panels — a compact fleet summary on the
dashboard, and a full view with every connected player on `g`.

```
╭─ Game Servers — 10/10 up · 12 playing (8 human · 4 bot) ─────────────────────────╮
│ ● CS 1.6          NSC Retro Fleet Arena (CS 1.6)  cstrike · Counter-Strike  ...  │
│     player                   score   ping     time                               │
│     hazard                      14   28ms     6m                                 │
│ ● Quake III Arena NSC Retro Fleet Arena (Q3A)     baseq3 · Free For All     ...  │
```

Per server: advertised hostname, mod/gamedir, game mode, current map,
population with a fill bar, connect port, and query latency. Per player:
name, score, ping and connected time — whichever of those the engine
actually reports.

### Discovery

Servers are found by reading the kernel's UDP socket table
(`/proc/net/udp`), so the panel only appears on a machine that is really
hosting them, and each server is probed **at the address it actually bound**
— a server bound to one LAN IP rather than `0.0.0.0` would time out if
probed on loopback. Only ports that answer their engine's query become rows.

Polling runs on a background thread every 5s; the render path never blocks
on a UDP timeout.

### Supported engines

| Engine | Games | Query |
|--------|-------|-------|
| GoldSrc / Source | Half-Life, CS 1.6, The Specialists | `A2S_INFO` + `A2S_PLAYER` (with challenge) |
| Quake III | Quake III Arena, OpenArena | `getstatus` |
| Quake II | Quake 2 | `status` |
| QuakeWorld | mvdsv | `status` |
| GameSpy | UT99, UT2004 | `\status\` + `\players\` on game port + 1 |
| Torque | Tribes 2 | binary info request |

Each family needs its own query — probing everything with Quake's
`getstatus` reports Quake 2, UT and Tribes 2 as falsely down. Two protocol
notes worth knowing: modern GoldSrc gates queries behind an anti-reflection
challenge that must be echoed back, and Tribes 2 only answers in plaintext
when bit `0x02` is set in the request flags.

Not every protocol carries every field. A2S reports score and connect time
but no ping; Tribes 2 reports a population but no roster at all. Those show
as `—` rather than a fabricated zero.

### Custom fleets

To override discovery — non-standard ports, or servers on another box —
create `~/.config/omenfan/gameservers.json`:

```json
[
  {"label": "CS 1.6", "engine": "a2s", "port": 27018, "host": "127.0.0.1",
   "unit": "cs16-server", "proxy_port": 27015},
  {"label": "UT99", "engine": "gamespy", "port": 7798, "game_port": 7797}
]
```

`engine` is one of `a2s`, `q3`, `q2`, `qw`, `gamespy`, `torque`.

## Fleet Host

On a machine running [retro-agent](https://github.com/voidsstr/retro-agent)'s
login-screen dashboard (`retro-dashboard-collector`), omenfan shows the same
status the GDM wall does. The dashboard gets a compact **Fleet Host** panel
with one line per section, the header gets `fleet N/M · svc N/M` and a clock,
and `f` opens a full view with every panel:

| Panel | Shows |
|-------|-------|
| Fleet | every retro box: up/offline, reported hostname, OS, RTT, IP |
| Services | the fleet's systemd units (user + system), state, uptime, restarts |
| Server Watchdog | units down and why, restarts this hour, slow starts, browser proxies, recent restart actions |
| PXE | bound sockets (proxyDHCP/TFTP/BINL), boot holds, last client, last file served |
| Favourites Agent | last/next pass, boxes reached, favourites written, live servers by engine |
| Agents | chat daemon + brain health, queue depth, AI runs in flight, last log line |
| Remote | Chrome Remote Desktop, RDP, console occupancy, login sessions |
| Web Sites | per-site agent health, articles published in 7 days, deploys (when the collector publishes them) |

Nothing is gathered by omenfan itself — it reads the collector's published
state file (`/run/retro-dashboard/state.json`, override with
`$OMENFAN_HOST_STATE`) at most every 2s. The collector already does the
network sweeps and `systemctl` queries on its own cadence, and running them
from the render loop would stall the screen. Without the collector the file
is absent and none of this appears.

Statuses use the wall's vocabulary, so a glyph means the same thing in both
places: `●` ok, `◐` running, `○` off on purpose, `·` never installed, `?`
unknown, `⋯` stale, `▲` warn, `‖` blocked, `✕` failed. Only the last three
are faults — a powered-down fleet box is `○`, not red. When the collector's
sample is more than 15s old the header says `host data stale`.

## Crash Recovery

`host/crash-recovery/` reopens a pinned list of Claude Code sessions after the
host reboots or crashes, all as tabs in one Ptyxis window, and tells each one
to check the state of its work and continue. It runs at login, so pair it with
GDM autologin.

```bash
install -m 755 host/crash-recovery/crash-recovery-sessions.sh ~/.local/bin/
install -m 644 host/crash-recovery/crash-recovery-sessions.desktop ~/.config/autostart/
mkdir -p ~/.config/crash-recovery
cp host/crash-recovery/sessions.conf.example ~/.config/crash-recovery/sessions.conf
```

Edit `sessions.conf` to choose which sessions come back (`uuid | cwd | title`,
full UUIDs only). Sessions already running are skipped, and so is any whose
`cwd` no longer exists. A transcript whose last line was cut off by the power
loss is trimmed first, and the cut bytes are kept as
`<uuid>.jsonl.precrash-<timestamp>`. After 4 or more boots in an hour the
sessions reopen without the continue prompt. `--dry-run` prints the commands,
and the log is `~/.local/state/crash-recovery-sessions.log`.

### Auto-detection

- **Not root** — Monitor-only mode. All sysfs/proc sensors work.
- **Root + hwmon fans** — Discovers fans via `/sys/class/hwmon`, controls via PWM.
- **Root + HP OMEN EC/WMI** — Full OMEN fan control + hwmon fans.
- **Non-OMEN, no hwmon fans** — Pure system monitor.

## How It Works

### Architecture

```
omenfan/
├── __main__.py     # Entry point, signal handlers, curses wrapper
├── detect.py       # Hardware detection (DMI, EC, WMI, hwmon)
├── hwmon.py        # Generic hwmon fan discovery + PWM control
├── ec.py           # HP OMEN EC access (mmap, ec_sys, dummy)
├── wmi.py          # HP OMEN WMI BIOS interface (acpi_call)
├── sensors.py      # System metrics (CPU, mem, disk, net, GPU, thermal)
├── fan.py          # Fan controller (EC + WMI + hwmon backends)
├── gameservers.py  # Live game server queries (A2S, Quake, GameSpy, Torque)
├── hoststate.py    # Fleet host panels from retro-agent's dashboard collector
├── tui.py          # TUI rendering and input
├── widgets.py      # Braille bars, sparklines, fan icons
└── theme.py        # Color scheme
```

### Fan Control Priority

1. **HP OMEN EC/WMI** — Used when detected (direct register control, BIOS profiles)
2. **hwmon PWM** — Universal fallback via `/sys/class/hwmon/*/pwm*`
3. **Monitor only** — Read-only fan RPMs when no control interface available

### Safety

- PWM writes clamped to minimum 25% duty (64/255) to prevent fan stall
- Original `pwm_enable` mode + PWM value saved before first manual write
- Restored on exit via `cleanup()`, including on SIGTERM/SIGINT
- HP-specific hwmon drivers (`hp_wmi`) skipped when EC/WMI active (prevents double-counting)

## Kernel Lockdown

On Secure Boot systems, kernel lockdown blocks `/dev/mem`. Options:
1. Disable Secure Boot (removes lockdown)
2. Use `ec_sys` module (automatic fallback)
3. Monitor-only mode (works regardless)

hwmon fan control is **not affected** by kernel lockdown.

## License

MIT
