#!/usr/bin/env bash
# Start a built Jumpkut on a private Xvfb display and D-Bus session with
# temporary XDG directories, check single-instance forwarding and a clean
# quit, and that a seeded history.json survives byte for byte.
# Usage: scripts/smoke-test.sh [LAUNCHER]   (default: target/release/jumpkut)
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
launcher=$(readlink -f -- "${1:-$project/target/release/jumpkut}")
for command in Xvfb dbus-run-session; do
    command -v "$command" >/dev/null || { echo "Missing: $command" >&2; exit 1; }
done
work=$(mktemp -d "${TMPDIR:-/tmp}/jumpkut-smoke.XXXXXXXX")
trap 'kill "$xvfb" 2>/dev/null || true; rm -rf -- "$work"' EXIT
mkdir -p "$work"/{config,data/jumpkut,state,cache,runtime}
chmod 700 "$work/runtime"
cp "$project/tests/fixtures/history.json" "$work/data/jumpkut/history.json"
display=:$((90 + RANDOM % 100))
Xvfb "$display" -screen 0 1280x800x24 -nolisten tcp >"$work/xvfb.log" 2>&1 &
xvfb=$!
sleep 1
export JUMPKUT_BUS_NAME=io.github.jumpkut.SmokeTest
export DISPLAY=$display XDG_SESSION_TYPE=x11 XDG_CONFIG_HOME=$work/config XDG_DATA_HOME=$work/data \
    XDG_STATE_HOME=$work/state XDG_CACHE_HOME=$work/cache XDG_RUNTIME_DIR=$work/runtime
unset WAYLAND_DISPLAY
dbus-run-session -- bash -euo pipefail -c '
    "$1" --version
    "$1" --daemon >"$2/app.log" 2>&1 &
    app=$!
    # Wait until the daemon owns its bus name, so the commands below are
    # forwarded to it instead of starting a second instance.
    owned=
    for _ in $(seq 100); do
        kill -0 "$app" 2>/dev/null || { cat "$2/app.log"; echo "Jumpkut exited during start-up" >&2; exit 1; }
        if dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus \
            org.freedesktop.DBus.NameHasOwner "string:$JUMPKUT_BUS_NAME" 2>/dev/null | grep -q "boolean true"; then
            owned=1; break
        fi
        sleep 0.1
    done
    [ -n "$owned" ] || { cat "$2/app.log"; echo "Jumpkut did not claim its D-Bus name within 10 s" >&2; exit 1; }
    "$1" --history          # forwarded to the running instance
    "$1" --daemon           # forwarded; does not start a second one
    sleep 1
    "$1" --quit
    for _ in $(seq 50); do kill -0 "$app" 2>/dev/null || break; sleep 0.1; done
    if kill -0 "$app" 2>/dev/null; then echo "Jumpkut did not quit" >&2; exit 1; fi
    wait "$app"
' smoke "$launcher" "$work"
cmp "$project/tests/fixtures/history.json" "$work/data/jumpkut/history.json"
echo "Smoke test passed: $launcher"
