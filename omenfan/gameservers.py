"""Live status polling for game servers hosted on this machine.

Each engine family needs its OWN query protocol — probing everything with
Quake's `getstatus` gives false "down" for Quake 2, UT and Tribes 2, which
ignore it entirely.  The protocols implemented here:

  a2s      GoldSrc/Source  A2S_INFO + A2S_PLAYER, with the anti-reflection
                           challenge dance modern HLDS requires
  q3       Quake III       `getstatus` -> infostring + `score ping "name"`
  q2       Quake 2         `status` (NOT `getstatus`)
  qw       QuakeWorld      `status` -> `userid frags time ping "name" ...`
  gamespy  UT99 / UT2004   `\\status\\` + `\\players\\` on game port + 1
  torque   Tribes 2        binary; only flag bit 0x02 returns plaintext info

Servers are discovered from the kernel's UDP socket table, so the panel only
appears on a machine that is actually hosting them, and each server is probed
at the address it really bound (the CS servers bind a specific LAN IP, not
0.0.0.0, so probing 127.0.0.1 would time out).
"""

import json
import os
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass, field


# ── Data model ───────────────────────────────────────────────────────

@dataclass
class Player:
    name: str = ""
    score: int = 0
    ping: int = -1        # -1 = protocol does not report ping
    time_s: float = -1.0  # -1 = protocol does not report connect time
    team: str = ""


@dataclass
class ServerStatus:
    up: bool = False
    name: str = ""            # Server hostname as advertised
    game: str = ""            # Human-readable game ("Counter-Strike")
    mod: str = ""             # Mod / gamedir ("cstrike", "ktx", "Classic")
    mode: str = ""            # Game mode ("DeathMatchPlus", "Capture the Flag")
    map: str = ""
    players: int = 0
    max_players: int = 0
    bots: int = 0
    version: str = ""
    player_list: list = field(default_factory=list)
    players_known: bool = True   # False when the engine reports no player list
    rtt_ms: float = 0.0
    error: str = ""
    last_ok: float = 0.0
    extra: str = ""           # Engine-specific tail (team scores, etc.)


@dataclass
class ServerDef:
    key: str                  # systemd unit name
    label: str                # Display label
    engine: str               # Query protocol key
    port: int                 # Query port
    host: str = "127.0.0.1"
    game_port: int = 0        # Port players connect to, when != query port
    proxy_port: int = 0       # A2S old-query proxy in front of it, if any
    proxy_up: bool = False


# The fleet this host runs (see retro-agent/scripts/game-servers/README.md).
# Keyed by the port the *query* goes to.  A port only becomes a row when
# something is actually listening on it and answers.
KNOWN_PORTS = [
    # (query_port, unit,                label,             engine, game_port, proxy_port)
    (27018, "cs16-server",        "CS 1.6",           "a2s",     0,    27015),
    (27019, "cs16-noblood",       "CS 1.6 No Blood",  "a2s",     0,    27016),
    (27017, "specialists-server", "The Specialists",  "a2s",     0,    0),
    (27961, "quake3-server",      "Quake III Arena",  "q3",      0,    0),
    (27960, "openarena-server",   "OpenArena",        "q3",      0,    0),
    (27910, "quake2-server",      "Quake 2",          "q2",      0,    0),
    (27502, "quakeworld-server",  "QuakeWorld",       "qw",      0,    0),
    ( 7798, "ut99-server",        "UT99",             "gamespy", 7797, 0),
    ( 7787, "ut2004-server",      "UT2004",           "gamespy", 7777, 0),
    (28000, "tribes2-server",     "Tribes 2",         "torque",  0,    0),
]

CONFIG_PATH = os.path.expanduser("~/.config/omenfan/gameservers.json")


# ── Discovery ────────────────────────────────────────────────────────

def _udp_listeners():
    """Return {port: bound_ip} for every UDP socket open on this host.

    Reads /proc/net/udp{,6} directly — no netstat/ss subprocess, and it gives
    us the bound address, which is what we must send the query to.
    """
    found = {}
    for path, v6 in (("/proc/net/udp", False), ("/proc/net/udp6", True)):
        try:
            with open(path) as f:
                lines = f.readlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 2:
                continue
            local = parts[1]
            if ":" not in local:
                continue
            addr_hex, port_hex = local.rsplit(":", 1)
            try:
                port = int(port_hex, 16)
            except ValueError:
                continue
            if port in found:
                continue
            found[port] = _decode_addr(addr_hex, v6)
    return found


