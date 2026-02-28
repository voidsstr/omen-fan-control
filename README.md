# omenfan

A terminal-based system monitor and fan controller for HP OMEN desktops and laptops. Pure Python, zero dependencies — uses only the standard library and Linux sysfs/proc interfaces.

```
╭── OMEN FAN CONTROL ── Ultra 9 285K · 24C · 62 GB · Balanced · Auto ── ◐ ──╮
│  nsc · Ubuntu 24.04 · 6.17.0-14 · ↑ 10h 23m · 812 procs · HB:142        │
╰───────────────────────────────────────────────────────────────────────────────╯
╭─ CPU ──────────────────────╮ ╭─ Fans ─────────────────────╮ ╭─ GPU ──────────╮
│  Usage ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶░░  42% │ │  ⣏⣹ Rear 1  ⣿⣿⣿⣿⣿⣿⣿⣾░  2430│ │  RTX 5090      │
│  Freq  ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿  4.2G│ │  ⣏⣹ Rear 2  ⣿⣿⣿⣿⣿⣿⣤░░   480│ │  Util ⣿⣿⣿░  85%│
│  Load  1.24  0.98  1.05    │ │  ⣏⣹ Front 1 ⣿⣿⣿⣿⣿⣿⣿⣿⣿  2580│ │  Pwr  ⣿⣿⣿░ 387W│
│  use ⣠⣤⣴⣶⣾⣿⣾⣶⣴⣤⣠⣀⣀⣠⣤⣴⣶⣾⣿│ │  ⣏⣹ Front 2 ⣿⣿⣿⣿⣿⣿⣿⣿⣿  2610│ │  VRAM ⣿⣿░ 22/32G│
│  tmp ⣀⣠⣤⣤⣠⣀⣀⣀⣠⣤⣶⣾⣿⣾⣶⣤⣠⣀⣀│ │  ⣏⣹ Front 3 ⣿⣿⣿⣿⣿⣿⣿⣿⣿  2540│ ╰────────────────╯
│  cores ⣶⣴⣤⣾⣤⣴⣶⣾⣤⣠⣶⣴              │ ╰────────────────────────────╯ ╭─ Memory ───────╮
╰────────────────────────────╯ ╭─ Temperatures ──────────────╮ │  RAM ⣿⣿⣿░ 11/62G│
╭─ Network ──────────────────╮ │  CPU   ⣿⣿⣿⣿⣿⣶░░░░░  56°C  │ │  Swap ⣀░░░ 1.7/8G│
│  enp129s0  ▼  1.2M/s ▲340K│ │  Board ⣿⣿⣠░░░░░░░░  28°C  │ ╰────────────────╯
│  wlan0     ▼   42K/s ▲ 12K│ │  VRM   ⣿⣿⣿⣤░░░░░░░  40°C  │ ╭─ Disk ─────────╮
│  rx ⣀⣠⣤⣶⣾⣿⣾⣶⣤⣠⣀⣀⣠⣤⣶⣾⣿⣾⣶⣤⣠│ │  DIMM  ⣿⣠░░░░░░░░░  30°C  │ │  / ⣿⣿⣿⣿⣿⣿⣤░454/955G│
╰────────────────────────────╯ │  NV GPU⣿⣿⣿⣿⣶░░░░░░  62°C  │ ╰────────────────╯
                               ╰────────────────────────────╯
╭─ Fan Curves ──────────────────────────────────────────────────────────────────╮
│  Balanced     20→600  26→800  30→1000  40→1400  50→1800  60→2400  70→3200    │
│  Quiet        20→500  26→600  30→700  40→900  50→1200  60→1600  70→2000      │
│  Performance  20→800  26→1000  30→1200  40→1600  50→2000  60→2800  70→3200   │
│  Aggressive   40→30%  50→50%  60→70%  70→85%  80→100%  90→100%               │
│  Max          100%                                                            │
╰───────────────────────────────────────────────────────────────────────────────╯
1Bal 2Qt 3Perf 4Max 5Aggro 6Edit 7- 8+ qQuit
● Balanced mode active
```

## Features

