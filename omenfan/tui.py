"""Full TUI rendering and input handling."""

import curses
import struct
import time
from collections import deque

from .theme import (C_TITLE, C_BORDER, C_COOL, C_WARM, C_HOT, C_FLO, C_FMID,
                    C_FHI, C_KEY, C_ACTIVE, C_DIM, C_ALERT, C_CURVE, C_SPARK,
                    C_GPU, C_NET_RX, C_NET_TX, C_MEM, C_WARN, SPIN, init_colors)
from .widgets import (_s, _box, _bar, _spark, format_bytes, format_rate,
                      format_duration, draw_mini_bars, fan_icon)
from .fan import (FanController, FanCurve, PROFILE_NONE, PROFILE_MAX,
                  PROFILE_AGGRESSIVE, MAX_RPM, MAX_TEMP)
from .sensors import SensorCollector
from .detect import SystemCapabilities
from . import wmi as wmi_mod


# Standard bar layout: all panels (including fans) start bars at x + BAR_COL
BAR_COL = 14


class OmenFanTUI:
    """Main TUI class — consumes sensors and fan controller."""

    def __init__(self, stdscr, ec, caps, fan_ctl, sensors, monitor_only=False):
        self.scr = stdscr
        self.ec = ec
        self.caps = caps
        self.fan = fan_ctl
        self.sensors = sensors
        self.monitor_only = monitor_only
        self.running = True
        self.rtick = 0
        self.anim = {}

        # Curve editing state
        self.selected_curve = -1
        self.selected_point = 0
        self.edit_mode = False

        # Status bar
        self.status_msg = ""
        self.status_time = 0

        # Cached curves
        # Map standard perf_mode (0=Quiet, 1=Balanced, 3=Performance) to
        # curve_names index: ("Balanced"=0, "Quiet"=1, "Performance"=2)
        self._mode_to_curve = {0: 1, 1: 0, 2: 0, 3: 2}
        self._curves = []
        self._active_curve = self._mode_to_curve.get(fan_ctl.perf_mode, 0)

    def set_status(self, msg):
        self.status_msg = msg
        self.status_time = time.time()

    def _ease(self, key, target):
        cur = self.anim.get(key, target)
        self.anim[key] = cur + (target - cur) * 0.35
        return self.anim[key]

    def refresh_data(self):
        """Refresh all sensor data and fan keepalive."""
        self.sensors.refresh(ec=self.ec)
        self.rtick += 1

        # Read EC-specific fan curves
        if self.ec.method != "none" and not self.monitor_only:
            self._curves = self.fan.read_curves()
            self._active_curve = self._mode_to_curve.get(self.fan.perf_mode, 0)

        # Fan keepalive
        if not self.monitor_only and self.fan.can_control:
            cpu_temp = self.sensors.ec.cpu_temp if self.sensors.ec else 0
            self.fan.keepalive(cpu_temp)

    def handle_input(self, key):
        if key in (ord('q'), ord('Q'), ord('0'), 27):
            if not self.monitor_only:
                self.fan.cleanup()
            self.running = False
            return

        if self.monitor_only:
            return

        if key == ord('1'):
            self.fan.stop_override()
            self.fan.set_perf_mode(1)
            self.set_status("Mode \u2192 Balanced")
        elif key == ord('2'):
            self.fan.stop_override()
            self.fan.set_perf_mode(0)
            self.set_status("Mode \u2192 Quiet")
        elif key == ord('3'):
            self.fan.stop_override()
            self.fan.set_perf_mode(3)
            self.set_status("Mode \u2192 Performance")
        elif key == ord('4'):
            try:
                if self.fan.fan_max:
                    self.fan.stop_override()
                    self.set_status("Fan max OFF")
                else:
                    self.fan.apply_max()
                    fc = self.fan.fan_count
                    self.set_status(f"FULL SPEED \u2014 all {fc} fans max")
            except Exception as e:
                self.fan.fan_max = False
                self.fan.profile = PROFILE_NONE
                self.set_status(f"Error: {e}")
        elif key == ord('5'):
            try:
                if self.fan.profile == PROFILE_AGGRESSIVE:
                    self.fan.stop_override()
                    self.set_status("Aggressive OFF")
                else:
                    self.fan.fan_max = False
                    self.fan.profile = PROFILE_AGGRESSIVE
                    cpu_temp = self.sensors.ec.cpu_temp if self.sensors.ec else 50
                    self.fan.apply_aggressive(cpu_temp)
                    self.set_status("Aggressive curve ON")
            except Exception as e:
                self.fan.fan_max = False
                self.fan.profile = PROFILE_NONE
                self.set_status(f"Error: {e}")
        elif key == ord('6'):
            if not self._curves:
                self.set_status("No EC curves on this model")
                return
            if self.selected_curve == -1:
                self.selected_curve = self._active_curve
                self.selected_point = 0
                self.edit_mode = True
                self.set_status("Edit: \u2190\u2192 point, \u2191\u2193 RPM, ESC exit")
            else:
                self.selected_curve = -1
                self.edit_mode = False
                self.set_status("Edit off")
        elif self.edit_mode and self.selected_curve >= 0 and self._curves:
            curve = self._curves[self.selected_curve]
            if key == curses.KEY_LEFT:
                self.selected_point = max(0, self.selected_point - 1)
            elif key == curses.KEY_RIGHT:
                self.selected_point = min(curve.npoints - 1,
                                          self.selected_point + 1)
            elif key == curses.KEY_UP:
                nr = min(MAX_RPM, curve.rpms[self.selected_point] + 50)
                curve.set_rpm(self.ec, self.selected_point, nr)
                self.set_status(f"Point {self.selected_point}: {nr} RPM")
            elif key == curses.KEY_DOWN:
                nr = max(200, curve.rpms[self.selected_point] - 50)
                curve.set_rpm(self.ec, self.selected_point, nr)
                self.set_status(f"Point {self.selected_point}: {nr} RPM")
        elif key == ord('8'):
            if self._curves:
                curve = self._curves[self._active_curve]
                for i in range(curve.npoints):
                    curve.set_rpm(self.ec, i, min(MAX_RPM, curve.rpms[i] + 100))
                self.set_status("All fans +100 RPM")
            else:
                self.set_status("No EC curves on this model")
        elif key == ord('7'):
            if self._curves:
                curve = self._curves[self._active_curve]
                for i in range(curve.npoints):
                    curve.set_rpm(self.ec, i, max(200, curve.rpms[i] - 100))
                self.set_status("All fans -100 RPM")
            else:
                self.set_status("No EC curves on this model")

    # ── Drawing ──────────────────────────────────────────────────────

    def draw(self):
        try:
            self.scr.erase()
            h, w = self.scr.getmaxyx()
            if w < 50 or h < 15:
                _s(self.scr, 0, 0, f"Terminal too small ({w}x{h}, need 50x15)")
                self.scr.refresh()
                return

            fw = w - 2
            mx = 1
            row = 0

            row = self._draw_header(row, mx, fw)

            # Choose layout
            if fw >= 118:
                row = self._draw_wide(row, mx, fw, h)
            elif fw >= 78:
                row = self._draw_medium(row, mx, fw, h)
            else:
                row = self._draw_narrow(row, mx, fw, h)

            # Controls + status (shared)
            if not self.monitor_only:
                row = self._draw_curves(row, mx, fw, h)
                row = self._draw_controls(row, mx, fw, h)
            row = self._draw_status(row, mx, fw, h)

            self.scr.refresh()
        except curses.error:
            pass

    def _draw_header(self, row, mx, fw):
        """Draw the top header bar with system identity."""
        _box(self.scr, row, mx, 4, fw)

        title = "OMEN FAN CONTROL" if not self.monitor_only else "SYSTEM MONITOR"
        _s(self.scr, row + 1, mx + 3, title,
           curses.color_pair(C_TITLE) | curses.A_BOLD)

        # Right-side info (row+1)
        sysinfo = self.sensors.sysinfo
        ec = self.sensors.ec
        spin = SPIN[self.rtick % len(SPIN)]

        info_parts = []
        if sysinfo:
            if sysinfo.cpu_model:
                model = sysinfo.cpu_model
                for rm in ("Intel(R) Core(TM) ", "(R)", "(TM)"):
                    model = model.replace(rm, "")
                info_parts.append((model.strip(), C_DIM))
                info_parts.append((" \u00b7 ", C_DIM))
            info_parts.append((f"{sysinfo.cpu_cores}C", C_DIM))
            info_parts.append((" \u00b7 ", C_DIM))
            info_parts.append((f"{sysinfo.ram_gb:.0f} GB", C_DIM))

        if not self.monitor_only:
            prof_map = {PROFILE_NONE: "Auto", PROFILE_MAX: "MAX",
                        PROFILE_AGGRESSIVE: "Aggro"}
            prof = prof_map.get(self.fan.profile, "?")
            prof_col = C_ACTIVE
            if self.fan.profile == PROFILE_MAX:
                prof_col = C_ALERT
            elif self.fan.profile == PROFILE_AGGRESSIVE:
                prof_col = C_WARM

            if ec:
                info_parts.append((" \u00b7 ", C_DIM))
                info_parts.append((ec.perf_mode_name, C_DIM))
            info_parts.append((" \u00b7 ", C_DIM))
            info_parts.append((prof, prof_col))

        info_parts.append(("  ", C_DIM))
        info_parts.append((spin, C_TITLE))

        total_len = sum(len(t) for t, _ in info_parts)
        ix = mx + fw - 2 - total_len
        for text, col in info_parts:
            attr = curses.color_pair(col)
            if col in (C_ALERT, C_WARM, C_TITLE):
                attr |= curses.A_BOLD
            _s(self.scr, row + 1, ix, text, attr)
            ix += len(text)

        # Subtitle line (row+2): distro · kernel · uptime · procs
        sub_parts = []
        if sysinfo:
            if sysinfo.hostname:
                sub_parts.append((sysinfo.hostname, C_TITLE))
            if sysinfo.distro:
                # Shorten distro name
                distro = sysinfo.distro
                for rm in ("GNU/Linux", "Linux"):
                    distro = distro.replace(rm, "").strip()
                if len(distro) > 24:
                    distro = distro[:24]
                sub_parts.append((distro, C_DIM))
            if sysinfo.kernel:
                # Just major.minor.patch
                kern = sysinfo.kernel.split("-")[0]
                if len(sysinfo.kernel.split("-")) > 1:
                    kern += "-" + sysinfo.kernel.split("-")[1]
                sub_parts.append((kern, C_DIM))
            if sysinfo.uptime_sec > 0:
                sub_parts.append(("\u2191 " + format_duration(sysinfo.uptime_sec),
                                  C_ACTIVE))
        cpu = self.sensors.cpu
        if cpu and cpu.proc_total > 0:
            sub_parts.append((f"{cpu.proc_total} procs", C_DIM))

        if ec:
            sub_parts.append((f"HB:{ec.heartbeat:3d}", C_DIM))

        sx = mx + 3
        for i, (text, col) in enumerate(sub_parts):
            if i > 0:
                _s(self.scr, row + 2, sx, " \u00b7 ", curses.color_pair(C_DIM))
                sx += 3
            attr = curses.color_pair(col)
            if col == C_TITLE:
                attr |= curses.A_BOLD
            _s(self.scr, row + 2, sx, text, attr)
            sx += len(text)

        return row + 4

    # ── Wide layout (≥120 cols) — three columns ──────────────────

    def _draw_wide(self, row, mx, fw, max_h):
        """Three-column layout: left=CPU+Net, center=Fans+Temps+Curves, right=GPU+Mem+Disk."""
        col_w = fw // 3
        lx = mx
        cx = mx + col_w
        rx = mx + col_w * 2
        rw = fw - col_w * 2   # Right col gets remainder

        # Left column: CPU + Network
        row_l = row
        row_l = self._draw_cpu_panel(row_l, lx, col_w, max_h)
        row_l = self._draw_net_panel(row_l, lx, col_w, max_h)

        # Center column: Fans + Temperatures
        row_c = row
        row_c = self._draw_fans_panel(row_c, cx, col_w, max_h)
        row_c = self._draw_temps_panel(row_c, cx, col_w, max_h)

        # Right column: GPU + Memory + Disk + Battery
        row_r = row
        row_r = self._draw_gpu_panel(row_r, rx, rw, max_h)
        row_r = self._draw_mem_panel(row_r, rx, rw, max_h)
        row_r = self._draw_disk_panel(row_r, rx, rw, max_h)
        row_r = self._draw_battery_panel(row_r, rx, rw, max_h)

        return max(row_l, row_c, row_r)

    # ── Medium layout (≥80 cols) — two columns ───────────────────

    def _draw_medium(self, row, mx, fw, max_h):
        """Two-column layout."""
        lw = fw // 2
        rw = fw - lw
        lx = mx
        rx = mx + lw

        row_l = row
        row_l = self._draw_cpu_panel(row_l, lx, lw, max_h)
        row_l = self._draw_temps_panel(row_l, lx, lw, max_h)
        row_l = self._draw_net_panel(row_l, lx, lw, max_h)

        row_r = row
        row_r = self._draw_fans_panel(row_r, rx, rw, max_h)
        row_r = self._draw_gpu_panel(row_r, rx, rw, max_h)
        row_r = self._draw_mem_panel(row_r, rx, rw, max_h)
        row_r = self._draw_disk_panel(row_r, rx, rw, max_h)
        row_r = self._draw_battery_panel(row_r, rx, rw, max_h)

        return max(row_l, row_r)

    # ── Narrow layout (<80 cols) — stacked ───────────────────────

    def _draw_narrow(self, row, mx, fw, max_h):
        """Single-column stacked layout."""
        row = self._draw_cpu_panel(row, mx, fw, max_h)
        row = self._draw_fans_panel(row, mx, fw, max_h)
        row = self._draw_temps_panel(row, mx, fw, max_h)
        row = self._draw_gpu_panel(row, mx, fw, max_h)
        row = self._draw_mem_panel(row, mx, fw, max_h)
        row = self._draw_net_panel(row, mx, fw, max_h)
        row = self._draw_disk_panel(row, mx, fw, max_h)
        row = self._draw_battery_panel(row, mx, fw, max_h)
        return row

    # ── Individual panels ────────────────────────────────────────

    def _draw_cpu_panel(self, row, x, w, max_h):
        """CPU usage, frequency, load, sparkline, per-core bars."""
        cpu = self.sensors.cpu
        if not cpu:
            return self._draw_placeholder(row, x, w, "CPU", "Collecting...")
        if row + 7 > max_h - 4:
            return row

        # Height: usage + freq + load + sparklines + per-core + border
        has_cores = len(cpu.per_core_pct) > 0
        ph = 5 + (1 if has_cores else 0) + 2
        _box(self.scr, row, x, ph, w, "CPU")

        bx = x + BAR_COL
        bar_w = w - BAR_COL - 8
        y = row + 1

        # Usage
        frac = self._ease("cpu_usage", cpu.usage_pct / 100.0)
        uc = C_COOL if cpu.usage_pct < 40 else C_WARM if cpu.usage_pct < 70 else C_HOT
        _s(self.scr, y, x + 2, "Usage", curses.color_pair(C_DIM))
        _bar(self.scr, y, bx, max(4, bar_w), frac, 'temp')
        _s(self.scr, y, bx + max(4, bar_w) + 1, f"{cpu.usage_pct:4.0f}%",
           curses.color_pair(uc) | curses.A_BOLD)
        y += 1

        # Frequency with range
        if cpu.freq_mhz > 0:
            freq_ghz = cpu.freq_mhz / 1000
            max_ghz = 6.0
            frac = self._ease("cpu_freq", min(1.0, freq_ghz / max_ghz))
            _s(self.scr, y, x + 2, "Freq ", curses.color_pair(C_DIM))
            _bar(self.scr, y, bx, max(4, bar_w), frac, 'fan')
            freq_s = f"{freq_ghz:4.1f}G"
            _s(self.scr, y, bx + max(4, bar_w) + 1, freq_s,
               curses.color_pair(C_FMID) | curses.A_BOLD)
        y += 1

        # Load + process count + governor
        _s(self.scr, y, x + 2, "Load ", curses.color_pair(C_DIM))
        lc = C_COOL if cpu.load_1 < self.caps.cpu_cores * 0.5 else C_WARM if cpu.load_1 < self.caps.cpu_cores else C_HOT
        load_s = f"{cpu.load_1:.2f}  {cpu.load_5:.2f}  {cpu.load_15:.2f}"
        _s(self.scr, y, bx, load_s, curses.color_pair(lc))
        # Process count and governor right-aligned
        extra = ""
        if cpu.proc_running > 0:
            extra = f"{cpu.proc_running}/{cpu.proc_total}P"
        if cpu.governor:
            gov_short = cpu.governor[:4]
            extra = f"{extra} {gov_short}" if extra else gov_short
        if extra:
            _s(self.scr, y, x + w - len(extra) - 2, extra,
               curses.color_pair(C_DIM) | curses.A_DIM)
        y += 1

        # CPU usage sparkline
        sk_w = w - BAR_COL - 2
        if sk_w > 8 and self.sensors.hist_cpu_usage:
            _s(self.scr, y, x + 2, "use ", curses.color_pair(C_DIM) | curses.A_DIM)
            _spark(self.scr, y, bx, sk_w, self.sensors.hist_cpu_usage, 100)
        y += 1

        # EC CPU temp sparkline (if available)
        if self.sensors.hist_cpu_temp:
            _s(self.scr, y, x + 2, "tmp ", curses.color_pair(C_DIM) | curses.A_DIM)
            _spark(self.scr, y, bx, sk_w, self.sensors.hist_cpu_temp, MAX_TEMP)
            y += 1

        # Per-core mini bars
        if has_cores and w > 30:
            _s(self.scr, y, x + 2, "cores", curses.color_pair(C_DIM) | curses.A_DIM)
            draw_mini_bars(self.scr, y, bx, w - BAR_COL - 2, cpu.per_core_pct, 100)

        return row + ph

    def _draw_fans_panel(self, row, x, w, max_h):
        """Fan RPM bars with animated braille fan spinner icons."""
        ec = self.sensors.ec
        gpu = self.sensors.gpu
        if not ec and not gpu:
            if self.monitor_only and not self.caps.is_omen:
                return row  # No fans to show on non-OMEN in monitor mode
            return self._draw_placeholder(row, x, w, "Fans", "No EC data")

        fans = []
        if ec:
            labels = self.caps.ec_regmap.fan_labels
            is_laptop = self.caps.ec_regmap.chassis == "laptop"

            if is_laptop:
                # Laptop: rear_rpms has all fans, front_duty has pct for each
                for i, rpm in enumerate(ec.rear_rpms):
                    label = labels[i] if i < len(labels) else f"Fan {i+1}"
                    pct = ec.front_duty[i] if i < len(ec.front_duty) else -1
                    suffix = f"{pct}%" if pct >= 0 else ""
                    fans.append((label, rpm, suffix, False))
            else:
                # Desktop: rear fans (WMI mode) + front fans (EC duty)
                for i, rpm in enumerate(ec.rear_rpms):
                    label = labels[i] if i < len(labels) else f"Rear {i+1}"
                    fans.append((label, rpm, ec.fan_mode_name, False))
                for i, rpm in enumerate(ec.front_rpms):
                    li = len(ec.rear_rpms) + i
                    label = labels[li] if li < len(labels) else f"Front {i+1}"
                    pct = ec.front_duty[i] if i < len(ec.front_duty) else -1
                    suffix = f"{pct}%" if pct >= 0 else ""
                    fans.append((label, rpm, suffix, False))
        if gpu and gpu.fan_pct > 0:
            fans.append(("NV GPU", gpu.fan_pct, f"{gpu.temp_c}\u00b0C", True))

        if not fans:
            return row
        if row + len(fans) + 2 > max_h - 4:
            return row

        ph = len(fans) + 2
        _box(self.scr, row, x, ph, w, "Fans")

        bx = x + BAR_COL
        bar_w = w - BAR_COL - 8
        for i, (label, value, suffix, is_pct) in enumerate(fans):
            fy = row + 1 + i

            # Animated fan icon
            if is_pct:
                icon_rpm = int(value / 100.0 * MAX_RPM)
            else:
                icon_rpm = value
            icon = fan_icon(icon_rpm, MAX_RPM)
            fc_icon = C_FMID if icon_rpm < 1500 else C_FHI
            _s(self.scr, fy, x + 2, icon, curses.color_pair(fc_icon))

            # Label
            _s(self.scr, fy, x + 5, f"{label:<8}", curses.color_pair(C_DIM))

            # Bar
            if is_pct:
                frac = self._ease(f"f_{label}", value / 100.0)
                fc = C_FMID
                val_s = f"{value:3d}%"
            else:
                frac = self._ease(f"f_{label}", value / MAX_RPM)
                fc = C_FLO if value < 800 else C_FMID if value < 1500 else C_FHI
                val_s = f"{value:5d}"
            _bar(self.scr, fy, bx, max(4, bar_w), frac, 'fan')
            _s(self.scr, fy, bx + max(4, bar_w) + 1, val_s,
               curses.color_pair(fc) | curses.A_BOLD)

        return row + ph

    def _draw_temps_panel(self, row, x, w, max_h):
        """Temperature panel — EC temps if available, else hwmon."""
        ec = self.sensors.ec

        temps = []
        if ec and (ec.cpu_temp > 0 or ec.board_temp > 0):
            temps.append(("CPU", ec.cpu_temp))
            board_label = "GPU" if self.caps.ec_regmap.chassis == "laptop" else "Board"
            if ec.board_temp > 0:
                temps.append((board_label, ec.board_temp))
            if ec.vrm_temp > 0:
                temps.append(("VRM", ec.vrm_temp))
            if ec.dimm_temps:
                avg_d = sum(ec.dimm_temps) // max(1, len(ec.dimm_temps))
                if avg_d > 0:
                    temps.append(("DIMM", avg_d))
        else:
            # Fall back to hwmon thermals
            for tz in self.sensors.thermals:
                if tz.source == "hwmon" and tz.temp_c > 0:
                    # Shorten name
                    name = tz.name.split("/")[-1][:8]
                    temps.append((name, int(tz.temp_c)))
                if len(temps) >= 6:
                    break

        # NVIDIA GPU temp
        gpu = self.sensors.gpu
        if gpu and gpu.temp_c > 0:
            temps.append(("NV GPU", gpu.temp_c))

        if not temps:
            return row
        if row + len(temps) + 2 > max_h - 4:
            return row

        ph = len(temps) + 2
        _box(self.scr, row, x, ph, w, "Temperatures")

        bx = x + BAR_COL
        bar_w = w - BAR_COL - 8
        for i, (label, temp) in enumerate(temps):
            ty = row + 1 + i
            frac = self._ease(f"t_{label}", temp / MAX_TEMP)
            tc = C_COOL if temp < 50 else C_WARM if temp < 75 else C_HOT
            _s(self.scr, ty, x + 2, f"{label:<6}", curses.color_pair(C_DIM))
            _bar(self.scr, ty, bx, max(4, bar_w), frac, 'temp')
            _s(self.scr, ty, bx + max(4, bar_w) + 1, f"{temp:3d}\u00b0C",
               curses.color_pair(tc) | curses.A_BOLD)

        return row + ph

    def _draw_gpu_panel(self, row, x, w, max_h):
        """NVIDIA GPU panel — temp, power, VRAM, clocks, sparklines."""
        gpu = self.sensors.gpu
        if not gpu:
            if self.caps.has_nvidia:
                return self._draw_placeholder(row, x, w, "GPU", "nvidia-smi unavailable")
            return row
        if row + 6 > max_h - 4:
            return row

        has_spark = len(self.sensors.hist_gpu_usage) > 2
        # name + util + power + vram + clocks = 5 content rows + 2 border
        ph = 7 + (1 if has_spark else 0)
        _box(self.scr, row, x, ph, w, "GPU")
        y = row + 1
        bx = x + BAR_COL
        bar_w = w - BAR_COL - 8

        # Name
        name = gpu.name
        for rm in ("NVIDIA ", "GeForce "):
            name = name.replace(rm, "")
        _s(self.scr, y, x + 2, name[:w-4], curses.color_pair(C_GPU) | curses.A_BOLD)
        y += 1

        # Utilization
        frac = self._ease("gpu_util", gpu.util_pct / 100.0)
        _s(self.scr, y, x + 2, "Util", curses.color_pair(C_DIM))
        _bar(self.scr, y, bx, max(4, bar_w), frac, 'temp')
        _s(self.scr, y, bx + max(4, bar_w) + 1, f"{gpu.util_pct:3d}%",
           curses.color_pair(C_GPU) | curses.A_BOLD)
        y += 1

        # Power
        max_power = 575.0  # RTX 5090 TDP
        pfrac = self._ease("gpu_pwr", gpu.power_w / max_power)
        _s(self.scr, y, x + 2, "Pwr ", curses.color_pair(C_DIM))
        _bar(self.scr, y, bx, max(4, bar_w), pfrac, 'temp')
        _s(self.scr, y, bx + max(4, bar_w) + 1, f"{gpu.power_w:5.0f}W",
           curses.color_pair(C_WARM) | curses.A_BOLD)
        y += 1

        # VRAM
        vfrac = self._ease("gpu_vram", gpu.vram_used_pct / 100.0)
        used_gb = gpu.vram_used_mb / 1024
        total_gb = gpu.vram_total_mb / 1024
        _s(self.scr, y, x + 2, "VRAM", curses.color_pair(C_DIM))
        _bar(self.scr, y, bx, max(4, bar_w), vfrac, 'fan')
        vram_s = f"{used_gb:.0f}/{total_gb:.0f}G"
        _s(self.scr, y, bx + max(4, bar_w) + 1, vram_s,
           curses.color_pair(C_FMID) | curses.A_BOLD)
        y += 1

        # Clocks + pstate + driver
        clk_s = f"Core {gpu.clock_core_mhz}  Mem {gpu.clock_mem_mhz}  {gpu.pstate}"
        if gpu.driver_version:
            clk_s += f"  drv {gpu.driver_version}"
        _s(self.scr, y, x + 2, clk_s[:w-4], curses.color_pair(C_DIM))
        y += 1

        # GPU usage sparkline
        if has_spark:
            sk_w = w - BAR_COL - 2
            if sk_w > 8:
                _s(self.scr, y, x + 2, "use ", curses.color_pair(C_DIM) | curses.A_DIM)
                _spark(self.scr, y, bx, sk_w, self.sensors.hist_gpu_usage, 100)

        return row + ph

    def _draw_mem_panel(self, row, x, w, max_h):
        """Memory usage — RAM + Swap + cache breakdown + sparkline."""
        mem = self.sensors.memory
        if not mem:
            return row

        has_spark = len(self.sensors.hist_mem_pct) > 2
        ph = 5 + (1 if has_spark else 0)
        if row + ph > max_h - 4:
            return row

        _box(self.scr, row, x, ph, w, "Memory")
        bx = x + BAR_COL
        bar_w = w - BAR_COL - 8
        y = row + 1

        # RAM
        frac = self._ease("mem_ram", mem.used_pct / 100.0)
        used_gb = mem.used_kb / 1048576
        total_gb = mem.total_kb / 1048576
        _s(self.scr, y, x + 2, "RAM ", curses.color_pair(C_DIM))
        _bar(self.scr, y, bx, max(4, bar_w), frac, 'temp')
        ram_s = f"{used_gb:.0f}/{total_gb:.0f}G"
        mc = C_COOL if mem.used_pct < 60 else C_WARM if mem.used_pct < 85 else C_HOT
        _s(self.scr, y, bx + max(4, bar_w) + 1, ram_s,
           curses.color_pair(mc) | curses.A_BOLD)
        y += 1

        # Swap
        if mem.swap_total_kb > 0:
            sfrac = self._ease("mem_swap", mem.swap_used_pct / 100.0)
            sused_gb = mem.swap_used_kb / 1048576
            stotal_gb = mem.swap_total_kb / 1048576
            _s(self.scr, y, x + 2, "Swap", curses.color_pair(C_DIM))
            _bar(self.scr, y, bx, max(4, bar_w), sfrac, 'fan')
            swap_s = f"{sused_gb:.1f}/{stotal_gb:.0f}G"
            sc = C_COOL if mem.swap_used_pct < 30 else C_WARN if mem.swap_used_pct < 60 else C_HOT
            _s(self.scr, y, bx + max(4, bar_w) + 1, swap_s,
               curses.color_pair(sc) | curses.A_BOLD)
        y += 1

        # Cache + Buffers + Dirty breakdown
        cached_gb = mem.cached_kb / 1048576
        buf_gb = mem.buffers_kb / 1048576
        shared_gb = mem.shared_kb / 1048576
        detail = f"Cache {cached_gb:.1f}G  Buf {buf_gb:.1f}G  Shm {shared_gb:.1f}G"
        if mem.dirty_kb > 1024:
            dirty_mb = mem.dirty_kb / 1024
            detail += f"  Dirty {dirty_mb:.0f}M"
        _s(self.scr, y, x + 2, detail[:w-4], curses.color_pair(C_DIM) | curses.A_DIM)
        y += 1

        # Memory sparkline
        if has_spark:
            sk_w = w - BAR_COL - 2
            _s(self.scr, y, x + 2, "use ", curses.color_pair(C_DIM) | curses.A_DIM)
            _spark(self.scr, y, bx, sk_w, self.sensors.hist_mem_pct, 100)

        return row + ph

    def _draw_net_panel(self, row, x, w, max_h):
        """Network interfaces — RX/TX rates + sparkline."""
        ifaces = self.sensors.net_interfaces
        if not ifaces:
            return row

        has_spark = len(self.sensors.hist_net_rx) > 2
        ph = len(ifaces) + 2 + (1 if has_spark else 0)
        if row + ph > max_h - 4:
            return row

        _box(self.scr, row, x, ph, w, "Network")

        for i, ni in enumerate(ifaces):
            ny = row + 1 + i
            rx_s = format_rate(ni.rx_bytes_sec)
            tx_s = format_rate(ni.tx_bytes_sec)
            name = ni.name[:10]
            _s(self.scr, ny, x + 2, f"{name:<10}", curses.color_pair(C_DIM))
            _s(self.scr, ny, x + 13, "\u25bc", curses.color_pair(C_NET_RX))
            _s(self.scr, ny, x + 14, f"{rx_s:>9}", curses.color_pair(C_NET_RX))
            _s(self.scr, ny, x + 24, " \u25b2", curses.color_pair(C_NET_TX))
            _s(self.scr, ny, x + 26, f"{tx_s:>9}", curses.color_pair(C_NET_TX))

        # Combined throughput sparkline
        if has_spark:
            sy = row + 1 + len(ifaces)
            bx = x + BAR_COL
            sk_w = w - BAR_COL - 2
            if sk_w > 8:
                _s(self.scr, sy, x + 2, "rx ", curses.color_pair(C_NET_RX) | curses.A_DIM)
                # Scale to max seen value
                max_rx = max(self.sensors.hist_net_rx) if self.sensors.hist_net_rx else 1
                max_rx = max(max_rx, 1024)  # at least 1KB
                _spark(self.scr, sy, bx, sk_w, self.sensors.hist_net_rx, max_rx)

        return row + ph

    def _draw_disk_panel(self, row, x, w, max_h):
        """Disk usage bars + I/O rates + temps."""
        disks = self.sensors.disks
        if not disks:
            return row
        # Limit to 4 disks
        disks = disks[:4]
        dio = self.sensors.disk_io

        has_io = dio and (dio.read_bytes_sec > 0 or dio.write_bytes_sec > 0
                          or len(self.sensors.hist_disk_read) > 2)
        ph = len(disks) + 2 + (1 if has_io else 0)
        if row + ph > max_h - 4:
            return row

        _box(self.scr, row, x, ph, w, "Disk")

        bx = x + BAR_COL
        bar_w = w - BAR_COL - 10
        for i, d in enumerate(disks):
            dy = row + 1 + i
            frac = self._ease(f"disk_{d.mount}", d.used_pct / 100.0)
            mount = d.mount if len(d.mount) <= 6 else d.mount[:6]
            _s(self.scr, dy, x + 2, f"{mount:<6}", curses.color_pair(C_DIM))
            dc = C_COOL if d.used_pct < 70 else C_WARM if d.used_pct < 90 else C_HOT
            _bar(self.scr, dy, bx, max(4, bar_w), frac, 'temp')
            size_s = f"{d.used_gb:.0f}/{d.total_gb:.0f}G"
            _s(self.scr, dy, bx + max(4, bar_w) + 1, size_s,
               curses.color_pair(dc) | curses.A_BOLD)
            # NVMe temp inline after size
            if d.temp_c > 0:
                tx = bx + max(4, bar_w) + 1 + len(size_s) + 1
                ttc = C_COOL if d.temp_c < 45 else C_WARM if d.temp_c < 65 else C_HOT
                _s(self.scr, dy, tx, f"{d.temp_c}\u00b0",
                   curses.color_pair(ttc))

        # I/O rates
        if has_io:
            iy = row + 1 + len(disks)
            _s(self.scr, iy, x + 2, "I/O ", curses.color_pair(C_DIM))
            rd_s = format_rate(dio.read_bytes_sec)
            wr_s = format_rate(dio.write_bytes_sec)
            _s(self.scr, iy, bx, f"R:", curses.color_pair(C_NET_RX) | curses.A_DIM)
            _s(self.scr, iy, bx + 2, f"{rd_s:<10}",
               curses.color_pair(C_NET_RX))
            _s(self.scr, iy, bx + 12, f"W:", curses.color_pair(C_NET_TX) | curses.A_DIM)
            _s(self.scr, iy, bx + 14, wr_s,
               curses.color_pair(C_NET_TX))

        return row + ph

    def _draw_battery_panel(self, row, x, w, max_h):
        """Battery panel — only shown when battery is present (laptops)."""
        bat = self.sensors.battery
        if not bat:
            return row
        if row + 3 > max_h - 4:
            return row

        ph = 3
        _box(self.scr, row, x, ph, w, "Battery")
        bx = x + BAR_COL
        bar_w = w - BAR_COL - 8
        y = row + 1

        frac = self._ease("bat_cap", bat.capacity_pct / 100.0)
        bc = C_HOT if bat.capacity_pct < 15 else C_WARN if bat.capacity_pct < 30 else C_COOL
        status_icon = "\u26a1" if bat.status == "Charging" else "\u2193" if bat.status == "Discharging" else "\u2713"
        _s(self.scr, y, x + 2, f"{status_icon} {bat.status[:6]}", curses.color_pair(C_DIM))
        _bar(self.scr, y, bx, max(4, bar_w), frac, 'fan')
        suf = f"{bat.capacity_pct:3d}%"
        if bat.power_w > 0:
            suf += f" {bat.power_w:.0f}W"
        _s(self.scr, y, bx + max(4, bar_w) + 1, suf[:8],
           curses.color_pair(bc) | curses.A_BOLD)

        return row + ph

    def _draw_placeholder(self, row, x, w, title, msg):
        """Draw a minimal placeholder panel for unavailable data."""
        _box(self.scr, row, x, 3, w, title)
        _s(self.scr, row + 1, x + 2, f"[{msg}]",
           curses.color_pair(C_DIM) | curses.A_DIM)
        return row + 3

    def _draw_curves(self, row, x, fw, max_h):
        """Draw fan curves: EC curves + Aggressive + Max."""
        # Build list of all displayable curves
        lines = []  # (name, points_str, is_active, ec_curve_idx)

        # EC curves (Balanced, Quiet, Performance)
        for ci, curve in enumerate(self._curves):
            is_act = (self.fan.profile == PROFILE_NONE
                      and ci == self._active_curve)
            pts = []
            for pi in range(curve.npoints):
                pts.append(f"{curve.temps[pi]}\u2192{curve.rpms[pi]}")
            lines.append((curve.name, pts, is_act, ci))

        # Aggressive curve
        from .fan import AGGRESSIVE_CURVE
        agg_active = self.fan.profile == PROFILE_AGGRESSIVE
        agg_pts = [f"{t}\u2192{d}%" for t, d, _ in AGGRESSIVE_CURVE]
        lines.append(("Aggressive", agg_pts, agg_active, -1))

        # Max
        max_active = self.fan.fan_max
        lines.append(("Max", ["100%"], max_active, -1))

        if not lines:
            return row
        cv_h = len(lines) + 2
        if row + cv_h + 6 >= max_h:
            return row

        _box(self.scr, row, x, cv_h, fw, "Fan Curves")
        for li, (name, pts, is_act, ec_idx) in enumerate(lines):
            cy = row + 1 + li
            is_ed = ec_idx >= 0 and ec_idx == self.selected_curve and self.edit_mode

            # Name: green+bold when active, dim otherwise
            if is_act:
                name_attr = curses.color_pair(C_ACTIVE) | curses.A_BOLD
            else:
                name_attr = curses.color_pair(C_DIM)
            _s(self.scr, cy, x + 2, f"{name:<12}", name_attr)

            # Points
            px = x + 15
            col = C_CURVE if is_act else C_DIM
            for pi, pt in enumerate(pts):
                if is_ed and pi == self.selected_point:
                    _s(self.scr, cy, px, f"[{pt}]",
                       curses.color_pair(C_ALERT) | curses.A_BOLD
                       | curses.A_REVERSE)
                    px += len(pt) + 3
                else:
                    _s(self.scr, cy, px, pt, curses.color_pair(col))
                    px += len(pt) + 2

        return row + cv_h

    def _draw_controls(self, row, x, fw, max_h):
        """Draw single-line controls legend."""
        if row >= max_h - 1:
            return row

        cx = x + 1
        items = [("1", "Bal"), ("2", "Qt"), ("3", "Perf"),
                 ("4", "Max"), ("5", "Aggro"), ("6", "Edit"),
                 ("7", "-"), ("8", "+"), ("q", "Quit")]
        for key, desc in items:
            _s(self.scr, row, cx, key,
               curses.color_pair(C_KEY) | curses.A_BOLD)
            cx += len(key)
            _s(self.scr, row, cx, f"{desc} ", curses.color_pair(C_DIM))
            cx += len(desc) + 1

        return row + 1

    def _draw_status(self, row, x, fw, max_h):
        """Draw the status bar + warnings."""
        if row >= max_h - 1:
            return row

        # Status message
        if self.status_msg and (time.time() - self.status_time) < 5:
            _s(self.scr, row, x + 1, "\u25cf ",
               curses.color_pair(C_ACTIVE) | curses.A_BOLD)
            _s(self.scr, row, x + 3, self.status_msg,
               curses.color_pair(C_ACTIVE))
            row += 1

        # Warnings
        for warn in self.caps.warnings[:2]:
            if row >= max_h - 1:
                break
            _s(self.scr, row, x + 1, "\u26a0 ", curses.color_pair(C_WARN) | curses.A_BOLD)
            _s(self.scr, row, x + 3, warn[:fw - 4], curses.color_pair(C_WARN))
            row += 1

        return row

    def run(self):
        """Main event loop."""
        self.scr.nodelay(True)
        self.scr.timeout(100)   # 10fps for smooth animation
        curses.curs_set(0)
        init_colors()

        last_read = 0
        while self.running:
            now = time.time()
            if now - last_read >= 1.0:
                self.refresh_data()
                last_read = now
            self.draw()
            key = self.scr.getch()
            if key != -1:
                self.handle_input(key)
                while True:
                    k2 = self.scr.getch()
                    if k2 == -1:
                        break
                    self.handle_input(k2)
