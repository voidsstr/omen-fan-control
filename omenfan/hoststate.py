"""Fleet host status — the retro-agent login-screen wall, in the terminal.

On a machine running retro-agent's `retro-dashboard-collector`, everything the
GDM login-screen wall shows is published to one small JSON file:

    /run/retro-dashboard/state.json    (override: $OMENFAN_HOST_STATE)

This module reads that file and turns each section into a panel — FLEET,
SERVICES, PXE, REMOTE, AGENTS, FAVOURITES AGENT, the game-server watchdog and
WEB SITES — using the wall's own status vocabulary, so a glyph means the same
thing here as it does on the monitor.

Nothing is gathered here: the collector is a root service that already does
the probing on its own cadence, and re-doing it from a TUI render loop would
put TCP sweeps and `systemctl` calls on the path that keeps the screen
painting.  On a machine without the collector the file is absent and every
panel simply stays hidden.

Panels are built as rows of (text, colour, flags) segments with no curses
dependency, so the builders can be exercised without a terminal.
"""

import json
import os
import time

from .theme import (C_TITLE, C_ACTIVE, C_DIM, C_ALERT, C_WARM, C_COOL,
                    C_NET_TX, C_SPARK)


STATE_PATH = os.environ.get("OMENFAN_HOST_STATE",
                            "/run/retro-dashboard/state.json")
REFRESH_SEC = 2.0
STALE_AFTER_SEC = 15


# ── Status vocabulary (mirrors retro-agent dashboard/extension/render.js) ──
#
# Only warn / blocked / fail are faults.  "Never installed", "switched off on
# purpose", "could not find out" and "ran and failed" are different calls to
# action and must not collapse into one red dot.

STATUS = {
    #          glyph   colour     flags rank label
    "ok":      ("●", C_ACTIVE, "",  0, "ok"),
    "busy":    ("◐", C_TITLE,  "",  1, "running"),
    "off":     ("○", C_DIM,    "d", 2, "off"),
    "absent":  ("·", C_DIM,    "d", 3, "absent"),
    "unknown": ("?", C_DIM,    "",  4, "unknown"),
    "stale":   ("⋯", C_DIM,    "",  5, "stale"),
    "warn":    ("▲", C_WARM,   "b", 6, "warn"),
    "blocked": ("‖", C_NET_TX, "b", 7, "blocked"),
    "fail":    ("✕", C_ALERT,  "b", 8, "fail"),
}


def mark(st):
    """Status glyph segment."""
    g, col, flags, _, _ = STATUS.get(st, STATUS["unknown"])
    return (g, col, flags)


def st_color(st):
    return STATUS.get(st, STATUS["unknown"])[1]


def worst(states):
    """A group is green only when everything in it is green."""
    best = "ok"
    for s in states:
        if STATUS.get(s, STATUS["unknown"])[3] > STATUS[best][3]:
            best = s
    return best


def age(sec):
    """Compact age: 5s, 3m, 2h, 4d."""
    try:
        s = float(sec)
    except (TypeError, ValueError):
        return "—"
    if s != s or s < 0:
        return "—"
    if s < 60:
        return f"{int(s)}s"
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < 86400:
        return f"{int(s // 3600)}h"
    return f"{int(s // 86400)}d"


def uptime(sec):
    """Uptime: 3d 4h, 5h 12m, 7m."""
    try:
        s = int(sec or 0)
    except (TypeError, ValueError):
        return "—"
    d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def pad(text, n):
    text = str(text if text is not None else "")
    return text[:n].ljust(n)


def seg(text, col=C_DIM, flags=""):
    return (str(text), col, flags)


# ── Reader ──────────────────────────────────────────────────────────

