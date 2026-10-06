#!/bin/sh
# Capture the real GTK interface without accessing the login session.
set -eu
xvfb=${1:-Xvfb}
project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output=${2:-"$project/docs/share/screenshots"}
temporary=$(mktemp -d -t jumpkut-share.XXXXXX)
xvfb_pid=
cleanup() {
    if [ -n "$xvfb_pid" ]; then
        kill "$xvfb_pid" 2>/dev/null || true
        wait "$xvfb_pid" 2>/dev/null || true
    fi
    rm -rf -- "$temporary"
}
trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM

"$xvfb" -displayfd 3 -screen 0 1280x800x24 -nolisten tcp -ac \
    3>"$temporary/display" >"$temporary/xvfb.log" 2>&1 &
xvfb_pid=$!
attempt=0
while [ ! -s "$temporary/display" ]; do
    attempt=$((attempt + 1))
    if ! kill -0 "$xvfb_pid" 2>/dev/null || [ "$attempt" -ge 100 ]; then
        cat "$temporary/xvfb.log" >&2
        exit 1
    fi
    sleep 0.05
done

DISPLAY=":$(cat "$temporary/display")"
export DISPLAY
export XDG_SESSION_TYPE=x11 GDK_BACKEND=x11 GTK_USE_PORTAL=0
export GSETTINGS_BACKEND=memory NO_AT_BRIDGE=1 JUMPKUT_SHARE_CAPTURE=1
export XDG_CONFIG_HOME="$temporary/config" XDG_DATA_HOME="$temporary/data"
export XDG_STATE_HOME="$temporary/state" XDG_CACHE_HOME="$temporary/cache"
export XDG_RUNTIME_DIR="$temporary/runtime" TZ=America/Chicago
mkdir -m 700 "$XDG_RUNTIME_DIR"
unset WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS
cat >"$temporary/session.conf" <<'BUS'
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
BUS
dbus-run-session --config-file="$temporary/session.conf" -- /bin/sh -s -- "$temporary" "$project" "$output" <<'SESSION'
set -eu
temporary=$1
project=$2
output=$3
metacity --replace --sm-disable >"$temporary/metacity.log" 2>&1 &
window_manager=$!
cleanup_window_manager() {
    kill "$window_manager" 2>/dev/null || true
    wait "$window_manager" 2>/dev/null || true
}
trap cleanup_window_manager 0
trap 'exit 130' INT
trap 'exit 143' TERM
/usr/bin/python3 - <<'PYTHON'
import time
from Xlib import X, display
connection = display.Display()
atom = connection.intern_atom("_NET_SUPPORTING_WM_CHECK")
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    if connection.screen().root.get_full_property(atom, X.AnyPropertyType):
        connection.close()
        break
    time.sleep(0.05)
else:
    raise SystemExit("Metacity did not initialize on the disposable display")
PYTHON
cd -- "$project"
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 scripts/capture-share.py "$output"
SESSION