- **Fan control** — Switch between Quiet, Balanced, and Performance BIOS profiles. Force max fan speed or apply a temperature-based aggressive curve. Edit individual fan curve points in the EC.
- **Live monitoring** — CPU usage, frequency, load, per-core bars. Memory, swap, cache breakdown. Disk usage and I/O rates. Network RX/TX per interface. NVIDIA GPU utilization, power, VRAM, clocks. Battery status on laptops.
- **Braille graphics** — Smooth filled line graphs and gradient bars using Unicode braille characters. Animated fan spinner icons that scale with RPM.
- **Adaptive layout** — Three-column (120+ cols), two-column (80+), or stacked single-column (50+) layouts that respond to terminal size.
- **Multi-model support** — 67+ known HP OMEN board IDs across desktop (45L, 30L, 880), OMEN laptop (15/16/17 from 2019-2023+), HP Victus, and Victus S families. Unknown boards get a best-guess generic register map.
- **Graceful degradation** — Runs as a system monitor without root. Each sensor independently try/excepted — a broken nvidia-smi or missing EC doesn't crash the TUI.
- **Zero dependencies** — Pure Python 3.8+ standard library. No pip packages needed.

## Requirements

- Linux (kernel 4.x+)
- Python 3.8+
- Root access for fan control (optional — runs in monitor-only mode without root)
- `acpi_call` kernel module for WMI BIOS interface (fan profiles, performance modes)
- `/dev/mem` access for desktop EC mmap, or `ec_sys` module for laptop EC access

## Installation

### Quick start (no install)

```bash
git clone https://github.com/voidsstr/omen-fan-control.git
cd omen-fan-control

# Full mode (fan control + monitoring):
sudo python3 -m omenfan

# Monitor-only (no root needed):
python3 -m omenfan
```

### Install as package

```bash
pip install .
sudo omenfan
```

### Install acpi_call (required for fan control)

The `acpi_call` kernel module provides the WMI BIOS interface used to switch fan profiles and performance modes.

| Distro | Command |
|--------|---------|
| Ubuntu / Debian / Mint / Pop!_OS | `sudo apt install acpi-call-dkms` |
| Arch / Manjaro / EndeavourOS | `sudo pacman -S acpi_call` |
| openSUSE | `sudo zypper install acpi_call-kmp-default` |
| Fedora / RHEL | Build from source with DKMS |
| Gentoo | `sudo emerge sys-power/acpi_call` |
| NixOS | Add `acpi_call` to `boot.extraModulePackages` |
| Void | `sudo xbps-install -S acpi_call-dkms` |

Load the module:

```bash
sudo modprobe acpi_call
```

To load on boot, add `acpi_call` to `/etc/modules-load.d/acpi_call.conf`.

## Usage

```
omenfan [OPTIONS]

Options:
  --max             Start with all fans at 100%
  --aggressive      Start with temperature-based aggressive curve
  --monitor-only    System monitoring only, no fan control
  --dump-ec         Print raw EC register dump and exit
  --version         Show version
```

### Keyboard Controls

| Key | Action |
|-----|--------|
| `1` | Balanced profile |
| `2` | Quiet profile |
| `3` | Performance profile |
| `4` | Toggle max fan speed (all fans 100%) |
| `5` | Toggle aggressive temperature curve |
| `6` | Enter/exit fan curve editor |
| `7` | All curve points -100 RPM |
| `8` | All curve points +100 RPM |
| Arrow keys | Navigate curve points / adjust RPM (in edit mode) |
| `q` / `Esc` | Quit (restores fans to safe state) |

### Auto-detection behavior

- **Not root** — Automatically enters monitor-only mode. All sysfs/proc sensors still work (CPU, memory, disk, network, GPU, thermals). No fan control.
- **Root, no acpi_call** — EC-only mode. Desktop front fan duty works via EC registers. No WMI profile switching.
- **Root, no EC** — WMI-only mode. Can switch profiles but can't read EC temperatures or fan RPMs.
- **Root, full access** — EC + WMI. Full fan control and monitoring.
- **Non-OMEN hardware** — Pure system monitor. No EC or WMI panels, but CPU/memory/disk/network/GPU/thermal monitoring works on any Linux machine.

## How It Works

### Architecture

```
omenfan/
├── __init__.py     # Package version
├── __main__.py     # Entry point, argument parsing, curses wrapper
├── detect.py       # Hardware detection and capability probing
├── ec.py           # EC access: mmap (/dev/mem), ec_sys, dummy fallback
├── wmi.py          # WMI BIOS interface via acpi_call
├── sensors.py      # System metrics (CPU, mem, disk, net, GPU, thermal, battery)
├── fan.py          # Fan controller, profiles, curves
├── tui.py          # TUI rendering and input handling
├── widgets.py      # Rendering primitives (boxes, bars, sparklines, braille icons)
└── theme.py        # Color scheme and gradient definitions
```

### EC (Embedded Controller)

HP OMEN systems expose an Embedded Controller that provides direct access to hardware sensors and fan control registers.