class HostState:
    """Rate-limited reader for the collector's state file.

    The file lives on tmpfs, is ~20 KB and is replaced atomically by the
    collector (write-tmp + rename), so a plain read never sees half a file
    and costs microseconds — no thread needed.
    """

    def __init__(self, path=STATE_PATH):
        self.path = path
        self.state = None
        self.error = ""
        self._last_read = 0.0
        self._mtime = 0.0

    def refresh(self, now=None):
        now = time.time() if now is None else now
        if now - self._last_read < REFRESH_SEC:
            return
        self._last_read = now
        try:
            mt = os.stat(self.path).st_mtime
            if mt == self._mtime and self.state is not None:
                return
            with open(self.path, "r", encoding="utf-8") as f:
                self.state = json.load(f)
            self._mtime = mt
            self.error = ""
        except FileNotFoundError:
            self.state = None
            self.error = "collector not running"
        except (OSError, ValueError) as e:
            # Keep the last good sample; its age will mark it stale.
            self.error = str(e)[:60]

    @property
    def active(self):
        return self.state is not None

    def age_sec(self, now=None):
        if not self.state:
            return None
        now = time.time() if now is None else now
        return max(0.0, now - float(self.state.get("ts") or 0))

    def stale(self):
        a = self.age_sec()
        return a is not None and a > STALE_AFTER_SEC

    def section(self, key):
        return (self.state or {}).get(key) or {}


# ── Panel builders ──────────────────────────────────────────────────
#
# Each returns (title, rows); rows are lists of segments.  Panels that have
# nothing to say return None and are not drawn.

def build_fleet(fl):
    nodes = fl.get("nodes") or []
    title = f"Fleet  {fl.get('up', 0)}/{fl.get('total', 0)} up"
    if fl.get("disabled"):
        return title, [[seg("  polling disabled", C_DIM, "d")]]
    if fl.get("error"):
        return title, [[mark("fail"), seg(" " + str(fl["error"]), C_ALERT)]]
    if not nodes:
        return title, [[seg("  no nodes configured", C_DIM, "d")]]
    rows = []
    for n in nodes:
        # A box that is off is `off`, never a fault: the fleet is powered on
        # demand and an empty sweep is the normal resting state.
        up = bool(n.get("up"))
        name = (n.get("name") or n.get("label")) if up else n.get("label")
        r = [mark("ok" if up else "off"),
             seg(" " + pad(name, 17), C_DIM if not up else C_TITLE,
                 "" if up else "d")]
        if up:
            r.append(seg(pad(n.get("os") or "", 8), C_DIM))
            if n.get("rtt_ms") is not None:
                r.append(seg(f"{n['rtt_ms']:>4}ms", C_DIM, "d"))
            r.append(seg(f"  {n.get('ip', '')}", C_DIM, "d"))
            if n.get("also_at"):
                r.append(seg(f" +{n['also_at']}", C_DIM, "d"))
        else:
            r.append(seg(pad("offline", 9), C_DIM, "d"))
            r.append(seg(f"  {n.get('ip', '')}", C_DIM, "d"))
        rows.append(r)
    if not fl.get("up"):
        rows.append([seg("  fleet powered down", C_DIM, "d")])
    if fl.get("polled_at"):
        rows.append([seg(f"  swept {age(time.time() - fl['polled_at'])} ago",
                         C_DIM, "d")])
    return title, rows


_SVC_STATE = {
    "active": "ok", "activating": "busy", "deactivating": "busy",
    "reloading": "busy", "absent": "absent", "unknown": "unknown",
    "inactive": "off", "failed": "fail",
}


def build_services(sv):
    lst = sv.get("services") or []
    title = f"Services  {sv.get('up', 0)}/{sv.get('total', 0)} up"
    if not lst:
        return title, [[seg("  no data", C_DIM, "d")]]
    rows = []
    for s in lst:
        st = _SVC_STATE.get(s.get("state"), "unknown")
        if st == "ok" and s.get("uptime_sec") is not None:
            detail = f"up {uptime(s['uptime_sec'])}"
        elif st == "absent":
            detail = "not installed"
        else:
            detail = s.get("sub") or s.get("result") or ""
        r = [mark(st),
             seg(" " + pad(s.get("label") or s.get("unit"), 15),
                 C_TITLE if st == "ok" else st_color(st)),
             seg(pad(s.get("state") or "—", 10), st_color(st)),
             seg(pad(detail, 14), C_DIM, "d"),
             seg(s.get("scope", ""), C_DIM, "d")]
        if s.get("restarts"):
            r.append(seg(f"  {s['restarts']}r", C_WARM))
        rows.append(r)
    if sv.get("degraded"):
        rows.append([seg(f"  {', '.join(sv['degraded'])} not running",
                         C_ALERT, "b")])
    return title, rows


