"""System metrics collection: CPU, memory, disk, network, GPU, thermal."""

import glob
import os
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field

from .detect import SystemCapabilities, _read_file
from .hwmon import read_hwmon_fan, HwmonFanState


@dataclass
class CPUMetrics:
    usage_pct: float = 0.0           # Overall CPU usage %
    per_core_pct: list = field(default_factory=list)  # Per-core usage %
    freq_mhz: float = 0.0           # Average frequency MHz
    freq_min_mhz: float = 0.0       # Min core frequency
    freq_max_mhz: float = 0.0       # Max core frequency
    per_core_freq: list = field(default_factory=list)  # Per-core freq MHz
    load_1: float = 0.0
    load_5: float = 0.0
    load_15: float = 0.0
    proc_running: int = 0            # Running processes
    proc_total: int = 0              # Total processes
    governor: str = ""               # CPU frequency governor


@dataclass
class MemoryMetrics:
    total_kb: int = 0
    used_kb: int = 0
    available_kb: int = 0
    cached_kb: int = 0
    buffers_kb: int = 0
    dirty_kb: int = 0
    shared_kb: int = 0
    sreclaimable_kb: int = 0
    swap_total_kb: int = 0
    swap_used_kb: int = 0
    swap_cached_kb: int = 0

    @property
    def used_pct(self):
        return (self.used_kb / self.total_kb * 100) if self.total_kb else 0

    @property
    def swap_used_pct(self):
        return (self.swap_used_kb / self.swap_total_kb * 100) if self.swap_total_kb else 0


@dataclass
class GPUMetrics:
    name: str = ""
    temp_c: int = 0
    fan_pct: int = 0
    util_pct: int = 0
    power_w: float = 0.0
    vram_used_mb: int = 0
    vram_total_mb: int = 0
    clock_core_mhz: int = 0
    clock_mem_mhz: int = 0
    pstate: str = ""
    driver_version: str = ""

    @property
    def vram_used_pct(self):
        return (self.vram_used_mb / self.vram_total_mb * 100) if self.vram_total_mb else 0


@dataclass
class DiskMetrics:
    mount: str = ""
    device: str = ""
    total_gb: float = 0.0
    used_gb: float = 0.0
    model: str = ""
    temp_c: int = 0

    @property
    def used_pct(self):
        return (self.used_gb / self.total_gb * 100) if self.total_gb else 0


@dataclass
class DiskIOMetrics:
    read_bytes_sec: float = 0.0
    write_bytes_sec: float = 0.0


@dataclass
class NetInterface:
    name: str = ""
    rx_bytes_sec: float = 0.0
    tx_bytes_sec: float = 0.0


@dataclass
class ThermalZone:
    name: str = ""
    temp_c: float = 0.0
    source: str = ""   # "hwmon" or "thermal_zone"


@dataclass
class BatteryMetrics:
    capacity_pct: int = 0
    status: str = ""      # "Charging", "Discharging", "Full"
    power_w: float = 0.0


@dataclass
class HwmonFanMetrics:
    """Fan data from hwmon sysfs interface."""
    label: str = ""
    rpm: int = 0
    duty_pct: int = -1     # -1 if no PWM control
    can_control: bool = False


@dataclass
class ECMetrics:
    """Temperatures and fan data from EC registers."""
    cpu_temp: int = 0
    board_temp: int = 0
    vrm_temp: int = 0
    dimm_temps: list = field(default_factory=list)
    rear_rpms: list = field(default_factory=list)
    front_rpms: list = field(default_factory=list)
    front_duty: list = field(default_factory=list)
    perf_mode: int = 0
    auto_fan: int = 0
    heartbeat: int = 0
    thermal_version: str = "v1"

    @property
    def perf_mode_name(self):
        if self.thermal_version == "v0":
            # OMEN laptop v0: 0=Default, 1=Performance, 2=Cool
            return {0: "Default", 1: "Performance", 2: "Cool"}.get(
                self.perf_mode, f"Mode {self.perf_mode}")
        if self.perf_mode >= 0x30:
            # OMEN laptop v1: 0x30=Balanced, 0x31=Performance, 0x50=Quiet
            return {0x30: "Balanced", 0x31: "Performance", 0x50: "Quiet"}.get(
                self.perf_mode, f"Mode {self.perf_mode}")
        # Desktop / standard: 0=Quiet, 1=Balanced, 3=Performance
        return {0: "Quiet", 1: "Balanced", 3: "Performance"}.get(
            self.perf_mode, f"Mode {self.perf_mode}")

    @property
    def fan_mode_name(self):
        return "Auto" if self.auto_fan & 0x80 else "Manual"


