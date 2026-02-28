"""Hardware detection and capability probing for HP OMEN systems."""

import glob
import os
import subprocess
from dataclasses import dataclass, field

from .ec import (ECAccess, ECPortIO, ECDummy, EC_MAPS, ECRegisterMap,
                 ECMS_BASE, find_regmap, is_known_board, _ALL_LAPTOP_BOARDS,
                 _OMEN_DESKTOP_BOARDS)
from .hwmon import discover_hwmon_fans
from .wmi import wmi_available, try_load_acpi_call, probe_wmi_path, wmi_fan_count


@dataclass
class SystemCapabilities:
    """Detected hardware capabilities — drives all runtime decisions."""

    # Identity
    product_name: str = ""
    board_name: str = ""
    is_omen: bool = False

    # EC
    ec_method: str = "none"      # "mmap" | "ec_sys" | "none"
    ec_base: int = ECMS_BASE
    ec_regmap: ECRegisterMap = None

    # WMI
    wmi_available: bool = False
    wmi_path: str = ""
    fan_count: int = 0

    # CPU
    cpu_model: str = ""
    cpu_cores: int = 0

    # Memory
    total_ram_gb: float = 0.0

    # GPU
    has_nvidia: bool = False
    nvidia_name: str = ""

    # Sensors
    hwmon_devices: dict = field(default_factory=dict)  # {name: path}
    hwmon_fans: list = field(default_factory=list)     # list[HwmonFan]
    has_hwmon_fans: bool = False
    network_interfaces: list = field(default_factory=list)
    block_devices: list = field(default_factory=list)   # [(name, size_str, model)]
    has_battery: bool = False

    # System
    hostname: str = ""
    kernel: str = ""
    distro: str = ""
    distro_id: str = ""          # ID from os-release (e.g. "ubuntu", "fedora")
    os_release_pretty: str = ""
    chassis_type: int = 0        # DMI chassis type
    is_laptop: bool = False      # Derived from chassis_type
    lockdown: str = "none"       # Kernel lockdown: "none", "integrity", "confidentiality"

    # Issues
    warnings: list = field(default_factory=list)


def _read_file(path, default=""):
    """Read a sysfs/proc file, return stripped content or default."""
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def _check_lockdown():
    """Check kernel lockdown status.

    Returns: "none", "integrity", or "confidentiality"
    """
    content = _read_file("/sys/kernel/security/lockdown")
    if not content:
        return "none"
    # Format: "none [integrity] confidentiality" (brackets = active)
    if "[integrity]" in content:
        return "integrity"
    if "[confidentiality]" in content:
        return "confidentiality"
    if "[none]" in content:
        return "none"
    return "none"


def _detect_chassis():
    """Detect chassis type from DMI.

    Returns: (chassis_type_int, is_laptop_bool)
    Laptop types: 8 (Portable), 9 (Laptop), 10 (Notebook), 14 (Sub Notebook),
                  31 (Convertible), 32 (Detachable)
    """
    val = _read_file("/sys/devices/virtual/dmi/id/chassis_type")
    try:
        ctype = int(val)
    except (ValueError, TypeError):
        return 0, False
    laptop_types = {8, 9, 10, 14, 31, 32}
    return ctype, ctype in laptop_types


def _detect_distro_id():
    """Parse /etc/os-release for distro ID.

    Returns: (distro_id, pretty_name) e.g. ("ubuntu", "Ubuntu 24.04.2 LTS")
    """
    distro_id = ""
    pretty_name = ""
    try:
        for line in open("/etc/os-release"):
            if line.startswith("ID="):
                distro_id = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("PRETTY_NAME="):
                pretty_name = line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return distro_id, pretty_name


def _acpi_call_install_hint(distro_id):
    """Return distro-specific install hint for acpi_call module."""
    hints = {
        "ubuntu": "sudo apt install acpi-call-dkms",
        "debian": "sudo apt install acpi-call-dkms",
        "linuxmint": "sudo apt install acpi-call-dkms",
        "pop": "sudo apt install acpi-call-dkms",
        "fedora": "Build acpi_call from source with DKMS (not in Fedora repos)",
        "rhel": "Build acpi_call from source with DKMS",
        "centos": "Build acpi_call from source with DKMS",
        "arch": "sudo pacman -S acpi_call",
        "manjaro": "sudo pacman -S linux-latest-acpi_call",
        "endeavouros": "sudo pacman -S acpi_call",
        "opensuse-tumbleweed": "sudo zypper install acpi_call-kmp-default",
        "opensuse-leap": "sudo zypper install acpi_call-kmp-default",
        "gentoo": "sudo emerge sys-power/acpi_call",
        "nixos": "Add acpi_call to boot.extraModulePackages in configuration.nix",
        "void": "sudo xbps-install -S acpi_call-dkms",
    }
    return hints.get(distro_id, "Install the acpi_call kernel module for your distro")