def build_pxe(p):
    if not p:
        return None
    active = p.get("state") == "active"
    serving = bool(p.get("serving"))
    title = "PXE  " + ("serving" if serving else
                       ("no sockets" if active else (p.get("state") or "—")))
    # `active` but serving nothing is a warning: the unit looks healthy while
    # no machine can boot from it.
    st = "ok" if serving else ("warn" if active else "fail")
    r = [mark(st), seg(" " + pad(p.get("unit") or "retro-pxe", 12), C_TITLE),
         seg(pad(p.get("state") or "unknown", 10), st_color(st))]
    if p.get("uptime_sec") is not None:
        r.append(seg(f"up {uptime(p['uptime_sec'])}", C_DIM, "d"))
    if p.get("restarts"):
        r.append(seg(f"  {p['restarts']}r", C_WARM))
    rows = [r]
    ports = p.get("ports") or {}
    if ports:
        pr = [seg("  " + pad("ports", 12), C_DIM, "d")]
        for name, ok in ports.items():
            g = STATUS["ok" if ok else "fail"]
            pr.append(seg(f"{name} {g[0]}  ", g[1]))
        rows.append(pr)
    hc = p.get("hold_count") or 0
    rows.append([seg("  " + pad("boot holds", 12), C_DIM, "d"),
                 seg(f"{hc} machine{'' if hc == 1 else 's'} served",
                     C_TITLE if hc else C_DIM),
                 seg(" · will not reinstall", C_DIM, "d")])
    for h in (p.get("holds") or [])[:4]:
        rows.append([seg("  " + " " * 12, C_DIM),
                     seg(pad(h.get("mac", ""), 18), C_SPARK),
                     seg(f"{age(h.get('age_sec'))} ago", C_DIM, "d")])
    if p.get("last_activity_sec") is not None:
        clients = " ".join((p.get("recent_clients") or [])[:3])
        rows.append([seg("  " + pad("last seen", 12), C_DIM, "d"),
                     seg(f"{age(p['last_activity_sec'])} ago", C_TITLE),
                     seg(f"  {clients}", C_DIM, "d")])
    if p.get("last_offer"):
        rows.append([seg("  " + pad("last offer", 12), C_DIM, "d"),
                     seg(p["last_offer"], C_DIM)])
    if p.get("last_file"):
        rows.append([seg("  " + pad("last file", 12), C_DIM, "d"),
                     seg(p["last_file"][-48:], C_DIM, "d")])
    if p.get("files_served_recent"):
        rows.append([seg("  " + pad("served", 12), C_DIM, "d"),
                     seg(f"{p['files_served_recent']} files recently", C_DIM)])
    if p.get("last"):
        rows.append([seg("  " + p["last"][:72], C_DIM, "d")])
    return title, rows


def build_remote(r):
    if not r:
        return None
    rows = []
    crd = r.get("crd") or {}
    live = crd.get("state") == "connected"
    rows.append([mark("ok" if live else "off"),
                 seg(" " + pad("Chrome RD", 12), C_TITLE),
                 seg(pad(crd.get("state") or "off", 11),
                     C_ACTIVE if live else C_DIM),
                 seg(crd.get("user") or "", C_DIM, "d")])
    rdp = r.get("rdp") or {}
    live = rdp.get("state") == "connected"
    rows.append([mark("ok" if live else "off"),
                 seg(" " + pad("RDP", 12), C_TITLE),
                 seg(pad(rdp.get("state") or "off", 11),
                     C_ACTIVE if live else C_DIM),
                 seg(" ".join(rdp.get("peers") or []), C_DIM, "d")])
    con = r.get("console") or {}
    occ = bool(con.get("occupied"))
    rows.append([mark("ok" if occ else "off"),
                 seg(" " + pad("console", 12), C_TITLE),
                 seg(pad("logged in" if occ else "free", 11),
                     C_ACTIVE if occ else C_DIM),
                 seg(con.get("user") or "", C_DIM, "d")])
    sessions = r.get("sessions") or []
    if sessions:
        rows.append([seg("  sessions", C_TITLE, "b")])
        for s in sessions[:4]:
            rows.append([seg("  " + pad(f"#{s.get('id', '?')}", 5), C_DIM, "d"),
                         seg(pad(s.get("user") or "?", 10), C_TITLE),
                         seg(pad(s.get("type") or "", 9), C_DIM, "d"),
                         seg("remote" if s.get("remote")
                             else (s.get("seat") or "no seat"),
                             C_WARM if s.get("remote") else C_COOL)])
    return "Remote", rows