@dataclass
class SystemInfo:
    hostname: str = ""
    kernel: str = ""
    distro: str = ""
    uptime_sec: float = 0.0
    cpu_model: str = ""
    cpu_cores: int = 0
    ram_gb: float = 0.0


class SensorCollector:
    """Collects all system metrics. Each sensor independently try/excepted."""

    def __init__(self, caps: SystemCapabilities):
        self.caps = caps

        # Delta state for CPU usage
        self._prev_stat = None
        self._prev_stat_time = 0

        # Delta state for network
        self._prev_net = {}    # {iface: (rx_bytes, tx_bytes)}
        self._prev_net_time = 0

        # Delta state for disk I/O
        self._prev_diskio = {}  # {dev: (read_sectors, write_sectors)}
        self._prev_diskio_time = 0

        # Failure counters for disabling broken sensors
        self._fail_count = {}
        self._disabled = set()

        # History deques
        self.hist_cpu_temp = deque(maxlen=60)
        self.hist_cpu_usage = deque(maxlen=60)
        self.hist_gpu_temp = deque(maxlen=60)
        self.hist_gpu_usage = deque(maxlen=60)
        self.hist_mem_pct = deque(maxlen=60)
        self.hist_disk_read = deque(maxlen=60)
        self.hist_disk_write = deque(maxlen=60)
        self.hist_net_rx = deque(maxlen=60)
        self.hist_net_tx = deque(maxlen=60)

        # Latest data
        self.cpu = None
        self.memory = None
        self.gpu = None
        self.disks = []
        self.disk_io = None
        self.net_interfaces = []
        self.thermals = []
        self.battery = None
        self.ec = None
        self.hwmon_fans = []      # list[HwmonFanMetrics]
        self.sysinfo = None

    def _try(self, name, fn):
        """Run a sensor function with failure tracking."""
        if name in self._disabled:
            return None
        try:
            result = fn()
            self._fail_count[name] = 0
            return result
        except Exception:
            self._fail_count[name] = self._fail_count.get(name, 0) + 1
            if self._fail_count[name] >= 3:
                self._disabled.add(name)
            return None

    def refresh(self, ec=None):
        """Refresh all sensors. Call once per second."""
        self.cpu = self._try("cpu", self._read_cpu)
        self.memory = self._try("memory", self._read_memory)
        self.gpu = self._try("gpu", self._read_gpu) if self.caps.has_nvidia else None
        self.disks = self._try("disks", self._read_disks) or []
        self.disk_io = self._try("disk_io", self._read_disk_io)
        self.net_interfaces = self._try("net", self._read_net) or []
        self.thermals = self._try("thermal", self._read_thermals) or []
        self.battery = self._try("battery", self._read_battery) if self.caps.has_battery else None
        self.sysinfo = self._try("sysinfo", self._read_sysinfo)

        if ec and ec.method != "none":
            self.ec = self._try("ec", lambda: self._read_ec(ec))
        elif self.ec is None:
            self.ec = ECMetrics()

        # hwmon fans
        if self.caps.has_hwmon_fans:
            self.hwmon_fans = self._try("hwmon_fans",
                                        self._read_hwmon_fans) or []

        # CPU temp fallback from hwmon coretemp when no EC
        if self.ec and self.ec.cpu_temp == 0:
            self._try("cpu_temp_fallback", self._cpu_temp_fallback)

        # Update histories
        if self.ec and self.ec.cpu_temp > 0:
            self.hist_cpu_temp.append(self.ec.cpu_temp)
        if self.cpu:
            self.hist_cpu_usage.append(self.cpu.usage_pct)
        if self.gpu:
            if self.gpu.temp_c > 0:
                self.hist_gpu_temp.append(self.gpu.temp_c)
            self.hist_gpu_usage.append(self.gpu.util_pct)
        if self.memory:
            self.hist_mem_pct.append(self.memory.used_pct)
        if self.disk_io:
            self.hist_disk_read.append(self.disk_io.read_bytes_sec)
            self.hist_disk_write.append(self.disk_io.write_bytes_sec)
        if self.net_interfaces:
            total_rx = sum(ni.rx_bytes_sec for ni in self.net_interfaces)
            total_tx = sum(ni.tx_bytes_sec for ni in self.net_interfaces)
            self.hist_net_rx.append(total_rx)
            self.hist_net_tx.append(total_tx)

    def _read_cpu(self):
        """Read CPU usage from /proc/stat and frequency from cpufreq."""
        m = CPUMetrics()

        # Usage from /proc/stat
        with open("/proc/stat") as f:
            lines = f.readlines()

        now = time.monotonic()
        current = {}
        for line in lines:
            if line.startswith("cpu"):
                parts = line.split()
                name = parts[0]
                vals = [int(x) for x in parts[1:8]]
                # user, nice, system, idle, iowait, irq, softirq
                total = sum(vals)
                idle = vals[3] + vals[4]
                current[name] = (total, idle)

        if self._prev_stat:
            # Overall
            pt, pi = self._prev_stat.get("cpu", (0, 0))
            ct, ci = current.get("cpu", (0, 0))
            dt = ct - pt
            di = ci - pi
            m.usage_pct = ((dt - di) / dt * 100) if dt > 0 else 0

            # Per-core
            for i in range(self.caps.cpu_cores):
                key = f"cpu{i}"
                pt, pi = self._prev_stat.get(key, (0, 0))
                ct, ci = current.get(key, (0, 0))
                dt = ct - pt
                di = ci - pi
                pct = ((dt - di) / dt * 100) if dt > 0 else 0
                m.per_core_pct.append(max(0, min(100, pct)))

        self._prev_stat = current
        self._prev_stat_time = now

        # Frequency
        freqs = []
        for i in range(self.caps.cpu_cores):
            path = f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq"
            val = _read_file(path)
            if val:
                try:
                    freqs.append(int(val) / 1000)  # kHz → MHz
                except ValueError:
                    pass
        if freqs:
            m.freq_mhz = sum(freqs) / len(freqs)
            m.per_core_freq = freqs

        # Frequency range
        if freqs:
            m.freq_min_mhz = min(freqs)
            m.freq_max_mhz = max(freqs)

        # Load average + process counts
        try:
            load = _read_file("/proc/loadavg")
            parts = load.split()
            m.load_1 = float(parts[0])
            m.load_5 = float(parts[1])
            m.load_15 = float(parts[2])
            if len(parts) > 3 and "/" in parts[3]:
                r, t = parts[3].split("/")
                m.proc_running = int(r)
                m.proc_total = int(t)
        except Exception:
            pass

        # CPU governor
        try:
            gov = _read_file("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
            if gov:
                m.governor = gov
        except Exception:
            pass

        return m

    def _read_memory(self):
        """Read memory info from /proc/meminfo."""
        m = MemoryMetrics()
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    info[key] = int(parts[1])

        m.total_kb = info.get("MemTotal", 0)
        m.available_kb = info.get("MemAvailable", 0)
        m.cached_kb = info.get("Cached", 0)
        m.buffers_kb = info.get("Buffers", 0)
        m.dirty_kb = info.get("Dirty", 0)
        m.shared_kb = info.get("Shmem", 0)
        m.sreclaimable_kb = info.get("SReclaimable", 0)
        m.used_kb = m.total_kb - m.available_kb
        m.swap_total_kb = info.get("SwapTotal", 0)
        swap_free = info.get("SwapFree", 0)
        m.swap_used_kb = m.swap_total_kb - swap_free
        m.swap_cached_kb = info.get("SwapCached", 0)
        return m

    def _read_gpu(self):
        """Read NVIDIA GPU metrics via nvidia-smi."""
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,temperature.gpu,fan.speed,utilization.gpu,"
             "power.draw,memory.used,memory.total,clocks.current.graphics,"
             "clocks.current.memory,pstate,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode != 0:
            return None
        parts = [p.strip() for p in r.stdout.strip().split(",")]
        if len(parts) < 11:
            return None
        g = GPUMetrics()
        g.name = parts[0]
        g.temp_c = int(parts[1]) if parts[1].isdigit() else 0
        g.fan_pct = int(parts[2]) if parts[2].isdigit() else 0
        g.util_pct = int(parts[3]) if parts[3].isdigit() else 0
        try:
            g.power_w = float(parts[4])
        except ValueError:
            pass
        g.vram_used_mb = int(parts[5]) if parts[5].isdigit() else 0
        g.vram_total_mb = int(parts[6]) if parts[6].isdigit() else 0
        g.clock_core_mhz = int(parts[7]) if parts[7].isdigit() else 0
        g.clock_mem_mhz = int(parts[8]) if parts[8].isdigit() else 0
        g.pstate = parts[9]
        g.driver_version = parts[10]
        return g

    def _read_disks(self):
        """Read disk usage via os.statvfs for mounted partitions."""
        disks = []
        seen_devs = set()
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                dev, mount, fstype = parts[0], parts[1], parts[2]
                if fstype in ("tmpfs", "devtmpfs", "sysfs", "proc", "cgroup",
                              "cgroup2", "securityfs", "debugfs", "tracefs",
                              "configfs", "fusectl", "hugetlbfs", "mqueue",
                              "binfmt_misc", "autofs", "pstore", "efivarfs",
                              "bpf", "nsfs", "overlay", "squashfs", "fuse.portal"):
                    continue
                if dev in seen_devs:
                    continue
                if not dev.startswith("/dev/"):
                    continue
                seen_devs.add(dev)
                try:
                    st = os.statvfs(mount)
                    total = st.f_blocks * st.f_frsize
                    free = st.f_bfree * st.f_frsize
                    if total == 0:
                        continue
                    d = DiskMetrics()
                    d.mount = mount
                    d.device = dev
                    d.total_gb = total / (1024 ** 3)
                    d.used_gb = (total - free) / (1024 ** 3)
                    if d.total_gb < 0.5:
                        continue
                    # Find model from caps
                    dev_name = os.path.basename(dev).rstrip("0123456789p")
                    for bname, bsize, bmodel in self.caps.block_devices:
                        if bname == dev_name:
                            d.model = bmodel
                            break
                    # NVMe temperature from hwmon
                    if dev_name.startswith("nvme"):
                        try:
                            hwmon_base = f"/sys/class/block/{dev_name}/device/hwmon"
                            if os.path.exists(hwmon_base):
                                hwmon_dirs = os.listdir(hwmon_base)
                                if hwmon_dirs:
                                    temp_file = os.path.join(
                                        hwmon_base, hwmon_dirs[0], "temp1_input")
                                    temp_val = _read_file(temp_file)
                                    if temp_val:
                                        d.temp_c = int(temp_val) // 1000
                        except Exception:
                            pass
                    disks.append(d)
                except OSError:
                    continue
        return disks

    def _read_disk_io(self):
        """Read disk I/O from /proc/diskstats."""
        now = time.monotonic()
        current = {}
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 14:
                    continue
                name = parts[2]
                # Skip partitions (keep whole devices only)
                if any(c.isdigit() for c in name) and not name.startswith("nvme"):
                    continue
                if name.startswith("loop") or name.startswith("ram"):
                    continue
                read_sectors = int(parts[5])
                write_sectors = int(parts[9])
                current[name] = (read_sectors, write_sectors)

        m = DiskIOMetrics()
        if self._prev_diskio:
            dt = now - self._prev_diskio_time
            if dt > 0:
                total_read = 0
                total_write = 0
                for dev, (rs, ws) in current.items():
                    prs, pws = self._prev_diskio.get(dev, (rs, ws))
                    total_read += (rs - prs) * 512   # sectors → bytes
                    total_write += (ws - pws) * 512
                m.read_bytes_sec = total_read / dt
                m.write_bytes_sec = total_write / dt

        self._prev_diskio = current
        self._prev_diskio_time = now
        return m

    def _read_net(self):
        """Read network traffic from sysfs."""
        now = time.monotonic()
        current = {}
        interfaces = []

        for iface in self.caps.network_interfaces:
            rx_path = f"/sys/class/net/{iface}/statistics/rx_bytes"
            tx_path = f"/sys/class/net/{iface}/statistics/tx_bytes"
            rx = _read_file(rx_path)
            tx = _read_file(tx_path)
            if rx and tx:
                rx_bytes = int(rx)
                tx_bytes = int(tx)
                current[iface] = (rx_bytes, tx_bytes)

                ni = NetInterface(name=iface)
                if iface in self._prev_net:
                    dt = now - self._prev_net_time
                    if dt > 0:
                        prx, ptx = self._prev_net[iface]
                        ni.rx_bytes_sec = max(0, (rx_bytes - prx) / dt)
                        ni.tx_bytes_sec = max(0, (tx_bytes - ptx) / dt)
                interfaces.append(ni)

        self._prev_net = current
        self._prev_net_time = now
        return interfaces

    def _read_thermals(self):
        """Read thermal zones from hwmon and thermal_zone sysfs."""
        zones = []

        # hwmon temperatures
        seen_names = {}
        for name, path in self.caps.hwmon_devices.items():
            for temp_file in sorted(glob.glob(os.path.join(path, "temp*_input"))):
                val = _read_file(temp_file)
                if val:
                    try:
                        temp_c = int(val) / 1000.0
                        # Get label if available
                        label_file = temp_file.replace("_input", "_label")
                        label = _read_file(label_file) or name
                        # Deduplicate names (e.g., multiple spd5118 hwmon devices)
                        display_name = f"{name}/{label}"
                        seen_names[display_name] = seen_names.get(display_name, 0) + 1
                        if seen_names[display_name] > 1:
                            display_name = f"{name}{seen_names[display_name]}/{label}"
                        zones.append(ThermalZone(
                            name=display_name,
                            temp_c=temp_c,
                            source="hwmon"
                        ))
                    except ValueError:
                        pass

        # Thermal zones
        for tz_path in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
            tz_type = _read_file(os.path.join(tz_path, "type"))
            tz_temp = _read_file(os.path.join(tz_path, "temp"))
            if tz_temp:
                try:
                    temp_c = int(tz_temp) / 1000.0
                    zones.append(ThermalZone(
                        name=tz_type or os.path.basename(tz_path),
                        temp_c=temp_c,
                        source="thermal_zone"
                    ))
                except ValueError:
                    pass

        return zones

    def _read_battery(self):
        """Read battery info from /sys/class/power_supply/BAT*."""
        bat_paths = glob.glob("/sys/class/power_supply/BAT*")
        if not bat_paths:
            return None
        path = bat_paths[0]
        b = BatteryMetrics()
        cap = _read_file(os.path.join(path, "capacity"))
        if cap:
            b.capacity_pct = int(cap)
        b.status = _read_file(os.path.join(path, "status"))
        power = _read_file(os.path.join(path, "power_now"))
        if power:
            b.power_w = int(power) / 1000000
        return b

    def _read_ec(self, ec):
        """Read EC registers for temperatures and fan RPMs.

        Handles both desktop and laptop register layouts by checking for
        -1 offsets (unavailable) and empty tuples (no hardware).
        """
        regmap = self.caps.ec_regmap
        m = ECMetrics()
        m.thermal_version = regmap.thermal_version
        if regmap.temp_cpu >= 0:
            m.cpu_temp = ec.read(regmap.temp_cpu)
        if regmap.temp_board >= 0:
            m.board_temp = ec.read(regmap.temp_board)
        if regmap.temp_vrm >= 0:
            m.vrm_temp = ec.read(regmap.temp_vrm)
        m.dimm_temps = [ec.read(r) for r in regmap.temp_dimm]
        m.rear_rpms = [ec.read16_be(hi) for hi, lo in regmap.fan_rear]
        m.front_rpms = [ec.read16_be(hi) for hi, lo in regmap.fan_front]
        m.front_duty = [ec.read(r) for r in regmap.fan_front_duty]
        # Laptop: read fan speed percentages if available
        if regmap.fan_get_pct:
            m.front_duty = [ec.read(r) for r in regmap.fan_get_pct]
        if regmap.perf_mode >= 0:
            m.perf_mode = ec.read(regmap.perf_mode)
        if regmap.auto_fan >= 0:
            m.auto_fan = ec.read(regmap.auto_fan)
        if regmap.heartbeat >= 0:
            m.heartbeat = ec.read(regmap.heartbeat)
        return m

    def _read_hwmon_fans(self):
        """Read all hwmon fans discovered during detect."""
        result = []
        for fan in self.caps.hwmon_fans:
            state = read_hwmon_fan(fan)
            m = HwmonFanMetrics(
                label=fan.label,
                rpm=state.rpm,
                duty_pct=state.duty_pct if fan.pwm_path else -1,
                can_control=fan.can_control,
            )
            result.append(m)
        return result

    def _cpu_temp_fallback(self):
        """Populate ec.cpu_temp from hwmon coretemp/k10temp when no EC."""
        if not self.ec:
            return
        # Try common CPU temp hwmon drivers
        for name in ("coretemp", "k10temp", "zenpower", "it87", "nct6775"):
            path = self.caps.hwmon_devices.get(name)
            if not path:
                continue
            # Look for Package/Tdie/temp1 (highest-level CPU temp)
            for suffix in ("temp1_input",):
                val = _read_file(os.path.join(path, suffix))
                if val:
                    try:
                        temp = int(val) // 1000
                        if temp > 0:
                            self.ec.cpu_temp = temp
                            return
                    except ValueError:
                        pass

    def _read_sysinfo(self):
        """Read basic system info."""
        s = SystemInfo()
        s.hostname = self.caps.hostname
        s.kernel = self.caps.kernel
        s.distro = self.caps.distro
        s.cpu_model = self.caps.cpu_model
        s.cpu_cores = self.caps.cpu_cores
        s.ram_gb = self.caps.total_ram_gb
        uptime = _read_file("/proc/uptime")
        if uptime:
            try:
                s.uptime_sec = float(uptime.split()[0])
            except ValueError:
                pass
        return s