**Desktops** use a memory-mapped EC at `0xFD500000` (1024 bytes), accessed via `/dev/mem`. This contains temperature registers (CPU, board, VRM, DIMM), fan RPM counters (16-bit big-endian), fan duty registers (PWM 0-100 for front fans), performance mode state, and stored fan curves (Balanced, Quiet, Performance with temp→RPM point pairs).

**Laptops** use the standard ACPI EC at port I/O addresses, accessed via `/sys/kernel/debug/ec/ec0/io` (the `ec_sys` kernel module). Laptop registers follow the OmenMon layout: CPU temp at `0x57`, GPU temp at `0xB7`, fan RPMs at `0xB0-0xB3`, manual mode toggle at `0x62`, and fan speed percentage registers at `0x2C-0x2D`.

Desktop BIOS only controls the 2 rear fans through thermal profiles. The 3 front fans have no BIOS curve — omenfan manages them directly via EC PWM duty registers with a temperature-based curve.

### WMI BIOS Interface

WMI calls go through `/proc/acpi/call` (the `acpi_call` kernel module). Each call packs a 16-byte header (signature `SECU`, command type `0x20008`, command ID, data length) plus a variable-length payload, then writes it as an ACPI method call to `\_SB.WMID.WMAA` (desktops) or `\_SB.WMI.WMAA` (laptops).

Key WMI commands:
- `0x10` — Fan count query (also used as keepalive)
- `0x29` — Set performance mode (0=Quiet, 1=Balanced, 3=Performance)
- `0x2E` — Set fan speeds (2 bytes = rear only, 6 bytes = all 5 fans)
- `0x27` — Toggle max fan mode

### Fan Profiles

| Profile | Rear Fans | Front Fans | Description |
|---------|-----------|------------|-------------|
| Auto (Balanced/Quiet/Perf) | BIOS-controlled via WMI | EC duty from temp curve | BIOS manages rear fans; omenfan manages front fans based on CPU temperature |
| Aggressive | WMI duty bytes | EC duty from aggressive curve | Temperature-reactive: 30% at 40C up to 100% at 80C+ |
| Max | WMI 0xE0 all fans | EC duty 100% | All fans full speed |

### Sensor Collection

All sensors are read from standard Linux interfaces — no proprietary tools or libraries needed:

| Sensor | Source |
|--------|--------|
| CPU usage (overall + per-core) | `/proc/stat` delta between reads |
| CPU frequency | `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq` |
| Load average | `/proc/loadavg` |
| Memory / swap | `/proc/meminfo` |
| Disk usage | `os.statvfs()` on mounted filesystems |
| Disk I/O | `/proc/diskstats` delta |
| Network RX/TX | `/sys/class/net/*/statistics/` delta |
| NVIDIA GPU | `nvidia-smi` CSV query (temp, fan, util, power, VRAM, clocks) |
| Thermal zones | `/sys/class/hwmon/*/temp*_input` and `/sys/class/thermal/thermal_zone*/temp` |
| EC temps/RPMs | Direct EC register reads (mmap or ec_sys) |
| Battery | `/sys/class/power_supply/BAT*/` |

Each sensor is independently try/excepted with failure counting. After 3 consecutive failures, a sensor is disabled to avoid repeated error overhead.

## Supported Models

### Desktops (memory-mapped EC)

| Board ID | Model |
|----------|-------|
| `8D2C` | OMEN 45L GT22-2xxx |
| `89EB` | OMEN Desktop |
| `8703` | OMEN 30L GT13 |
| `8437` | OMEN Desktop 880 |

### OMEN Laptops

67 known board IDs across generations:
- **v0 thermal** (6 boards): 8607, 8746-874A
- **v1 thermal** (49 boards): OMEN 15/16/17 from 2019 through 2023+ (84DA-8BAD)
- **Victus** (1 board): 8A25
- **Victus S** (7 boards): 8BBE, 8BD4-8D41

Unknown OMEN boards are detected via DMI product name and assigned a generic register map based on chassis type (laptop vs desktop).

## EC Register Dump

If your OMEN model isn't in the database, you can contribute a register dump:

```bash
sudo python3 -m omenfan --dump-ec > ec_dump.txt
```

This prints the raw EC register space with your board name and EC method. Open an issue with the dump and your model name to help expand hardware support.

## Kernel Lockdown

On systems with Secure Boot enabled, kernel lockdown blocks `/dev/mem` access. Options:

1. **Disable Secure Boot** in BIOS — removes lockdown entirely
2. **Use `ec_sys` instead** — omenfan automatically falls back to ec_sys if mmap fails (with reduced register access on desktops)
3. **Monitor-only mode** — all sysfs/proc sensors work regardless of lockdown

## License

MIT