_AGENT_STATE = {
    "running": "ok", "alive": "ok", "ok": "ok", "starting": "busy",
    "stale": "stale", "unknown": "unknown", "dead": "fail", "failed": "fail",
}


def build_agents(a):
    if not a:
        return None
    rows = []
    d = a.get("daemon") or {}
    st = _AGENT_STATE.get(d.get("state"), "off")
    rows.append([mark(st), seg(" " + pad("chat daemon", 13), C_TITLE),
                 seg(pad(d.get("state") or "—", 9), st_color(st)),
                 seg(f"pid {d['pid']}" if d.get("pid") else "", C_DIM, "d"),
                 seg(f"  log {age(d['log_age_sec'])}"
                     if d.get("log_age_sec") is not None else "", C_DIM, "d")])
    b = a.get("brain") or {}
    st = _AGENT_STATE.get(b.get("state"), "off")
    rows.append([mark(st), seg(" " + pad("chat brain", 13), C_TITLE),
                 seg(pad(b.get("state") or "—", 9), st_color(st)),
                 seg(f"hb {age(b['age_sec'])}"
                     if b.get("age_sec") is not None else "", C_DIM, "d")])
    q = a.get("queue") or {}
    rows.append([seg("  " + pad("queue", 13), C_DIM, "d"),
                 seg(f"{q.get('inbox', 0)} in  ",
                     C_WARM if q.get("inbox") else C_DIM),
                 seg(f"{q.get('outbox', 0)} out  ",
                     C_WARM if q.get("outbox") else C_DIM),
                 seg(f"{q.get('failed', 0)} fail",
                     C_ALERT if q.get("failed") else C_DIM)])
    h = a.get("history") or {}
    if h.get("prompts") is not None:
        rows.append([seg("  " + pad("history", 13), C_DIM, "d"),
                     seg(f"{h.get('prompts', 0)} prompts · "
                         f"{h.get('hosts', 0)} hosts", C_DIM)])
    runs = a.get("runs") or []
    if runs:
        rows.append([seg("  current work", C_TITLE, "b")])
        for r in runs[:4]:
            prog = r.get("progress") or {}
            bits = []
            if prog.get("epoch") is not None:
                bits.append(f"e{prog['epoch']}")
            if prog.get("step") is not None:
                bits.append(f"s{prog['step']}")
            loss = (r.get("metrics") or {}).get("loss")
            if loss is not None:
                try:
                    bits.append(f"loss {float(loss):.3f}")
                except (TypeError, ValueError):
                    pass
            rows.append([seg("  " + pad(r.get("kind") or "?", 10), C_COOL),
                         seg(pad(r.get("model") or "", 12), C_DIM, "d"),
                         seg(" ".join(bits), C_WARM)])
    else:
        rows.append([seg("  no AI runs in flight", C_DIM, "d")])
    # The daemon's last log line is the best answer to "what is it doing"
    last = d.get("last") or b.get("last")
    if last:
        # Drop the log's date; the time of day is enough on a live panel
        if len(last) > 11 and last[:4].isdigit() and last[10] == " ":
            last = last[11:]
        rows.append([seg("  " + last[:72], C_DIM, "d")])
    return "Agents", rows


