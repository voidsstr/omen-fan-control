"""EC (Embedded Controller) access via memory-mapped I/O, ec_sys, or dummy fallback."""

import mmap
import os
import time
from dataclasses import dataclass, field, replace


@dataclass
class ECRegisterMap:
    """Register offsets for a specific HP OMEN model.

    Supports both desktop (mmap, offsets 0x000-0x3FF) and laptop (ec_sys,
    offsets 0x00-0xFF) layouts. Fields set to -1 are not available on
    that model. Empty tuples mean no hardware at those positions.
    """
    name: str
    board_id: str
    chassis: str = "desktop"     # "desktop" or "laptop"

    # Temperature registers (single byte, degrees C)
    temp_cpu: int = 0x100
    temp_board: int = 0x102      # Board sensor (desktops) / GPU (laptops)
    temp_vrm: int = 0x104
    temp_dimm: tuple = (0x106, 0x108, 0x10A, 0x10C)

    # Fan RPM registers (16-bit big-endian: (hi, lo) pairs)
    fan_rear: tuple = ((0x120, 0x121), (0x122, 0x123))
    fan_front: tuple = ((0x124, 0x125), (0x126, 0x127), (0x128, 0x129))

    # Fan labels (matches fan_rear + fan_front ordering)
    fan_labels: tuple = ("Rear 1", "Rear 2", "Front 1", "Front 2", "Front 3")

    # Fan duty registers (desktop: direct PWM 0-100)
    fan_front_duty: tuple = (0x13D, 0x13E, 0x13F)

    # Laptop fan speed set/get registers (write/read percentage 0-100)
    fan_set_pct: tuple = ()
    fan_get_pct: tuple = ()

    # Control registers
    perf_mode: int = 0x133
    auto_fan: int = 0x138
    heartbeat: int = 0x0FF

    # Laptop manual fan control
    manual_mode: int = -1        # Laptop: register for manual/auto toggle
    manual_mode_on: int = 0x06   # Value to enable manual mode
    manual_mode_off: int = 0x00  # Value for auto mode
    auto_countdown: int = -1     # Laptop: countdown timer register
    max_fan_reg: int = -1        # Laptop: max fan speed toggle
    max_fan_on: int = 0x0C       # Value to enable max fan
    max_fan_off: int = 0x00      # Value to disable max fan

    # Thermal profile version affects perf_mode value interpretation
    thermal_version: str = "v1"  # "v0" or "v1"

    # Fan curve bases (desktop-only, stored in EC memory)
    curve_bases: tuple = (0x200, 0x240, 0x2C0)
    curve_names: tuple = ("Balanced", "Quiet", "Performance")


# ── Laptop register map template ─────────────────────────────────────
# Shared by all OMEN laptops (OmenMon register layout from ACPI DSDT).
# Accessed via ec_sys at standard ACPI EC port offsets (0x00-0xFF).

_LAPTOP_BASE = dict(
    chassis="laptop",
    temp_cpu=0x57,
    temp_board=0xB7,     # GPU temp on laptops
    temp_vrm=-1,
    temp_dimm=(),
    fan_rear=((0xB0, 0xB1), (0xB2, 0xB3)),
    fan_front=(),
    fan_labels=("CPU Fan", "GPU Fan"),
    fan_front_duty=(),
    fan_set_pct=(0x2C, 0x2D),
    fan_get_pct=(0x2E, 0x2F),
    perf_mode=0x95,
    auto_fan=-1,
    heartbeat=-1,
    manual_mode=0x62,
    auto_countdown=0x63,
    max_fan_reg=0xEC,
    curve_bases=(),
    curve_names=(),
)

_LAPTOP_V0 = {**_LAPTOP_BASE, "thermal_version": "v0"}
_LAPTOP_V1 = {**_LAPTOP_BASE, "thermal_version": "v1"}


# ── Known board families ─────────────────────────────────────────────

# OMEN laptop v0 boards (forced legacy thermal profile values)
_OMEN_LAPTOP_V0_BOARDS = frozenset({
    "8607",
    "8746", "8747", "8748", "8749", "874A",
})

