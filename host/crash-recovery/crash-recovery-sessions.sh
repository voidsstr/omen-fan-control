#!/usr/bin/env bash
# Reopens the Claude sessions listed in ~/.config/crash-recovery/sessions.conf as
# ptyxis tabs and tells each to continue. Run at login by
# ~/.config/autostart/crash-recovery-sessions.desktop (with GDM autologin enabled).
#   --dry-run    print the commands only
#   --no-prompt  resume without sending the continue prompt
set -uo pipefail
conf="$HOME/.config/crash-recovery/sessions.conf"
log="$HOME/.local/state/crash-recovery-sessions.log"
dry=0; send_prompt=1
for a in "$@"; do case "$a" in --dry-run) dry=1 ;; --no-prompt) send_prompt=0 ;; esac; done
[ $dry = 1 ] || exec >>"$log" 2>&1
echo "=== $(date '+%F %T') boot $(cat /proc/sys/kernel/random/boot_id)"

prompt="The host (OMEN, .132) just rebooted or crashed and this session was resumed automatically. Before redoing anything, check the actual state of whatever you were in the middle of (processes, files, remote boxes), then continue the task where you left off."

# Crash-loop guard: 4+ boots in the last hour (kdump capture boots count) means
# something is killing the box repeatedly -- open the tabs but do not auto-continue.
recent=$(journalctl --list-boots --no-pager -o json 2>/dev/null | python3 -c '
import json,sys,time
b=json.load(sys.stdin); now=time.time()*1e6
print(sum(1 for x in b if now-x.get("first_entry",0) < 3600e6))' 2>/dev/null || echo 0)
if [ "${recent:-0}" -ge 4 ]; then
  echo "crash-loop guard: $recent boots in the last hour -- resuming without the continue prompt"
  send_prompt=0
fi

ptyxis_windows() {
  gdbus introspect --session --dest org.gnome.Ptyxis --object-path /org/gnome/Ptyxis/window -r 2>/dev/null \
    | grep -c 'node /org/gnome/Ptyxis/window/'
}
# Ptyxis's own session restore reopens the last saved window (a plain shell tab) when the
# launcher starts it, and that window competes with ours for the --tab calls (2026-11-10).
# This launcher is the restore mechanism, so switch Ptyxis's off.
if [ $dry = 0 ] && [ "$(gsettings get org.gnome.Ptyxis restore-session 2>/dev/null)" = true ]; then
  gsettings set org.gnome.Ptyxis restore-session false && echo "ptyxis restore-session -> false"
fi

first=1
while IFS='|' read -r sid cwd title; do
  sid=$(echo "$sid" | xargs); cwd=$(echo "$cwd" | xargs); title=$(echo "$title" | xargs)
  [ -z "$sid" ] || [ "${sid:0:1}" = "#" ] && continue
  if [ $dry = 0 ] && pgrep -f "^claude --resume $sid" >/dev/null; then echo "skip (running): $sid"; continue; fi
  [ -d "$cwd" ] || { echo "skip (missing cwd $cwd): $sid"; continue; }
  # A hard power-off can leave a half-written last line in the transcript; back up and trim it.
  jf=$(find "$HOME/.claude/projects" -maxdepth 2 -name "$sid.jsonl" | head -1)
  [ -n "$jf" ] || { echo "WARN no transcript for $sid -- resume will open the picker"; }
  if [ -n "$jf" ] && [ $dry = 0 ]; then
    python3 - "$jf" <<'PY'
import json, os, sys, time
p = sys.argv[1]
size = os.path.getsize(p)
with open(p, 'rb') as f:  # transcripts reach 200+ MB; only the tail matters
    f.seek(max(0, size - 4 * 1024 * 1024)); tail = f.read()
body = tail.rstrip(b'\n')
last = body.rsplit(b'\n', 1)[-1] if body else b''
try:
    if last: json.loads(last)
except ValueError:
    cut = size - (len(tail) - (len(body) - len(last)))
    open(p + '.precrash-' + time.strftime('%Y%m%d%H%M%S'), 'wb').write(tail[len(tail) - (size - cut):])
    os.truncate(p, cut)
    print(f'repaired truncated last line ({len(last)} bytes) in {p}')
PY
  fi
  inner="CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 claude --resume $sid --dangerously-skip-permissions"
  [ $send_prompt = 1 ] && inner+=" $(printf '%q' "$prompt")"
  inner+='; exec ${SHELL:-bash}'
  # The first session gets a fresh window; the rest go in as tabs. ptyxis --tab targets the
  # *active* window, so wait until each tab's claude is actually running before adding the
  # next -- under boot load a window can take a minute to appear, and firing blind scattered
  # the tabs across windows (2026-09-29).
  mode=--tab; [ $first = 1 ] && mode=--new-window
  cmd=(ptyxis "$mode" -T "claude: $title" -d "$cwd" -- bash -lc "$inner")
  if [ $dry = 1 ]; then printf '%q ' "${cmd[@]}"; echo; else
    echo "open ($mode): $sid ($title)"; setsid "${cmd[@]}" >/dev/null 2>&1 < /dev/null &
    # Anchor on ^claude: the unanchored pattern also matched this ptyxis launcher's own
    # command line, so the wait passed before the window existed and the --tab calls
    # built a second window (2026-09-29 13:19 boot).
    for i in $(seq 180); do pgrep -f "^claude --resume $sid" >/dev/null && break; sleep 1; done
    pgrep -f "^claude --resume $sid" >/dev/null && echo "  running after ${i}s" || echo "  WARN not running after 180s"
    if [ $first = 1 ]; then
      # A running claude doesn't mean the window has focus yet; --tab with no active window
      # makes a new one (2026-11-10 boot ended up with 2 windows). Wait for the window
      # object, then give the shell a moment to focus it.
      for j in $(seq 60); do [ "$(ptyxis_windows)" -ge 1 ] && break; sleep 1; done
      sleep 8
    else
      sleep 3
    fi
  fi
  first=0
done < "$conf"
[ $dry = 0 ] && { w=$(ptyxis_windows); echo "ptyxis windows: $w"; [ "$w" -gt 1 ] && echo "  WARN tabs split across $w windows"; }