def build_favourites(f):
    if not f:
        return None
    if f.get("error"):
        fix = (f"waiting on {f['hint']}" if f.get("hint")
               else "systemctl --user start retro-gameindex")
        return "Favourites Agent", [
            [mark("warn"), seg(f" favourites agent {f['error']}", C_WARM)],
            [seg("  " + fix, C_DIM, "d")]]
    phase = f.get("phase")
    running = phase and phase not in ("idle", "failed")
    bad = f.get("ok") is False or phase == "failed" or f.get("stale_sec")
    # A pass in flight is busy, not failed, even before its first report.
    st = "busy" if running else ("fail" if bad else "ok")
    now = time.time()
    rows = [[mark(st), seg(" " + pad("server lists", 14), C_TITLE),
             seg(phase if running else ("failed" if bad else "idle"),
                 st_color(st))]]
    ago = f"{age(now - f['ts'])} ago" if f.get("ts") else "—"
    nxt = age(max(0, f["next_pass_at"] - now)) if f.get("next_pass_at") else "—"
    rows.append([seg("  " + pad("last pass", 13), C_DIM, "d"),
                 seg(ago, C_TITLE),
                 seg(f" ({f['duration_sec']}s)" if f.get("duration_sec")
                     else "", C_DIM, "d"),
                 seg(f" · next {nxt}", C_DIM, "d")])
    boxes = [str(ip).split(".")[-1] for ip in (f.get("agents") or [])]
    rows.append([seg("  " + pad("boxes", 13), C_DIM, "d"),
                 seg(f"{len(boxes)} reached", C_ACTIVE if boxes else C_DIM),
                 seg("  ." + " .".join(boxes) if boxes else "", C_DIM, "d")])
    w = f.get("writes") or {}
    r = [seg("  " + pad("favourites", 13), C_DIM, "d"),
         seg(f"{w.get('wrote', 0)} written", C_WARM if w.get("wrote") else C_DIM),
         seg(f"  {w.get('unchanged', 0)} same", C_DIM, "d")]
    if w.get("skipped"):
        r.append(seg(f"  {w['skipped']} skipped", C_DIM, "d"))
    if w.get("busy"):
        r.append(seg(f"  {w['busy']} busy", C_WARM))
    if w.get("failed"):
        r.append(seg(f"  {w['failed']} failed", C_ALERT, "b"))
    rows.append(r)
    fav = f.get("favorites") or {}
    rows.append([seg("  " + pad("live servers", 13), C_DIM, "d"),
                 seg(f"{f.get('servers_known', 0)} known", C_TITLE),
                 seg(f"  · {fav['files']} files on {fav.get('boxes', 0)} boxes"
                     if fav.get("files") else "", C_DIM, "d")])
    by_engine = f.get("servers_by_engine") or {}
    if by_engine:
        def _n(v):
            return (v.get("servers", 0) if isinstance(v, dict) else v) or 0
        bits = []
        for k, v in sorted(by_engine.items(), key=lambda kv: -_n(kv[1]))[:5]:
            plr = v.get("players") if isinstance(v, dict) else None
            bits.append(f"{k} {_n(v)}" + (f" ({plr} plr)" if plr else ""))
        txt = "  ".join(bits)
        rows.append([seg("  " + pad("by engine", 13), C_DIM, "d"),
                     seg(txt, C_DIM)])
    for err in (f.get("errors") or [])[:2]:
        rows.append([seg("  " + str(err)[:70], C_ALERT, "d")])
    if f.get("stale_sec") and f.get("ts"):
        rows.append([seg(f"  no pass for {age(f['stale_sec'])}", C_ALERT, "b")])
    return "Favourites Agent", rows