# OMEN laptop v1 boards (standard thermal profile values)
_OMEN_LAPTOP_V1_BOARDS = frozenset({
    # OMEN 15/17 2019
    "84DA", "84DB", "84DC",
    # OMEN 15 2020
    "8572", "8573", "8574", "8575",
    # OMEN 16/17 2020-2021
    "8600", "8601", "8602", "8603", "8604", "8605", "8606", "860A",
    # OMEN 16/17 2021-2022
    "8786", "8787", "8788", "878A", "878B", "878C", "87B5",
    # OMEN 16 2022
    "886B", "886C",
    # OMEN 16/17 2022-2023
    "88C8", "88CB", "88D1", "88D2",
    "88F4", "88F5", "88F6", "88F7", "88FD", "88FE", "88FF",
    "8900", "8901", "8902", "8912", "8917", "8918", "8949", "894A",
    # OMEN 16 2023+
    "8A13", "8A14", "8A15", "8A42",
    "8BAB", "8BAD",
})

# Boards with EC countdown timer that auto-reverts to balanced
_OMEN_TIMED_BOARDS = frozenset({"8A15", "8A42", "8BAD"})

# HP Victus laptops (standard WMI thermal profile)
_VICTUS_BOARDS = frozenset({"8A25"})

# HP Victus S laptops (newer WMI commands)
_VICTUS_S_BOARDS = frozenset({
    "8BBE", "8BD4", "8BD5", "8C78", "8C99", "8C9C", "8D41",
})

# Known OMEN desktop boards (use memory-mapped EC)
_OMEN_DESKTOP_BOARDS = frozenset({
    "89EB",   # OMEN Desktop (generic)
    "8703",   # OMEN 30L Desktop GT13
    "8437",   # OMEN Desktop 880
})

# All known laptop boards (union)
_ALL_LAPTOP_BOARDS = (_OMEN_LAPTOP_V0_BOARDS | _OMEN_LAPTOP_V1_BOARDS
                      | _VICTUS_BOARDS | _VICTUS_S_BOARDS)


# ── Specific model register maps ─────────────────────────────────────

EC_MAPS = {
    # ── OMEN Desktops ─────────────────────────────────────────────
    "8D2C": ECRegisterMap(
        name="OMEN 45L GT22",
        board_id="8D2C",
    ),
}


def find_regmap(board_id, is_laptop=False):
    """Find the best register map for a board ID.

    Args:
        board_id: DMI board_name string (e.g. "8D2C")
        is_laptop: True if chassis type indicates portable

    Returns:
        ECRegisterMap instance (may be a generic/guessed map)
    """
    # Exact match first
    if board_id in EC_MAPS:
        return EC_MAPS[board_id]

    # Laptop v0 family
    if board_id in _OMEN_LAPTOP_V0_BOARDS:
        return ECRegisterMap(
            name=f"OMEN Laptop ({board_id})",
            board_id=board_id,
            **_LAPTOP_V0,
        )

    # Laptop v1 family
    if board_id in _OMEN_LAPTOP_V1_BOARDS:
        return ECRegisterMap(
            name=f"OMEN Laptop ({board_id})",
            board_id=board_id,
            **_LAPTOP_V1,
        )

    # Victus
    if board_id in _VICTUS_BOARDS:
        return ECRegisterMap(
            name=f"HP Victus ({board_id})",
            board_id=board_id,
            **_LAPTOP_V1,
        )

    # Victus S
    if board_id in _VICTUS_S_BOARDS:
        return ECRegisterMap(
            name=f"HP Victus S ({board_id})",
            board_id=board_id,
            **_LAPTOP_V1,
        )

    # Known desktop boards (use default desktop register map)
    if board_id in _OMEN_DESKTOP_BOARDS:
        return ECRegisterMap(
            name=f"OMEN Desktop ({board_id})",
            board_id=board_id,
        )

    # Unknown board — guess from chassis type
    if is_laptop:
        return ECRegisterMap(
            name=f"Unknown OMEN Laptop ({board_id})",
            board_id=board_id,
            **_LAPTOP_V1,
        )

    # Default: unknown desktop or non-OMEN
    return ECRegisterMap(name="Unknown", board_id=board_id)


def is_known_board(board_id):
    """Check if a board ID is in our database."""
    return (board_id in EC_MAPS
            or board_id in _ALL_LAPTOP_BOARDS
            or board_id in _OMEN_DESKTOP_BOARDS)


def is_timed_board(board_id):
    """Check if a board has the EC auto-revert countdown timer."""
    return board_id in _OMEN_TIMED_BOARDS