def detect() -> SystemCapabilities:
    """Probe all hardware capabilities. Every step is independently try/excepted."""
    caps = SystemCapabilities()

    # ── Identity ──────────────────────────────────────────────────
    try:
        caps.product_name = _read_file("/sys/devices/virtual/dmi/id/product_name")
        caps.board_name = _read_file("/sys/devices/virtual/dmi/id/board_name")
        caps.is_omen = "OMEN" in caps.product_name.upper() or "VICTUS" in caps.product_name.upper()
    except Exception:
        caps.warnings.append("Could not read DMI identity")

    # ── Chassis type (laptop vs desktop) ──────────────────────────
    try:
        caps.chassis_type, caps.is_laptop = _detect_chassis()
    except Exception:
        pass

    # If board is in our laptop database, trust that over chassis_type
    if caps.board_name in _ALL_LAPTOP_BOARDS:
        caps.is_laptop = True
    elif caps.board_name in _OMEN_DESKTOP_BOARDS or caps.board_name in EC_MAPS:
        caps.is_laptop = False

    # ── Hostname / kernel / distro ────────────────────────────────
    try:
        caps.hostname = _read_file("/etc/hostname") or os.uname().nodename
    except Exception:
        pass
    try:
        caps.kernel = os.uname().release
    except Exception:
        pass
    try:
        caps.distro_id, caps.os_release_pretty = _detect_distro_id()
        caps.distro = caps.os_release_pretty
    except Exception:
        pass

    # ── Kernel lockdown ────────────────────────────────────────────
    try:
        caps.lockdown = _check_lockdown()
    except Exception:
        pass

    # ── CPU ────────────────────────────────────────────────────────
    try:
        caps.cpu_cores = os.cpu_count() or 0
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                caps.cpu_model = line.split(":", 1)[1].strip()
                break
    except Exception:
        caps.warnings.append("Could not read CPU info")

    # ── Memory ────────────────────────────────────────────────────
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                caps.total_ram_gb = round(kb / 1048576, 1)
                break
    except Exception:
        pass

    # ── WMI / acpi_call ───────────────────────────────────────────
    try:
        if not wmi_available():
            if os.geteuid() == 0:
                try_load_acpi_call()
        caps.wmi_available = wmi_available()
        if caps.wmi_available:
            caps.wmi_path = probe_wmi_path() or ""
            if caps.wmi_path:
                fc = wmi_fan_count(caps.wmi_path)
                caps.fan_count = fc if fc is not None else 0
            else:
                caps.warnings.append("acpi_call loaded but no working WMI path found")
        elif caps.is_omen and os.geteuid() == 0:
            hint = _acpi_call_install_hint(caps.distro_id)
            caps.warnings.append(f"acpi_call not available — {hint}")
    except Exception:
        caps.warnings.append("WMI probe failed")

    # ── EC ─────────────────────────────────────────────────────────
    try:
        if os.geteuid() == 0:
            if caps.is_laptop:
                # Laptops: prefer ec_sys (standard ACPI EC, 256 bytes)
                try:
                    ec = ECPortIO()
                    ec.open(writable=False)
                    caps.ec_method = "ec_sys"
                    ec.close()
                except Exception:
                    # Try loading ec_sys module
                    try:
                        subprocess.run(["modprobe", "ec_sys", "write_support=1"],
                                       capture_output=True, timeout=5)
                        ec = ECPortIO()
                        ec.open(writable=False)
                        caps.ec_method = "ec_sys"
                        ec.close()
                    except Exception:
                        pass

                if caps.ec_method == "none":
                    caps.warnings.append(
                        "No EC access — try: modprobe ec_sys write_support=1")
            else:
                # Desktops: prefer mmap (extended EC space, 1024 bytes)
                if caps.lockdown != "none":
                    caps.warnings.append(
                        f"Kernel lockdown ({caps.lockdown}) blocks /dev/mem — "
                        "disable Secure Boot or use ec_sys")

                # Try mmap first
                try:
                    ec = ECAccess(base=ECMS_BASE)
                    ec.open(writable=False)
                    caps.ec_method = "mmap"
                    caps.ec_base = ECMS_BASE
                    ec.close()
                except Exception:
                    pass

                # Fallback to ec_sys
                if caps.ec_method == "none":
                    try:
                        ec = ECPortIO()
                        ec.open(writable=False)
                        caps.ec_method = "ec_sys"
                        ec.close()
                    except Exception:
                        try:
                            subprocess.run(["modprobe", "ec_sys", "write_support=1"],
                                           capture_output=True, timeout=5)
                            ec = ECPortIO()
                            ec.open(writable=False)
                            caps.ec_method = "ec_sys"
                            ec.close()
                        except Exception:
                            pass

                if caps.ec_method == "none":
                    caps.warnings.append("No EC access (need /dev/mem or ec_sys)")
        else:
            caps.warnings.append("Not root \u2014 EC access unavailable")
    except Exception:
        caps.warnings.append("EC probe failed")

    # ── EC register map ───────────────────────────────────────────
    caps.ec_regmap = find_regmap(caps.board_name, is_laptop=caps.is_laptop)
    if caps.is_omen and not is_known_board(caps.board_name):
        if caps.ec_method != "none":
            caps.warnings.append(
                f"Unknown board '{caps.board_name}' \u2014 using generic "
                f"{'laptop' if caps.is_laptop else 'desktop'} register map"
            )

    # ── SELinux warning (Fedora/RHEL) ─────────────────────────────
    if caps.distro_id in ("fedora", "rhel", "centos") and caps.ec_method == "mmap":
        selinux_mode = _read_file("/sys/fs/selinux/enforce")
        if selinux_mode == "1":
            caps.warnings.append(
                "SELinux enforcing \u2014 /dev/mem access may be blocked")

    # ── NVIDIA GPU ────────────────────────────────────────────────
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name",
                            "--format=csv,noheader"], capture_output=True,
                           text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            caps.has_nvidia = True
            caps.nvidia_name = r.stdout.strip().split("\n")[0]
    except FileNotFoundError:
        pass
    except Exception:
        caps.warnings.append("nvidia-smi failed")

    # ── hwmon devices ─────────────────────────────────────────────
    try:
        for path in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            name = _read_file(os.path.join(path, "name"))
            if name:
                caps.hwmon_devices[name] = path
    except Exception:
        pass

    # ── hwmon fans ─────────────────────────────────────────────
    try:
        # Skip HP-specific hwmon drivers when EC/WMI is active to avoid
        # double-counting the same fans
        skip_names = set()
        if caps.ec_method != "none" or caps.wmi_available:
            skip_names.update({"hp_wmi", "hp-wmi", "hp_wmi_sensors"})

        caps.hwmon_fans = discover_hwmon_fans(skip_names=frozenset(skip_names))
        caps.has_hwmon_fans = len(caps.hwmon_fans) > 0

        # If no hwmon fans found and we're root, try loading common fan drivers
        if not caps.has_hwmon_fans and os.geteuid() == 0:
            for mod in ("nct6775", "it87", "dell-smm-hwmon", "thinkpad_acpi"):
                try:
                    subprocess.run(["modprobe", mod],
                                   capture_output=True, timeout=5)
                except Exception:
                    pass
            # Re-scan after loading modules
            caps.hwmon_fans = discover_hwmon_fans(
                skip_names=frozenset(skip_names))
            caps.has_hwmon_fans = len(caps.hwmon_fans) > 0
    except Exception:
        pass

    # ── Network interfaces ────────────────────────────────────────
    try:
        for iface in sorted(os.listdir("/sys/class/net")):
            # Skip loopback, docker, veth
            if iface in ("lo",) or iface.startswith(("veth", "br-", "docker")):
                continue
            caps.network_interfaces.append(iface)
    except Exception:
        pass

    # ── Block devices ─────────────────────────────────────────────
    try:
        r = subprocess.run(["lsblk", "-d", "-n", "-o", "NAME,SIZE,MODEL"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                parts = line.split(None, 2)
                if len(parts) >= 2:
                    name = parts[0]
                    if name.startswith("loop") or name.startswith("ram"):
                        continue
                    size = parts[1]
                    model = parts[2].strip() if len(parts) > 2 else ""
                    caps.block_devices.append((name, size, model))
    except FileNotFoundError:
        # lsblk not found — fall back to /sys/block
        try:
            for bdev in sorted(os.listdir("/sys/block")):
                if bdev.startswith(("loop", "ram", "dm-")):
                    continue
                size_sectors = _read_file(f"/sys/block/{bdev}/size")
                if size_sectors:
                    size_bytes = int(size_sectors) * 512
                    size_gb = size_bytes / (1024**3)
                    size_s = f"{size_gb:.0f}G"
                    model = _read_file(f"/sys/block/{bdev}/device/model")
                    caps.block_devices.append((bdev, size_s, model))
        except Exception:
            pass
    except Exception:
        pass

    # ── Battery ───────────────────────────────────────────────────
    try:
        caps.has_battery = bool(glob.glob("/sys/class/power_supply/BAT*"))
    except Exception:
        pass

    return caps
