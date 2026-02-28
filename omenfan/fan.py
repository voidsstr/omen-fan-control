"""Fan control: curves, profiles, and the FanController."""

from . import wmi as wmi_mod
from .ec import ECRegisterMap, EC_MAPS

MAX_RPM = 3200
MAX_TEMP = 100

# Profile constants
PROFILE_NONE = 0
PROFILE_MAX = 1
PROFILE_AGGRESSIVE = 2

# Aggressive curve: (temp_threshold, duty%, wmi_byte)
AGGRESSIVE_CURVE = [
    (40,  30, 0x30), (50,  50, 0x50), (60,  70, 0x80),
    (70,  85, 0xB0), (80, 100, 0xE0), (90, 100, 0xFF),
]

# Front fan auto curve for desktop BIOS auto mode.
# The BIOS only manages rear fans — front fans need explicit EC duty control.
# Gentler than aggressive: quiet at idle, scales up with temperature.
FRONT_AUTO_CURVE = [
    (30,  20), (40,  30), (50,  45), (60,  60),
    (70,  75), (80,  90), (90, 100),
]


def _front_duty_for_temp(cpu_temp):
    """Look up front fan duty for a given CPU temperature."""
    for temp, duty in FRONT_AUTO_CURVE:
        if cpu_temp <= temp:
            return duty
    return FRONT_AUTO_CURVE[-1][1]


class FanCurve:
    """Represents one of the EC's stored fan curves (Balanced, Quiet, Performance).

    Only available on desktops — laptops do not have EC-stored fan curves.
    """

    def __init__(self, name, base, ec):
        self.name = name
        self.base = base
        raw = ec.read_bytes(base, 64)
        self.temps = list(raw[0:15])
        self.npoints = raw[15]
        self.rpms = []
        for i in range(0, 20, 2):
            self.rpms.append((raw[0x10 + i] << 8) | raw[0x10 + i + 1])
        self.min_temp = raw[0x3C] if len(raw) > 0x3C else 0
        self.max_temp = raw[0x3E] if len(raw) > 0x3E else 0

    def set_rpm(self, ec, point_idx, rpm):
        """Write a new RPM value to a specific curve point in the EC."""
        off = self.base + 0x10 + (point_idx * 2)
        ec.write16_be(off, rpm)
        self.rpms[point_idx] = rpm


def _raw_perf_to_standard(raw, thermal_version):
    """Translate raw EC perf_mode register value to standard WMI mode (0/1/3).

    Different OMEN generations encode the thermal profile differently in EC:
      Desktop:    0=Quiet, 1=Balanced, 3=Performance
      Laptop v0:  0=Default, 1=Performance, 2=Cool
      Laptop v1:  0x30=Default, 0x31=Performance, 0x50=Cool

    Returns standard mode value (0=Quiet, 1=Balanced, 3=Performance).
    """
    if thermal_version == "v0":
        return {0: 1, 1: 3, 2: 0}.get(raw, 1)
    # v1 — check if laptop encoding (0x30+) or desktop encoding (0-3)
    if raw >= 0x30:
        return {0x30: 1, 0x31: 3, 0x50: 0}.get(raw, 1)
    return {0: 0, 1: 1, 3: 3}.get(raw, 1)