def build_watchdog(g):
    """The game-server watchdog's view: unit health, restarts, proxies.

    omenfan's own Game Servers panel queries the servers live; what only the
    watchdog knows is what systemd thinks of each unit and what it has done
    about the ones that died.
    """
    if not g:
        return None
    if g.get("error"):
        fix = (f"waiting on {g['hint']}" if g.get("hint")
               else "systemctl --user start retro-gameservers-watch")
        return "Server Watchdog", [
            [mark("warn"), seg(f" watchdog {g['error']}", C_WARM)],
            [seg("  " + fix, C_DIM, "d")]]
    servers = [s for s in (g.get("servers") or [])
               if s.get("installed") is not False]
    wd = g.get("watchdog") or {}
    humans, bots = g.get("humans") or 0, g.get("bots") or 0
    title = f"Server Watchdog  {g.get('up', 0)}/{g.get('total', 0)} up"
    if humans:
        title += f" · {humans} playing"
    elif bots:
        title += f" · {bots} bots"
    rows = []
    st = ("fail" if g.get("stale_sec") else
          ("ok" if wd.get("enabled", True) else "off"))
    r = [mark(st), seg(" " + pad("watchdog", 12), C_TITLE),
         seg(pad("armed" if wd.get("enabled", True) else "restarts off", 13),
             st_color(st))]
    if wd.get("interval_sec"):
        r.append(seg(f"every {int(wd['interval_sec'])}s", C_DIM, "d"))
    if wd.get("passes") is not None:
        r.append(seg(f" · {wd['passes']} passes", C_DIM, "d"))
    if wd.get("started_at"):
        r.append(seg(f" · up {uptime(time.time() - wd['started_at'])}",
                     C_DIM, "d"))
    rows.append(r)
    if g.get("stale_sec"):
        rows.append([seg(f"  watchdog silent {age(g['stale_sec'])}",
                         C_ALERT, "b")])

    down = [s for s in servers if not s.get("up")]
    for s in down:
        why = s.get("watchdog") or f"unit {s.get('unit_state', '?')}"
        rows.append([mark("fail"), seg(" " + pad(s.get("label"), 16), C_ALERT),
                     seg(pad("DOWN", 6), C_ALERT, "b"), seg(why[:40], C_WARM)])
    flappy = sorted((s for s in servers if s.get("restarts_this_hour")),
                    key=lambda s: -s["restarts_this_hour"])
    for s in flappy[:4]:
        rows.append([mark("warn"), seg(" " + pad(s.get("label"), 16), C_WARM),
                     seg(f"{s['restarts_this_hour']} restarts this hour", C_WARM)])
    slow = [s for s in servers if s.get("slow_start_sec")]
    for s in slow[:2]:
        rows.append([mark("busy"), seg(" " + pad(s.get("label"), 16), C_TITLE),
                     seg(f"slow start {age(s['slow_start_sec'])}", C_DIM)])
    if not down and not flappy:
        up_times = [s.get("uptime_sec") for s in servers
                    if s.get("uptime_sec") is not None]
        tail = f" · oldest up {uptime(max(up_times))}" if up_times else ""
        rows.append([seg(f"  all {len(servers)} units healthy{tail}",
                         C_DIM, "d")])

    proxies = [x for x in (g.get("proxies") or []) if x.get("installed")]
    if proxies:
        pr = [seg("  " + pad("proxies", 12), C_DIM, "d")]
        for x in proxies:
            gl = STATUS["ok" if x.get("up") else "fail"]
            pr.append(seg(f"{x.get('port')} {gl[0]}  ", gl[1]))
        rows.append(pr)

    acts = wd.get("actions") or []
    if acts:
        rows.append([seg("  recent actions", C_TITLE, "b")])
        now = time.time()
        for a in acts[:5]:
            ok = a.get("ok")
            rows.append([seg("  " + pad(f"{age(now - (a.get('ts') or 0))} ago",
                                        8), C_DIM, "d"),
                         seg(pad(a.get("unit"), 18), C_WARM if ok else C_ALERT),
                         seg(pad(a.get("detail") or
                                 ("restarted" if ok else "FAILED"), 11),
                             C_TITLE if ok else C_ALERT),
                         seg(a.get("reason") or "", C_DIM, "d")])
    return title, rows


