"""Generic Linux hwmon fan backend.

Scans /sys/class/hwmon/ for fan RPM sensors (fan*_input) and PWM controls
(pwm*). Works with all major Super I/O drivers:

  - Nuvoton NCT6775-6799D (nct6775)   — ASUS, MSI, Gigabyte
  - ITE IT8688E-87xxF (it87)           — Gigabyte, ASUS
  - Fintek F71xxx (f71882fg)           — ASRock
  - Winbond W836xx (w83627ehf)         — legacy boards
  - Dell laptops (dell-smm-hwmon)
  - ThinkPad (thinkpad_acpi)
  - Apple (applesmc)
"""

import glob
import os
from dataclasses import dataclass, field

# Minimum PWM duty (25% of 255) — safety floor to prevent fan stall
MIN_PWM = 64


@dataclass
class HwmonFan:
    """One fan discovered via /sys/class/hwmon."""
    hwmon_path: str       # "/sys/class/hwmon/hwmon4"
    hwmon_name: str       # "nct6775", "it87", "dell_smm", etc.
    fan_index: int        # 1-based (fan1_input → 1)
    label: str            # From fan*_label or "{name} Fan {N}"
    rpm_path: str         # Path to fan*_input
    pwm_path: str = ""    # Path to pwm* (empty if no control)
    pwm_enable_path: str = ""
    can_control: bool = False
    original_pwm_enable: int = -1  # Saved for restore on exit
    original_pwm_value: int = -1


@dataclass
class HwmonFanState:
    """Current state of one hwmon fan."""
    rpm: int = 0
    pwm: int = 0          # Raw 0-255
    duty_pct: int = 0     # Derived: pwm / 255 * 100
    pwm_enable: int = -1  # 0=off, 1=manual, 2=auto


def _read_sysfs(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def _write_sysfs(path, value):
    try:
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except Exception:
        return False


def discover_hwmon_fans(skip_names=frozenset()):
    """Scan /sys/class/hwmon for fans.

    Args:
        skip_names: Set of hwmon driver names to skip (e.g. {"hp_wmi"}
                    to avoid double-counting on OMEN systems).

    Returns:
        List of HwmonFan objects, sorted by hwmon path + fan index.
    """
    fans = []
    for hwmon_path in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = _read_sysfs(os.path.join(hwmon_path, "name"))
        if not name:
            continue

        # Skip explicitly excluded drivers
        if name in skip_names:
            continue
        # Skip virtual/ACPI fan devices that just report on/off
        if name in ("acpi_fan",):
            continue

        # Find all fan*_input files
        for fan_file in sorted(glob.glob(os.path.join(hwmon_path, "fan*_input"))):
            basename = os.path.basename(fan_file)  # "fan1_input"
            idx_str = basename.replace("fan", "").replace("_input", "")
            try:
                fan_idx = int(idx_str)
            except ValueError:
                continue

            # Check if this fan is actually reporting data
            rpm_str = _read_sysfs(fan_file)
            if not rpm_str:
                continue
            try:
                rpm = int(rpm_str)
            except ValueError:
                continue
            # Skip fans that read 0 and have no PWM — likely not connected
            # (but keep 0 RPM fans if we can control them, they might be stopped)

            # Label
            label_path = os.path.join(hwmon_path, f"fan{fan_idx}_label")
            label = _read_sysfs(label_path)
            if not label:
                label = f"{name} Fan {fan_idx}"

            fan = HwmonFan(
                hwmon_path=hwmon_path,
                hwmon_name=name,
                fan_index=fan_idx,
                label=label,
                rpm_path=fan_file,
            )

            # Check for matching PWM control
            pwm_path = os.path.join(hwmon_path, f"pwm{fan_idx}")
            pwm_enable_path = os.path.join(hwmon_path, f"pwm{fan_idx}_enable")
            if os.path.exists(pwm_path):
                fan.pwm_path = pwm_path
                fan.pwm_enable_path = pwm_enable_path
                # Check if we can actually write to it
                fan.can_control = os.access(pwm_path, os.W_OK)

            # Skip fans with 0 RPM and no control (disconnected header)
            if rpm == 0 and not fan.can_control:
                continue

            fans.append(fan)

    return fans


def read_hwmon_fan(fan):
    """Read current state of one hwmon fan.

    Returns:
        HwmonFanState with current RPM, PWM, duty%, and mode.
    """
    state = HwmonFanState()

    rpm_str = _read_sysfs(fan.rpm_path)
    if rpm_str:
        try:
            state.rpm = int(rpm_str)
        except ValueError:
            pass

    if fan.pwm_path:
        pwm_str = _read_sysfs(fan.pwm_path)
        if pwm_str:
            try:
                state.pwm = int(pwm_str)
                state.duty_pct = round(state.pwm / 255 * 100)
            except ValueError:
                pass

    if fan.pwm_enable_path:
        en_str = _read_sysfs(fan.pwm_enable_path)
        if en_str:
            try:
                state.pwm_enable = int(en_str)
            except ValueError:
                pass

    return state


def set_hwmon_fan_manual(fan, duty_pct):
    """Set a hwmon fan to manual mode at a specific duty cycle.

    Args:
        fan: HwmonFan with can_control=True
        duty_pct: 0-100 duty cycle (clamped to minimum 25%)

    Returns:
        True if successful.

    Safety: PWM writes are clamped to minimum 25% (64/255) to prevent
    fan stall on systems where the minimum varies.
    """
    if not fan.can_control:
        return False

    # Save original state on first manual write
    if fan.original_pwm_enable == -1:
        state = read_hwmon_fan(fan)
        fan.original_pwm_enable = state.pwm_enable
        fan.original_pwm_value = state.pwm

    # Switch to manual mode (pwm_enable=1) if not already
    if fan.pwm_enable_path and os.path.exists(fan.pwm_enable_path):
        _write_sysfs(fan.pwm_enable_path, 1)

    # Clamp duty to safe range
    duty_pct = max(25, min(100, duty_pct))
    pwm = round(duty_pct / 100.0 * 255)
    pwm = max(MIN_PWM, min(255, pwm))

    return _write_sysfs(fan.pwm_path, pwm)


def set_hwmon_fan_auto(fan):
    """Restore a hwmon fan to automatic (BIOS) control.

    Sets pwm_enable=2 (automatic/thermal cruise) which lets the
    motherboard's firmware manage the fan speed.

    Returns:
        True if successful.
    """
    if not fan.can_control or not fan.pwm_enable_path:
        return False

    return _write_sysfs(fan.pwm_enable_path, 2)


def restore_all_hwmon_fans(fans):
    """Restore all hwmon fans to their original state.

    Called on exit to ensure fans return to BIOS control.
    """
    for fan in fans:
        if not fan.can_control:
            continue
        try:
            if fan.original_pwm_enable >= 0:
                # Restore original pwm_enable mode
                if fan.pwm_enable_path:
                    _write_sysfs(fan.pwm_enable_path, fan.original_pwm_enable)
                # Restore original PWM value if it was in manual mode
                if fan.original_pwm_enable == 1 and fan.original_pwm_value >= 0:
                    _write_sysfs(fan.pwm_path, fan.original_pwm_value)
            else:
                # No saved state — just set to auto
                set_hwmon_fan_auto(fan)
        except Exception:
            # Best effort — try auto as last resort
            try:
                set_hwmon_fan_auto(fan)
            except Exception:
                pass
