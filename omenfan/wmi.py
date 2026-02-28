"""WMI BIOS interface via acpi_call for HP OMEN systems."""

import os
import struct
import subprocess

ACPI_CALL = "/proc/acpi/call"
WMI_SIGN = 0x55434553   # "SECU" signature
WMI_COMD = 0x20008       # Gaming command type

# WMI command IDs
CMD_FAN_COUNT = 0x10
CMD_FAN_SPEED_GET = 0x11
CMD_GET_TEMP = 0x14
CMD_SET_PERF_MODE = 0x1A
CMD_GET_FAN_MODE = 0x25
CMD_FAN_MAX_GET = 0x26
CMD_FAN_MAX_SET = 0x27
CMD_GET_SYSTEM_DESIGN = 0x28
CMD_SET_PERF_MODE_V2 = 0x29
CMD_FAN_SET = 0x2E
CMD_GET_FAN_TYPE = 0x2C


def wmi_available(path=ACPI_CALL):
    """Check if acpi_call interface exists."""
    return os.path.exists(path)


def try_load_acpi_call():
    """Attempt to load the acpi_call kernel module."""
    try:
        subprocess.run(["modprobe", "acpi_call"], capture_output=True, timeout=5)
        return wmi_available()
    except Exception:
        return False


def wmi_call(cmdt, data=b'\x00\x00\x00\x00', wmi_path=r"\_SB.WMID.WMAA"):
    """Execute a WMI BIOS call and return the raw response string.

    Returns None on failure.
    """
    try:
        buf = struct.pack('<IIII', WMI_SIGN, WMI_COMD, cmdt, len(data)) + data
        with open(ACPI_CALL, "w") as f:
            f.write(f"{wmi_path} 0 0x03 b{buf.hex()}")
        with open(ACPI_CALL, "r") as f:
            return f.read().replace('\x00', '').strip()
    except Exception:
        return None


def wmi_fan_set(rear1, rear2, front1=None, front2=None, front3=None,
                wmi_path=r"\_SB.WMID.WMAA"):
    """Set fan speeds via WMI 0x2E.

    2-byte payload: rear fans only.
    6-byte payload: all 5 fans (required to enable front fan channels).
    Values: 0x00=auto, 0xFF=max.
    """
    try:
        if front1 is not None:
            data = bytes([rear1, rear2, front1, front2 or front1,
                          front3 or front1, 0x00])
        else:
            data = bytes([rear1, rear2])
        buf = struct.pack('<IIII', WMI_SIGN, WMI_COMD, CMD_FAN_SET, len(data)) + data
        with open(ACPI_CALL, "w") as f:
            f.write(f"{wmi_path} 0 0x03 b{buf.hex()}")
    except Exception:
        pass


def wmi_keepalive(wmi_path=r"\_SB.WMID.WMAA"):
    """Send a keepalive (fan count query) to prevent WMI timeout."""
    try:
        wmi_call(CMD_FAN_COUNT, wmi_path=wmi_path)
    except Exception:
        pass


def wmi_fan_count(wmi_path=r"\_SB.WMID.WMAA"):
    """Query the number of fans via WMI 0x10."""
    result = wmi_call(CMD_FAN_COUNT, wmi_path=wmi_path)
    if result:
        try:
            val = int(result.split()[0], 0)
            return val & 0xFF
        except Exception:
            pass
    return None


def wmi_fan_max_set(enable, wmi_path=r"\_SB.WMID.WMAA"):
    """Toggle max fan mode via WMI 0x27."""
    data = struct.pack('<I', 1 if enable else 0)
    return wmi_call(CMD_FAN_MAX_SET, data, wmi_path=wmi_path)


def wmi_set_perf_mode(mode, wmi_path=r"\_SB.WMID.WMAA"):
    """Set performance mode via WMI 0x29.

    mode: 0=Quiet, 1=Balanced, 3=Performance
    """
    data = struct.pack('<I', mode)
    return wmi_call(CMD_SET_PERF_MODE_V2, data, wmi_path=wmi_path)


def wmi_get_system_design(wmi_path=r"\_SB.WMID.WMAA"):
    """Query system design data via WMI 0x28."""
    return wmi_call(CMD_GET_SYSTEM_DESIGN, wmi_path=wmi_path)


def wmi_get_fan_type(wmi_path=r"\_SB.WMID.WMAA"):
    """Query fan type via WMI 0x2C."""
    return wmi_call(CMD_GET_FAN_TYPE, wmi_path=wmi_path)


def probe_wmi_path():
    """Probe WMI paths and return the first working one, or None.

    Tries desktop path first, then laptop path.
    """
    for path in [r"\_SB.WMID.WMAA", r"\_SB.WMI.WMAA"]:
        result = wmi_call(CMD_FAN_COUNT, wmi_path=path)
        if result and not result.startswith("Error"):
            return path
    return None