# Default EC memory-mapped base address (HP OMEN desktops)
ECMS_BASE = 0xFD500000
ECMS_SIZE = 0x400


class ECAccess:
    """EC access via /dev/mem memory-mapped I/O."""

    def __init__(self, base=ECMS_BASE, size=ECMS_SIZE):
        self._base = base
        self._size = size
        self._fd = None
        self._mm = None
        self._writable = False

    @property
    def method(self):
        return "mmap"

    def open(self, writable=False):
        flags = os.O_RDWR if writable else os.O_RDONLY
        prot = mmap.PROT_READ | (mmap.PROT_WRITE if writable else 0)
        self._fd = os.open("/dev/mem", flags)
        self._mm = mmap.mmap(self._fd, self._size, mmap.MAP_SHARED, prot,
                             offset=self._base)
        self._writable = writable

    def close(self):
        if self._mm:
            self._mm.close()
            self._mm = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def read(self, offset):
        return self._mm[offset]

    def read16_be(self, offset):
        return (self._mm[offset] << 8) | self._mm[offset + 1]

    def write(self, offset, value):
        if not self._writable:
            raise PermissionError("EC opened read-only")
        self._mm[offset] = value & 0xFF

    def write16_be(self, offset, value):
        if not self._writable:
            raise PermissionError("EC opened read-only")
        self._mm[offset] = (value >> 8) & 0xFF
        self._mm[offset + 1] = value & 0xFF

    def read_bytes(self, offset, length):
        return bytes(self._mm[offset:offset + length])

    def validate_heartbeat(self, timeout=0.3):
        """Check that the EC heartbeat register is incrementing."""
        hb1 = self.read(0x0FF)
        time.sleep(timeout)
        hb2 = self.read(0x0FF)
        return hb1 != hb2


class ECPortIO:
    """EC access via /sys/kernel/debug/ec/ec0/io (ec_sys module)."""

    def __init__(self):
        self._path = "/sys/kernel/debug/ec/ec0/io"

    @property
    def method(self):
        return "ec_sys"

    def open(self, writable=False):
        self._writable = writable
        if not os.path.exists(self._path):
            raise FileNotFoundError(f"{self._path} not found (load ec_sys module)")

    def close(self):
        pass

    def read(self, offset):
        with open(self._path, "rb") as f:
            f.seek(offset)
            return f.read(1)[0]

    def read16_be(self, offset):
        with open(self._path, "rb") as f:
            f.seek(offset)
            data = f.read(2)
            return (data[0] << 8) | data[1]

    def write(self, offset, value):
        if not self._writable:
            raise PermissionError("EC opened read-only")
        with open(self._path, "r+b") as f:
            f.seek(offset)
            f.write(bytes([value & 0xFF]))

    def write16_be(self, offset, value):
        if not self._writable:
            raise PermissionError("EC opened read-only")
        with open(self._path, "r+b") as f:
            f.seek(offset)
            f.write(bytes([(value >> 8) & 0xFF, value & 0xFF]))

    def read_bytes(self, offset, length):
        with open(self._path, "rb") as f:
            f.seek(offset)
            return f.read(length)

    def validate_heartbeat(self, timeout=0.3):
        hb1 = self.read(0x0FF)
        time.sleep(timeout)
        hb2 = self.read(0x0FF)
        return hb1 != hb2


class ECDummy:
    """Dummy EC that returns zeros. Used when no EC access is available."""

    @property
    def method(self):
        return "none"

    def open(self, writable=False):
        pass

    def close(self):
        pass

    def read(self, offset):
        return 0

    def read16_be(self, offset):
        return 0

    def write(self, offset, value):
        pass

    def write16_be(self, offset, value):
        pass

    def read_bytes(self, offset, length):
        return bytes(length)

    def validate_heartbeat(self, timeout=0.3):
        return False


def open_ec(ec_method="mmap", base=ECMS_BASE, writable=False):
    """Factory: open the best available EC access method.

    Args:
        ec_method: "mmap", "ec_sys", or "none"
        base: Memory-mapped base address (for mmap method)
        writable: Whether to open for writing

    Returns:
        An EC access object (ECAccess, ECPortIO, or ECDummy)
    """
    if ec_method == "mmap":
        ec = ECAccess(base=base)
    elif ec_method == "ec_sys":
        ec = ECPortIO()
    else:
        ec = ECDummy()
    ec.open(writable=writable)
    return ec