def build_sites(site):
    """specpicks / aisleprompt via the reusable-agents framework."""
    if not site:
        return None
    ag = site.get("agents") or {}
    arts = site.get("articles") or {}
    dep = site.get("deploys") or {}
    if ag.get("error"):
        return "Web Sites  api down", [
            [mark("fail"), seg(" " + pad("framework API", 14), C_TITLE),
             seg(str(ag["error"])[:50], C_ALERT)]]
    rows, states = [], []
    names = list((ag.get("sites") or {}).keys()) or ["specpicks", "aisleprompt"]
    for name in names:
        a = (ag.get("sites") or {}).get(name) or {}
        c = a.get("counts") or {}
        art = (arts.get("sites") or {}).get(name) or {}
        st = worst((["fail"] if c.get("fail") else []) +
                   (["blocked"] if c.get("blocked") else []) +
                   (["busy"] if c.get("busy") else []) + ["ok"])
        states.append(st)
        r = [mark(st), seg(" " + pad(name, 12), C_TITLE),
             seg(f"{c.get('ok', 0)}/{a.get('total', 0)} agents", C_DIM)]
        if c.get("fail"):
            r.append(seg(f" · {c['fail']} failing", C_ALERT, "b"))
        if c.get("blocked"):
            r.append(seg(f" · {c['blocked']} blocked", C_NET_TX))
        if c.get("off"):
            r.append(seg(f" · {c['off']} off", C_DIM, "d"))
        if c.get("absent"):
            r.append(seg(f" · {c['absent']} never ran", C_DIM, "d"))
        rows.append(r)
        ast = ("ok" if art.get("state") == "ok" else
               "absent" if art.get("state") == "absent" else "fail")
        r = [seg("  "), mark(ast), seg(" " + pad("articles", 10), C_DIM)]
        if ast == "ok":
            r.append(seg(str(art.get("week", 0)), C_TITLE, "b"))
            r.append(seg(" published, 7d", C_DIM))
            # aisleprompt future-dates scheduled pieces; not "published"
            if art.get("scheduled"):
                r.append(seg(f"  +{art['scheduled']} scheduled", C_DIM, "d"))
        else:
            r.append(seg(art.get("why") or arts.get("error") or "no data",
                         st_color(ast)))
        rows.append(r)
        if a.get("failing"):
            rows.append([seg("    " + ", ".join(a["failing"]), C_ALERT, "d")])
    if dep.get("state") and dep.get("state") != "absent":
        wd = dep.get("window_days")
        win = "?"
        if isinstance(wd, (int, float)):
            win = f"{round(wd * 24)}h" if wd < 1 else f"{wd:.1f}d"
        # A floor over the run index's window, not a 7-day count
        rows.append([mark(dep["state"]), seg(" " + pad("deploys", 12), C_TITLE),
                     seg(f"{dep.get('ok', 0)} ok", C_TITLE),
                     seg(" / ", C_DIM),
                     seg(f"{dep.get('bad', 0)} failed",
                         C_ALERT if dep.get("bad") else C_DIM),
                     seg(f"  last {age(dep.get('last_age'))} ago, "
                         f"{win} window", C_DIM, "d")])
    fresh = "ok"
    if site.get("collected_at"):
        a = time.time() - site["collected_at"]
        fresh = "ok" if a < 300 else ("stale" if a < 1200 else "fail")
        asof = f"as of {age(a)} ago"
    else:
        asof = "no timestamp"
    w = worst(states + [fresh])
    return f"Web Sites  {STATUS[w][4]} · {asof}", rows


def build_all(hs):
    """Every panel with something to show, in wall order."""
    out = []
    for fn, key in ((build_fleet, "fleet"), (build_services, "services"),
                    (build_watchdog, "gameservers"), (build_pxe, "pxe"),
                    (build_favourites, "gameindex"), (build_agents, "agents"),
                    (build_remote, "remote"), (build_sites, "sites")):
        sec = hs.section(key)
        if not sec:
            continue
        try:
            p = fn(sec)
        except Exception as e:   # one malformed section never blanks the rest
            p = (key.title(), [[mark("unknown"),
                                seg(f" unreadable: {e}"[:60], C_DIM)]])
        if p:
            out.append(p)
    return out