class FanController:
    """Manages fan control state and applies profiles.

    Handles both desktop (5-fan mmap EC + WMI) and laptop (2-fan ec_sys + WMI)
    layouts transparently via the ECRegisterMap.

    Desktop: 2 rear fans controlled by BIOS via WMI (Quiet/Balanced/Performance),
             3 front fans controlled directly via EC PWM duty registers.
             The BIOS does NOT manage front fans — they need active control.
    Laptop:  2 fans (CPU + GPU) via manual mode register + fan_set_pct.
    """

    def __init__(self, ec, regmap, wmi_path=None):
        self.ec = ec
        self.regmap = regmap
        self.wmi_path = wmi_path
        self.profile = PROFILE_NONE
        self.fan_max = False
        self.perf_mode = 1  # default Balanced; updated by initialize()
        self.initial_fan_duty = []
        self._has_wmi = wmi_path is not None and wmi_mod.wmi_available()
        self._has_ec = ec.method != "none"
        self._is_laptop = regmap.chassis == "laptop"

    @property
    def can_control(self):
        """Whether any fan control is possible."""
        return self._has_wmi or self._has_ec

    @property
    def fan_count(self):
        """Total number of fans from register map."""
        return len(self.regmap.fan_labels)

    def initialize(self):
        """Called at startup: detect current state without changing fans."""
        if not self._has_ec:
            return
        # Seed perf_mode from EC (translate raw value to standard 0/1/3)
        if self.regmap.perf_mode >= 0:
            raw = self.ec.read(self.regmap.perf_mode)
            self.perf_mode = _raw_perf_to_standard(
                raw, self.regmap.thermal_version)
        # Save initial fan duty for cleanup
        if self._is_laptop:
            self.initial_fan_duty = [
                self.ec.read(r) for r in self.regmap.fan_get_pct
            ] if self.regmap.fan_get_pct else []
        else:
            self.initial_fan_duty = [
                self.ec.read(r) for r in self.regmap.fan_front_duty
            ]

    def apply_max(self):
        """Set all fans to maximum speed."""
        self.fan_max = True
        self.profile = PROFILE_MAX
        if self._has_wmi:
            wmi_mod.wmi_keepalive(self.wmi_path)
            if self._is_laptop:
                wmi_mod.wmi_fan_max_set(True, wmi_path=self.wmi_path)
            else:
                wmi_mod.wmi_fan_set(0xE0, 0xE0, 0xE0, 0xE0, 0xE0,
                                    wmi_path=self.wmi_path)
        if self._has_ec:
            if self._is_laptop:
                # Enable manual mode + max fan via EC registers
                if self.regmap.manual_mode >= 0:
                    self.ec.write(self.regmap.manual_mode,
                                  self.regmap.manual_mode_on)
                if self.regmap.max_fan_reg >= 0:
                    self.ec.write(self.regmap.max_fan_reg,
                                  self.regmap.max_fan_on)
                # Set fan speed to 100%
                for reg in self.regmap.fan_set_pct:
                    self.ec.write(reg, 100)
                # Reset auto countdown to prevent EC auto-revert
                if self.regmap.auto_countdown >= 0:
                    self.ec.write(self.regmap.auto_countdown, 0xFF)
            else:
                # Desktop: set front fan PWM duty to 100%
                for reg in self.regmap.fan_front_duty:
                    self.ec.write(reg, 100)

    def apply_aggressive(self, cpu_temp):
        """Apply aggressive temperature-based curve."""
        duty = AGGRESSIVE_CURVE[-1][1]
        wmi_byte = AGGRESSIVE_CURVE[-1][2]
        for temp, d, wb in AGGRESSIVE_CURVE:
            if cpu_temp <= temp:
                duty = d
                wmi_byte = wb
                break
        if self._has_wmi:
            wmi_mod.wmi_keepalive(self.wmi_path)
            if self._is_laptop:
                # Laptop: 2-byte payload for 2 fans
                wmi_mod.wmi_fan_set(wmi_byte, wmi_byte,
                                    wmi_path=self.wmi_path)
            else:
                # Desktop: 6-byte payload for all 5 fans
                wmi_mod.wmi_fan_set(wmi_byte, wmi_byte, wmi_byte, wmi_byte,
                                    wmi_byte, wmi_path=self.wmi_path)
        if self._has_ec:
            if self._is_laptop:
                # Enable manual mode, set fan speed percentages
                if self.regmap.manual_mode >= 0:
                    self.ec.write(self.regmap.manual_mode,
                                  self.regmap.manual_mode_on)
                if self.regmap.max_fan_reg >= 0:
                    self.ec.write(self.regmap.max_fan_reg,
                                  self.regmap.max_fan_off)
                for reg in self.regmap.fan_set_pct:
                    self.ec.write(reg, duty)
                # Reset auto countdown to prevent EC auto-revert
                if self.regmap.auto_countdown >= 0:
                    self.ec.write(self.regmap.auto_countdown, 0xFF)
            else:
                # Desktop: set front fan PWM duty
                for reg in self.regmap.fan_front_duty:
                    self.ec.write(reg, duty)

    def stop_override(self):
        """Return to BIOS auto mode (rear fans) + temp-based auto (front fans)."""
        self.fan_max = False
        self.profile = PROFILE_NONE
        if self._has_wmi:
            if self._is_laptop:
                wmi_mod.wmi_fan_max_set(False, wmi_path=self.wmi_path)
                wmi_mod.wmi_fan_set(0x00, 0x00, wmi_path=self.wmi_path)
            else:
                # 2-byte: rear fans to BIOS auto only.
                # Front fans are NOT managed by BIOS — we handle them via
                # EC duty in keepalive(). Don't send 6-byte with 0x00 for
                # front fans, as that stops them (BIOS has no front fan curve).
                wmi_mod.wmi_fan_set(0x00, 0x00, wmi_path=self.wmi_path)
        if self._has_ec:
            if self._is_laptop:
                # Disable manual mode, restore BIOS auto control
                if self.regmap.manual_mode >= 0:
                    self.ec.write(self.regmap.manual_mode,
                                  self.regmap.manual_mode_off)
                if self.regmap.max_fan_reg >= 0:
                    self.ec.write(self.regmap.max_fan_reg,
                                  self.regmap.max_fan_off)
            elif self.regmap.fan_front_duty:
                # Desktop: set front fans to reasonable starting duty
                # (keepalive will adjust based on temperature each cycle)
                for reg in self.regmap.fan_front_duty:
                    self.ec.write(reg, 50)

    def keepalive(self, cpu_temp):
        """Called every refresh cycle to maintain the active profile."""
        if self.fan_max:
            self.apply_max()
        elif self.profile == PROFILE_AGGRESSIVE:
            self.apply_aggressive(cpu_temp)
        else:
            # BIOS auto mode
            if self._has_wmi:
                wmi_mod.wmi_keepalive(self.wmi_path)
            # Desktop: front fans need active management via EC duty
            # (BIOS only controls rear fans through its thermal profiles)
            if self._has_ec and not self._is_laptop and self.regmap.fan_front_duty:
                duty = _front_duty_for_temp(cpu_temp)
                for reg in self.regmap.fan_front_duty:
                    self.ec.write(reg, duty)

    def set_perf_mode(self, mode):
        """Set BIOS performance mode (0=Quiet, 1=Balanced, 3=Performance)."""
        self.perf_mode = mode
        if self._has_wmi:
            wmi_mod.wmi_set_perf_mode(mode, wmi_path=self.wmi_path)

    def cleanup(self):
        """Restore fans to safe state on exit."""
        try:
            if self._has_ec:
                if self._is_laptop:
                    # Restore auto mode
                    if self.regmap.manual_mode >= 0:
                        self.ec.write(self.regmap.manual_mode,
                                      self.regmap.manual_mode_off)
                    if self.regmap.max_fan_reg >= 0:
                        self.ec.write(self.regmap.max_fan_reg,
                                      self.regmap.max_fan_off)
                elif self.initial_fan_duty:
                    for i, reg in enumerate(self.regmap.fan_front_duty):
                        if i < len(self.initial_fan_duty):
                            duty = max(50, self.initial_fan_duty[i])
                            self.ec.write(reg, duty)
        except Exception:
            pass
        self.fan_max = False
        self.profile = PROFILE_NONE

    def read_curves(self):
        """Read fan curves from EC (desktop only — laptops have no EC curves)."""
        if not self._has_ec or not self.regmap.curve_bases:
            return []
        return [FanCurve(self.regmap.curve_names[i], self.regmap.curve_bases[i],
                         self.ec)
                for i in range(len(self.regmap.curve_bases))]
