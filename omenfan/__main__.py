"""Entry point for omenfan package."""

import argparse
import curses
import os
import signal
import sys

from . import __version__
from .detect import detect
from .ec import open_ec, ECDummy
from .fan import FanController, PROFILE_NONE, PROFILE_MAX, PROFILE_AGGRESSIVE
from .hwmon import restore_all_hwmon_fans
from .sensors import SensorCollector
from .tui import OmenFanTUI


def dump_ec(caps):
    """Print raw EC register dump for community contributions."""
    if caps.ec_method == "none":
        print("ERROR: No EC access available (need root + /dev/mem or ec_sys)")
        return 1

    ec = open_ec(caps.ec_method, caps.ec_base, writable=False)
    try:
        print(f"EC Register Dump — {caps.product_name} (board: {caps.board_name})")
        print(f"EC method: {caps.ec_method}, base: 0x{caps.ec_base:08X}")
        print()
        data = ec.read_bytes(0, 0x400)
        for offset in range(0, len(data), 16):
            hex_vals = " ".join(f"{data[offset+i]:02X}" for i in range(min(16, len(data) - offset)))
            ascii_vals = "".join(
                chr(data[offset+i]) if 32 <= data[offset+i] < 127 else "."
                for i in range(min(16, len(data) - offset))
            )
            print(f"  {offset:04X}: {hex_vals:<48}  {ascii_vals}")
    finally:
        ec.close()
    return 0


def main_tui(stdscr, caps, profile, monitor_only):
    """Curses-wrapped main function."""
    # Open EC
    if monitor_only or caps.ec_method == "none":
        ec = ECDummy()
        ec.open()
    else:
        ec = open_ec(caps.ec_method, caps.ec_base, writable=True)

    fan_ctl = None
    try:
        # Create fan controller (with hwmon fans for universal support)
        fan_ctl = FanController(
            ec, caps.ec_regmap,
            wmi_path=caps.wmi_path or None,
            hwmon_fans=caps.hwmon_fans,
        )
        if not monitor_only:
            fan_ctl.initialize()
            if profile == PROFILE_MAX:
                fan_ctl.apply_max()
            elif profile == PROFILE_AGGRESSIVE:
                fan_ctl.profile = PROFILE_AGGRESSIVE

        # Install signal handlers for clean fan restore on kill
        def _signal_cleanup(signum, frame):
            if fan_ctl and not monitor_only:
                fan_ctl.cleanup()
            sys.exit(128 + signum)

        signal.signal(signal.SIGTERM, _signal_cleanup)
        signal.signal(signal.SIGINT, _signal_cleanup)

        # Create sensor collector
        sensors = SensorCollector(caps)

        # Run TUI
        tui = OmenFanTUI(stdscr, ec, caps, fan_ctl, sensors, monitor_only)
        if profile == PROFILE_MAX:
            fc = fan_ctl.fan_count
            tui.set_status(f"FULL SPEED \u2014 all {fc} fans max")
        elif profile == PROFILE_AGGRESSIVE:
            tui.set_status("Aggressive curve active")
        tui.run()
    finally:
        if fan_ctl and not monitor_only:
            fan_ctl.cleanup()
        ec.close()


def entry():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="omenfan",
        description="Linux fan control and system monitoring TUI"
    )
    parser.add_argument("--version", action="version",
                        version=f"omenfan {__version__}")
    parser.add_argument("--max", action="store_true",
                        help="Start with all fans at 100%%")
    parser.add_argument("--aggressive", "--aggro", action="store_true",
                        help="Start with temp-based aggressive curve")
    parser.add_argument("--monitor-only", action="store_true",
                        help="System monitoring only, no fan control")
    parser.add_argument("--dump-ec", action="store_true",
                        help="Print raw EC register dump and exit")
    args = parser.parse_args()

    # Detect hardware
    caps = detect()

    # --dump-ec mode
    if args.dump_ec:
        sys.exit(dump_ec(caps))

    # Determine profile
    profile = PROFILE_NONE
    if args.max:
        profile = PROFILE_MAX
    elif args.aggressive:
        profile = PROFILE_AGGRESSIVE

    # Auto-enable monitor-only when not root or no control available
    monitor_only = args.monitor_only
    has_any_control = (caps.ec_method != "none" or caps.wmi_available
                       or any(f.can_control for f in caps.hwmon_fans))
    if os.geteuid() != 0:
        monitor_only = True
    elif not has_any_control:
        monitor_only = True

    if monitor_only and not args.monitor_only:
        if os.geteuid() != 0:
            caps.warnings.insert(0, "Not root — running in monitor-only mode")
        elif not has_any_control:
            caps.warnings.insert(0, "No fan control available — monitor-only mode")

    curses.wrapper(lambda stdscr: main_tui(stdscr, caps, profile, monitor_only))


if __name__ == "__main__":
    entry()
