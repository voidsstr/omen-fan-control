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
| `q` / `Esc` | Quit (restores fans) | |

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
