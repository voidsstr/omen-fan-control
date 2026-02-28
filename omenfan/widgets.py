"""Rendering primitives for the TUI: safe addstr, boxes, bars, sparklines."""

import curses
import time
from .theme import (C_BORDER, C_TITLE, C_DIM, C_COOL, C_WARM, C_HOT,
                    C_FLO, C_FMID, C_FHI, C_SPARK, SPARK, EIGHTHS,
                    grad_pair)

# Braille fan spinner — box outline with single missing dot traveling clockwise
# 2 chars = 4 cols × 4 rows, 12 perimeter dots form the box.
# One gap travels clockwise through all 12 positions.
#
# Perimeter positions (clockwise from top-left):
#  0  1  2  3
# 11        4
# 10        5
#  9  8  7  6

# Perimeter dot map: (char_index, bit_value) for each position clockwise
_BOX_PERIM = [
    (0, 0x01), (0, 0x08), (1, 0x01), (1, 0x08),  # top L→R
    (1, 0x10), (1, 0x20),                          # right T→B
    (1, 0x80), (1, 0x40), (0, 0x80), (0, 0x40),   # bottom R→L
    (0, 0x04), (0, 0x02),                          # left B→T
]
_FULL_BOX = [0xCF, 0xF9]  # all 12 perimeter dots on

# 12 frames: each with one dot missing from the perimeter
_FAN_FRAMES = []
for _i in range(len(_BOX_PERIM)):
    _chars = list(_FULL_BOX)
    _ci, _bit = _BOX_PERIM[_i]
    _chars[_ci] &= ~_bit
    _FAN_FRAMES.append(chr(0x2800 + _chars[0]) + chr(0x2800 + _chars[1]))
_FAN_STOPPED = chr(0x2800 + _FULL_BOX[0]) + chr(0x2800 + _FULL_BOX[1])


def _s(win, y, x, text, attr=0):
    """Safe addstr with clipping."""
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        ml = w - x
        if y == h - 1:
            ml -= 1
        if ml <= 0:
            return
        win.addstr(y, x, text[:ml], attr)
    except curses.error:
        pass


def _box(win, y, x, h, w, title=""):
    """Rounded box with optional title."""
    b = curses.color_pair(C_BORDER)
    _s(win, y, x, "\u256d" + "\u2500" * (w - 2) + "\u256e", b)
    _s(win, y + h - 1, x, "\u2570" + "\u2500" * (w - 2) + "\u256f", b)
    for i in range(1, h - 1):
        _s(win, y + i, x, "\u2502", b)
        _s(win, y + i, x + w - 1, "\u2502", b)
    if title:
        _s(win, y, x + 2, f" {title} ",
           curses.color_pair(C_TITLE) | curses.A_BOLD)


def _bar(win, y, x, width, frac, style='temp'):
    """Braille dot bar with smooth gradient. Flush edges.

    Filled cells use ⣿ (all dots), empty cells are blank.
    Gradient spans full bar width so color indicates position.
    """
    frac = max(0.0, min(1.0, frac))
    filled = round(frac * width)
    for i in range(filled):
        gp = i / max(1, width - 1)
        _s(win, y, x + i, "\u28ff", grad_pair(gp))


# Braille filled line graph levels (0-8).
# Odd levels: right-column dot at the line position, both columns filled below.
# Even levels: both columns filled to that height.
# Creates a smooth line + area fill effect.
#
# Bit layout per braille char (pin → bit):
#   Row 0: pin1=0x01 pin4=0x08
#   Row 1: pin2=0x02 pin5=0x10
#   Row 2: pin3=0x04 pin6=0x20
#   Row 3: pin7=0x40 pin8=0x80
_LINE_FILL = [
    0x00,                       # 0 — empty
    0x80,                       # 1 — ⢀ line at row 3 right
    0xC0,                       # 2 — ⣀ row 3 filled
    0xE0,                       # 3 — ⣠ row 3 filled + line at row 2 right
    0xE4,                       # 4 — ⣤ rows 2-3 filled
    0xF4,                       # 5 — ⣴ rows 2-3 filled + line at row 1 right
    0xF6,                       # 6 — ⣶ rows 1-3 filled
    0xFE,                       # 7 — ⣾ rows 1-3 filled + line at row 0 right
    0xFF,                       # 8 — ⣿ all filled
]


def _spark(win, y, x, width, history, max_val):
    """Smooth filled line graph using braille characters.

    Right-column dot marks the line at each level; both columns filled
    below.  Gives 8 vertical levels per character with a smooth area-
    under-the-curve look instead of blocky bars.

    Visual progression:  ⠀ ⢀ ⣀ ⣠ ⣤ ⣴ ⣶ ⣾ ⣿
    """
    if not history or max_val <= 0:
        return
    data = list(history)[-width:]
    pad = width - len(data)
    for i in range(width):
        if i < pad:
            _s(win, y, x + i, " ")
        else:
            v = data[i - pad]
            lv = int(v / max_val * 8 + 0.5)
            lv = max(0, min(8, lv))
            vfrac = max(0.0, min(1.0, v / max_val))
            cp = grad_pair(vfrac)
            if lv > 0:
                _s(win, y, x + i, chr(0x2800 + _LINE_FILL[lv]), cp)
            else:
                _s(win, y, x + i, " ")


def fan_icon(rpm, max_rpm=3200):
    """Return a 2-char braille fan icon frame based on RPM.

    Rotation speed scales with RPM: 0 RPM = stopped, max RPM = fastest spin.
    Uses wall-clock time so animation is framerate-independent.
    """
    if rpm <= 0:
        return _FAN_STOPPED
    # Rotations per second: scale RPM to 0.5–8 frames/sec
    rps = 0.5 + (rpm / max_rpm) * 7.5
    frame = int(time.monotonic() * rps) % len(_FAN_FRAMES)
    return _FAN_FRAMES[frame]


def format_bytes(n):
    """Format byte count as human-readable string (e.g., 1.2 GB)."""
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1024.0:
            if unit == 'B':
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def format_rate(n):
    """Format byte rate as human-readable string (e.g., 1.2M/s)."""
    for unit in ('B', 'K', 'M', 'G'):
        if abs(n) < 1024.0:
            if unit == 'B':
                return f"{int(n)}{unit}/s"
            return f"{n:.1f}{unit}/s"
        n /= 1024.0
    return f"{n:.1f}T/s"


def format_duration(seconds):
    """Format seconds as human-readable duration (e.g., 2d 5h 32m)."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds) // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    if hours < 24:
        return f"{hours}h {mins:02d}m"
    days = hours // 24
    hrs = hours % 24
    return f"{days}d {hrs}h {mins:02d}m"


def draw_mini_bars(win, y, x, width, values, max_val):
    """Draw a row of mini braille bars for per-core CPU usage."""
    if not values:
        return
    # Braille vertical fill levels (bottom to top, 4 dot rows)
    BR_LEVELS = [" ", "\u2840", "\u28c0", "\u28e0", "\u28f0", "\u28f8", "\u28fc", "\u28fe", "\u28ff"]
    for i, v in enumerate(values):
        bx = x + i
        if bx >= x + width:
            break
        frac = max(0.0, min(1.0, v / max_val))
        lv = int(frac * 8)
        lv = max(0, min(8, lv))
        cp = grad_pair(frac)
        _s(win, y, bx, BR_LEVELS[lv], cp)