def summary(hs):
    """One row per section, for the dashboard's compact Fleet Host panel."""
    rows = []
    fl = hs.section("fleet")
    if fl:
        up = fl.get("up", 0)
        rows.append([mark("ok" if up else "off"), seg(" " + pad("fleet", 11), C_TITLE),
                     seg(f"{up}/{fl.get('total', 0)} up",
                         C_ACTIVE if up else C_DIM),
                     seg("" if up else "  powered down", C_DIM, "d")])
    sv = hs.section("services")
    if sv:
        ok = sv.get("up", 0) == sv.get("total", 0)
        rows.append([mark("ok" if ok else "fail"),
                     seg(" " + pad("services", 11), C_TITLE),
                     seg(f"{sv.get('up', 0)}/{sv.get('total', 0)} up",
                         C_ACTIVE if ok else C_ALERT),
                     seg(f"  {', '.join(sv['degraded'])} down"[:40]
                         if sv.get("degraded") else "", C_ALERT)])
    g = hs.section("gameservers")
    if g and g.get("error"):
        rows.append([mark("warn"), seg(" " + pad("watchdog", 11), C_TITLE),
                     seg(str(g["error"])[:40], C_WARM)])
    elif g:
        wd = g.get("watchdog") or {}
        acts = wd.get("actions") or []
        st = "fail" if g.get("stale_sec") else (
            "ok" if g.get("up") == g.get("total") else "warn")
        tail = ""
        if acts:
            tail = f"  last restart {age(time.time() - (acts[0].get('ts') or 0))} ago"
        rows.append([mark(st), seg(" " + pad("watchdog", 11), C_TITLE),
                     seg(f"{g.get('up', 0)}/{g.get('total', 0)} units",
                         st_color(st)),
                     seg(tail, C_DIM, "d")])
    p = hs.section("pxe")
    if p:
        serving = bool(p.get("serving"))
        st = "ok" if serving else ("warn" if p.get("state") == "active" else "fail")
        rows.append([mark(st), seg(" " + pad("pxe", 11), C_TITLE),
                     seg("serving" if serving else (p.get("state") or "—"),
                         st_color(st)),
                     seg(f"  {p.get('hold_count', 0)} holds", C_DIM, "d"),
                     seg(f" · {age(p['last_activity_sec'])} ago"
                         if p.get("last_activity_sec") is not None else "",
                         C_DIM, "d")])
    f = hs.section("gameindex")
    if f:
        phase = f.get("phase")
        running = phase and phase not in ("idle", "failed")
        bad = f.get("error") or f.get("ok") is False or phase == "failed"
        st = "busy" if running else ("fail" if bad else "ok")
        rows.append([mark(st), seg(" " + pad("favourites", 11), C_TITLE),
                     seg(phase if running else ("failed" if bad else "idle"),
                         st_color(st)),
                     seg(f"  {f.get('servers_known', 0)} live servers",
                         C_DIM, "d")])
    a = hs.section("agents")
    if a:
        d = (a.get("daemon") or {}).get("state")
        b = (a.get("brain") or {}).get("state")
        st = worst([_AGENT_STATE.get(d, "off"), _AGENT_STATE.get(b, "off")])
        runs = len(a.get("runs") or [])
        rows.append([mark(st), seg(" " + pad("agents", 11), C_TITLE),
                     seg(f"daemon {d or '—'} · brain {b or '—'}", st_color(st)),
                     seg(f"  {runs} runs" if runs else "", C_WARM)])
    r = hs.section("remote")
    if r:
        who = []
        if (r.get("crd") or {}).get("state") == "connected":
            who.append("Chrome RD")
        if (r.get("rdp") or {}).get("state") == "connected":
            who.append("RDP")
        con = r.get("console") or {}
        rows.append([mark("ok" if who else "off"),
                     seg(" " + pad("remote", 11), C_TITLE),
                     seg(" + ".join(who) if who else "no remote session",
                         C_ACTIVE if who else C_DIM),
                     seg(f"  console {con.get('user') or 'free'}", C_DIM, "d")])
    return rows