def _decode_addr(addr_hex, v6):
    """Decode /proc/net/udp little-endian hex address to a probe target."""
    if v6:
        # We only ever probe over IPv4; a v6 socket here means dual-stack or
        # v6-only, and localhost is the right target either way.
        return "127.0.0.1"
    try:
        packed = struct.pack("<I", int(addr_hex, 16))
        ip = socket.inet_ntoa(packed)
    except (ValueError, struct.error):
        return "127.0.0.1"
    # 0.0.0.0 means "all interfaces" — talk to it over loopback.
    return "127.0.0.1" if ip == "0.0.0.0" else ip


def _load_config():
    """Optional user override: ~/.config/omenfan/gameservers.json.

    A list of {"port":, "label":, "engine":, "unit":, "host":, "game_port":}.
    Lets someone point omenfan at a fleet on non-standard ports or another box.
    """
    try:
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    defs = []
    for e in raw if isinstance(raw, list) else []:
        try:
            defs.append(ServerDef(
                key=str(e.get("unit", "")),
                label=str(e.get("label", "server")),
                engine=str(e.get("engine", "a2s")),
                port=int(e["port"]),
                host=str(e.get("host", "127.0.0.1")),
                game_port=int(e.get("game_port", 0)),
                proxy_port=int(e.get("proxy_port", 0)),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return defs or None


def discover_servers():
    """Build the server list from the config file, else from open UDP ports."""
    cfg = _load_config()
    if cfg:
        return cfg
    listeners = _udp_listeners()
    defs = []
    for port, unit, label, engine, game_port, proxy_port in KNOWN_PORTS:
        if port not in listeners:
            continue
        defs.append(ServerDef(key=unit, label=label, engine=engine, port=port,
                              host=listeners[port], game_port=game_port,
                              proxy_port=proxy_port))
    return defs


# ── Wire helpers ─────────────────────────────────────────────────────

def _ask(host, port, payload, timeout=1.2, bufsize=65535):
    """Send one UDP datagram, return (reply, rtt_ms) or (None, elapsed)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    t0 = time.monotonic()
    try:
        s.sendto(payload, (host, port))
        data = s.recv(bufsize)
        return data, (time.monotonic() - t0) * 1000.0
    except OSError:
        return None, (time.monotonic() - t0) * 1000.0
    finally:
        s.close()


def _infostring(text):
    """Parse a `\\key\\value\\key\\value` Quake/GameSpy infostring."""
    parts = text.split("\\")
    info = {}
    # A leading empty element is normal (string starts with a backslash).
    if parts and parts[0] == "":
        parts = parts[1:]
    for i in range(0, len(parts) - 1, 2):
        if parts[i] and parts[i] not in info:
            info[parts[i]] = parts[i + 1]
    return info


_QUOTED_PLAYER = re.compile(r'^\s*(-?\d+)\s+(-?\d+)\s+"([^"]*)"')
_QW_PLAYER = re.compile(r'^\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+"([^"]*)"')


def _cstr(buf, pos):
    """Read a NUL-terminated latin-1 string, return (text, next_pos)."""
    end = buf.find(b"\x00", pos)
    if end < 0:
        return buf[pos:].decode("latin-1", "replace"), len(buf)
    return buf[pos:end].decode("latin-1", "replace"), end + 1


# ── Per-engine queries ───────────────────────────────────────────────

def _query_a2s(sd):
    """GoldSrc/Source: A2S_INFO then A2S_PLAYER, both challenge-gated.

    Modern HLDS answers the first query with an 'A' challenge packet that must
    be echoed back — the same anti-reflection behaviour that makes old CS 1.6
    clients need the a2s proxy.
    """
    st = ServerStatus()
    req = b"\xff\xff\xff\xffTSource Engine Query\x00"
    r, rtt = _ask(sd.host, sd.port, req)
    if r and r[4:5] == b"A":
        r, rtt = _ask(sd.host, sd.port, req + r[5:9])
    if not r or len(r) < 6 or r[4:5] != b"I":
        st.error = "no response" if not r else "bad A2S_INFO"
        return st

    st.rtt_ms = rtt
    p = 6  # skip header (4) + 'I' (1) + protocol byte (1)
    st.name, p = _cstr(r, p)
    st.map, p = _cstr(r, p)
    st.mod, p = _cstr(r, p)      # gamedir: cstrike, ts
    st.game, p = _cstr(r, p)     # description: Counter-Strike
    if p + 7 <= len(r):
        p += 2                   # appid (int16)
        st.players = r[p]
        st.max_players = r[p + 1]
        st.bots = r[p + 2]
        p += 6                   # players, max, bots, type, env, visibility
        p += 1                   # VAC
        st.version, p = _cstr(r, p)
    st.up = True

    st.player_list = _query_a2s_players(sd)
    # A2S_PLAYER carries score + connect time but no ping — the count from
    # A2S_INFO stays authoritative if the player query is refused.
    if st.player_list:
        st.players = max(st.players, len(st.player_list))
    return st


def _query_a2s_players(sd):
    """A2S_PLAYER: {index, name, score int32, duration float32} per player."""
    req = b"\xff\xff\xff\xffU"
    r, _ = _ask(sd.host, sd.port, req + b"\xff\xff\xff\xff")
    if r and r[4:5] == b"A":
        r, _ = _ask(sd.host, sd.port, req + r[5:9])
    if not r or len(r) < 6 or r[4:5] != b"D":
        return []

    count = r[5]
    out = []
    p = 6
    for _ in range(count):
        if p >= len(r):
            break
        p += 1                                   # player index (unreliable)
        name, p = _cstr(r, p)
        if p + 8 > len(r):
            out.append(Player(name=name))
            break
        score = struct.unpack_from("<i", r, p)[0]
        dur = struct.unpack_from("<f", r, p + 4)[0]
        p += 8
        out.append(Player(name=name, score=score, time_s=dur))
    return out


def _query_quake(sd, request, name_key, map_key, mod_key, header_skip):
    """Shared Quake-family parse: infostring line, then player lines."""
    st = ServerStatus()
    r, rtt = _ask(sd.host, sd.port, request)
    if not r:
        st.error = "no response"
        return st

    st.rtt_ms = rtt
    text = r[4:].decode("latin-1", "replace")
    if header_skip and text.startswith(header_skip):
        text = text[len(header_skip):]
    lines = text.split("\n")
    info = _infostring(lines[0] if lines else "")

    st.up = True
    st.name = info.get(name_key, "")
    st.map = info.get(map_key, "")
    st.mod = info.get(mod_key, "")
    st.version = info.get("version", "")
    st.max_players = _int(info.get("sv_maxclients") or info.get("maxclients"))
    st.mode = _q3_gametype(info) if "g_gametype" in info else ""

    for line in lines[1:]:
        if not line.strip():
            continue
        m = _QUOTED_PLAYER.match(line)
        if m:
            st.player_list.append(Player(name=m.group(3),
                                         score=int(m.group(1)),
                                         ping=int(m.group(2))))
    st.players = len(st.player_list)
    # Quake 3 bots report ping 0; that is how the engine marks them.
    st.bots = sum(1 for p in st.player_list if p.ping == 0)
    return st


_Q3_GAMETYPES = {
    "0": "Free For All", "1": "Tournament", "2": "Single Player",
    "3": "Team Deathmatch", "4": "Capture the Flag", "5": "One Flag CTF",
    "6": "Overload", "7": "Harvester", "8": "Elimination",
    "9": "CTF Elimination", "10": "Last Man Standing",
    "11": "Double Domination", "12": "Domination",
}


def _q3_gametype(info):
    return _Q3_GAMETYPES.get(info.get("g_gametype", ""), "")


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _query_q3(sd):
    return _query_quake(sd, b"\xff\xff\xff\xffgetstatus\n",
                        "sv_hostname", "mapname", "gamename",
                        "statusResponse\n")


def _query_q2(sd):
    # Quake 2 answers `status`, not `getstatus`, and prefixes it with "print".
    return _query_quake(sd, b"\xff\xff\xff\xffstatus\n",
                        "hostname", "mapname", "gamename", "print\n")


def _query_qw(sd):
    """QuakeWorld (mvdsv): like Quake 2 but players carry userid + time."""
    st = ServerStatus()
    r, rtt = _ask(sd.host, sd.port, b"\xff\xff\xff\xffstatus\n")
    if not r:
        st.error = "no response"
        return st

    st.rtt_ms = rtt
    text = r[4:].decode("latin-1", "replace")
    if text.startswith("n"):
        text = text[1:]
    lines = text.split("\n")
    info = _infostring(lines[0] if lines else "")

    st.up = True
    st.name = info.get("hostname", "")
    st.map = info.get("map", "")
    st.mod = info.get("*gamedir", "")
    st.version = info.get("*version", "")
    st.max_players = _int(info.get("maxclients"))
    st.mode = info.get("status", "")

    for line in lines[1:]:
        if not line.strip():
            continue
        m = _QW_PLAYER.match(line)
        if m:
            # userid, frags, minutes connected, ping, name
            st.player_list.append(Player(name=m.group(5),
                                         score=int(m.group(2)),
                                         time_s=int(m.group(3)) * 60.0,
                                         ping=int(m.group(4))))
    st.players = len(st.player_list)
    return st


def _query_gamespy(sd):
    """UT99 / UT2004 GameSpy: \\status\\ for info, \\players\\ for the roster.

    Both live on the game port + 1 — querying the game port itself gets
    nothing back.
    """
    st = ServerStatus()
    r, rtt = _ask(sd.host, sd.port, b"\\status\\")
    if not r:
        st.error = "no response"
        return st

    st.rtt_ms = rtt
    info = _infostring(r.decode("latin-1", "replace"))
    st.up = True
    st.name = info.get("hostname", "")
    st.map = info.get("maptitle") or info.get("mapname", "")
    st.mod = info.get("gamename", "")
    st.mode = info.get("gametype", "")
    st.version = info.get("gamever", "")
    st.players = _int(info.get("numplayers"))
    st.max_players = _int(info.get("maxplayers"))

    pr, _ = _ask(sd.host, sd.port, b"\\players\\")
    if pr:
        st.player_list = _parse_gamespy_players(pr.decode("latin-1", "replace"))
        if st.player_list:
            st.players = max(st.players, len(st.player_list))
    return st


def _parse_gamespy_players(text):
    """GameSpy roster: keys are suffixed with the player index (frags_0 ...)."""
    info = _infostring(text)
    by_idx = {}
    for key, val in info.items():
        if "_" not in key:
            continue
        field_name, _, idx = key.rpartition("_")
        if not idx.isdigit():
            continue
        by_idx.setdefault(int(idx), {})[field_name] = val

    out = []
    for idx in sorted(by_idx):
        rec = by_idx[idx]
        name = rec.get("player") or rec.get("playername")
        if name is None:
            continue
        out.append(Player(name=name,
                          score=_int(rec.get("frags"), 0),
                          ping=_int(rec.get("ping"), -1),
                          team=rec.get("team", "")))
    return out


def _query_torque(sd):
    """Tribes 2 (Torque): binary GameInfoRequest.

    Request type 0x12 with flag bit 0x02 set is the one that answers in
    plaintext — without that bit the server replies with an opaque 82-byte
    blob.  The reply carries counts but no roster, so per-player detail is
    genuinely unavailable for this engine.
    """
    st = ServerStatus()
    r, rtt = _ask(sd.host, sd.port, bytes([0x12, 0x02, 0, 0, 0, 0]), timeout=2.0)
    if not r or len(r) < 8 or r[0] != 0x14:
        st.error = "no response" if not r else "bad info response"
        return st

    st.rtt_ms = rtt
    st.up = True
    st.game = "Tribes 2"
    st.players_known = False
    p = 6  # type (1) + flags (1) + key (4)

    def pstr(pos):
        """Length-prefixed string."""
        if pos >= len(r):
            return "", pos
        n = r[pos]
        return r[pos + 1:pos + 1 + n].decode("latin-1", "replace"), pos + 1 + n

    st.mod, p = pstr(p)      # "Classic"
    st.mode, p = pstr(p)     # "Capture the Flag"
    st.map, p = pstr(p)      # "Raindance"
    if p + 5 <= len(r):
        p += 1               # status flags
        st.players = r[p]
        st.max_players = r[p + 1]
        st.bots = r[p + 2]
        p += 5               # players, max, bots, cpu speed (int16)
    st.name, p = pstr(p)
    if p + 2 <= len(r):
        n = struct.unpack_from("<H", r, p)[0]
        st.extra = r[p + 2:p + 2 + n].decode("latin-1", "replace").replace("\n", " ")
    return st


ENGINES = {
    "a2s": _query_a2s,
    "q3": _query_q3,
    "q2": _query_q2,
    "qw": _query_qw,
    "gamespy": _query_gamespy,
    "torque": _query_torque,
}


def query(sd):
    """Query one server, never raising."""
    fn = ENGINES.get(sd.engine)
    if fn is None:
        return ServerStatus(error="unknown engine %s" % sd.engine)
    try:
        st = fn(sd)
    except Exception as e:                       # a malformed packet must not
        return ServerStatus(error=str(e)[:40])   # take the TUI down
    if st.up:
        st.last_ok = time.time()
    return st


def _probe_proxy(sd):
    """Is the A2S old-query proxy in front of this server answering?"""
    if not sd.proxy_port:
        return False
    req = b"\xff\xff\xff\xffTSource Engine Query\x00"
    r, _ = _ask(sd.host, sd.proxy_port, req, timeout=1.0)
    if r and r[4:5] == b"A":
        r, _ = _ask(sd.host, sd.proxy_port, req + r[5:9], timeout=1.0)
    return bool(r and r[4:5] == b"I")


# ── Background monitor ───────────────────────────────────────────────

class GameServerMonitor:
    """Polls every discovered server on a background thread.

    The TUI must never block on a UDP timeout, so all network work happens
    here and the render path only ever reads the last snapshot.
    """

    def __init__(self, interval=5.0, rediscover_every=60.0):
        self.interval = interval
        self.rediscover_every = rediscover_every
        self._lock = threading.Lock()
        self._servers = []
        self._status = {}          # port -> ServerStatus
        self._seen = set()         # ports that have answered at least once
        self._thread = None
        self._stop = threading.Event()
        self.first_pass_done = False

    def start(self):
        """Begin polling. Safe to call when no servers are present."""
        self._servers = discover_servers()
        if not self._servers:
            self.first_pass_done = True
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    @property
    def active(self):
        with self._lock:
            return bool(self._seen)

    def snapshot(self):
        """Return [(ServerDef, ServerStatus)] for servers that have answered.

        A server that answered once but is silent now stays in the list as
        down — that is a report worth seeing, unlike a port we never reached.
        """
        with self._lock:
            return [(sd, self._status[sd.port]) for sd in self._servers
                    if sd.port in self._seen and sd.port in self._status]

    def totals(self):
        """(servers_up, servers_total, players, bots) across the fleet."""
        snap = self.snapshot()
        up = sum(1 for _, st in snap if st.up)
        players = sum(st.players for _, st in snap if st.up)
        bots = sum(st.bots for _, st in snap if st.up)
        return up, len(snap), players, bots

    def _loop(self):
        last_discover = time.time()
        while not self._stop.is_set():
            self._poll_once()
            self.first_pass_done = True
            if time.time() - last_discover >= self.rediscover_every:
                # A unit may have been started or stopped since we began.
                found = discover_servers()
                with self._lock:
                    known = {s.port for s in self._servers}
                    self._servers.extend(s for s in found
                                         if s.port not in known)
                last_discover = time.time()
            self._stop.wait(self.interval)

    def _poll_once(self):
        with self._lock:
            servers = list(self._servers)
        if not servers:
            return

        results = {}
        threads = []

        def run(sd):
            st = query(sd)
            if st.up and sd.proxy_port:
                sd.proxy_up = _probe_proxy(sd)
            results[sd.port] = st

        # One thread per server: ~10 short-lived threads, all blocked on a
        # sub-second UDP timeout, so a dead server costs latency not wall time.
        for sd in servers:
            t = threading.Thread(target=run, args=(sd,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=4.0)

        with self._lock:
            for port, st in results.items():
                if not st.up and port in self._status:
                    # Keep the last-known-good identity so a blip does not
                    # blank the row's name and map.
                    prev = self._status[port]
                    st.name = st.name or prev.name
                    st.map = st.map or prev.map
                    st.game = st.game or prev.game
                    st.mod = st.mod or prev.mod
                    st.last_ok = prev.last_ok
                self._status[port] = st
                if st.up:
                    self._seen.add(port)
