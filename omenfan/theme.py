"""Color scheme constants and initialization."""

import curses

# Color pair IDs (basic)
C_TITLE  = 1
C_BORDER = 2
C_COOL   = 3
C_WARM   = 4
C_HOT    = 5
C_FLO    = 6
C_FMID   = 7
C_FHI    = 8
C_KEY    = 9
C_ACTIVE = 10
C_DIM    = 11
C_ALERT  = 14
C_CURVE  = 15
C_SPARK  = 16
C_GPU    = 17
C_NET_RX = 18
C_NET_TX = 19
C_MEM    = 20
C_WARN   = 21

# Gradient color pairs (256-color) — muted teal → sage → gold → coral → rose
C_GRAD_BASE = 30
GRAD_COLORS_256 = [72, 108, 144, 150, 186, 180, 174, 173, 167, 131]
GRAD_STEPS = len(GRAD_COLORS_256)

# Whether 256-color gradient is available (set during init)
has_256 = False

# Visual characters
SPARK = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
EIGHTHS = " \u258f\u258e\u258d\u258c\u258b\u258a\u2589\u2588"
SPIN = "\u25d0\u25d3\u25d1\u25d2"


def init_colors():
    """Initialize all curses color pairs."""
    global has_256

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_TITLE,  curses.COLOR_CYAN,    -1)
    curses.init_pair(C_BORDER, curses.COLOR_BLUE,    -1)
    curses.init_pair(C_COOL,   curses.COLOR_GREEN,   -1)
    curses.init_pair(C_WARM,   curses.COLOR_YELLOW,  -1)
    curses.init_pair(C_HOT,    curses.COLOR_RED,     -1)
    curses.init_pair(C_FLO,    curses.COLOR_GREEN,   -1)
    curses.init_pair(C_FMID,   curses.COLOR_CYAN,    -1)
    curses.init_pair(C_FHI,    curses.COLOR_MAGENTA, -1)
    curses.init_pair(C_KEY,    curses.COLOR_YELLOW,  -1)
    curses.init_pair(C_ACTIVE, curses.COLOR_GREEN,   -1)
    curses.init_pair(C_DIM,    curses.COLOR_WHITE,   -1)
    curses.init_pair(C_ALERT,  curses.COLOR_RED,     -1)
    curses.init_pair(C_CURVE,  curses.COLOR_GREEN,   -1)
    curses.init_pair(C_SPARK,  curses.COLOR_CYAN,    -1)
    curses.init_pair(C_GPU,    curses.COLOR_GREEN,   -1)
    curses.init_pair(C_NET_RX, curses.COLOR_GREEN,   -1)
    curses.init_pair(C_NET_TX, curses.COLOR_MAGENTA, -1)
    curses.init_pair(C_MEM,    curses.COLOR_YELLOW,  -1)
    curses.init_pair(C_WARN,   curses.COLOR_RED,     -1)

    # 256-color gradient: green(46) → yellow(226) → red(196)
    try:
        if curses.COLORS >= 256:
            for i, fg in enumerate(GRAD_COLORS_256):
                curses.init_pair(C_GRAD_BASE + i, fg, -1)
            has_256 = True
    except Exception:
        has_256 = False


def grad_pair(frac):
    """Return curses color_pair for a smooth gradient position 0.0→1.0.

    Green at 0.0, yellow at 0.5, red at 1.0.
    Falls back to 3-color discrete if 256 colors unavailable.
    """
    frac = max(0.0, min(1.0, frac))
    if has_256:
        idx = int(frac * (GRAD_STEPS - 1) + 0.5)
        idx = max(0, min(GRAD_STEPS - 1, idx))
        return curses.color_pair(C_GRAD_BASE + idx)
    else:
        if frac < 0.4:
            return curses.color_pair(C_COOL)
        elif frac < 0.7:
            return curses.color_pair(C_WARM)
        else:
            return curses.color_pair(C_HOT)
