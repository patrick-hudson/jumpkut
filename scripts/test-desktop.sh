#!/bin/sh
# Run native tests on a disposable X11 display and session bus.
set -eu

if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [path-to-Xvfb]\n' "$0" >&2
    exit 2
fi
xvfb=${1:-Xvfb}
if ! command -v "$xvfb" >/dev/null 2>&1; then
    printf 'Xvfb is required. Supply its path or install the xvfb package.\n' >&2
    exit 1
fi
for dependency in metacity dbus-run-session /usr/bin/python3; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
        printf 'Missing desktop-test dependency: %s\n' "$dependency" >&2
        exit 1
    fi
done

project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
temporary=$(mktemp -d -t jumpkut-desktop.XXXXXX)
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
        printf 'Could not start the disposable X11 display.\n' >&2
        tail -n 40 "$temporary/xvfb.log" >&2
        exit 1
    fi
    sleep 0.05
done

DISPLAY=":$(cat "$temporary/display")"
export DISPLAY
export XDG_SESSION_TYPE=x11 GDK_BACKEND=x11 GTK_USE_PORTAL=0
export GSETTINGS_BACKEND=memory NO_AT_BRIDGE=1 JUMPKUT_DESKTOP_TEST=1
unset WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS

# No service directories: the tests cannot activate desktop services from the
# user's login session. dbus-run-session provides and tears down this private bus.
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

dbus-run-session --config-file="$temporary/session.conf" -- /bin/sh -s -- "$temporary" "$project" <<'SESSION'
set -eu
temporary=$1
project=$2
metacity --replace --sm-disable >"$temporary/metacity.log" 2>&1 &
window_manager=$!
cleanup_window_manager() {
    kill "$window_manager" 2>/dev/null || true
    wait "$window_manager" 2>/dev/null || true
}
trap cleanup_window_manager 0
trap 'exit 130' INT
trap 'exit 143' TERM

if ! /usr/bin/python3 - <<'PYTHON'
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
then
    tail -n 40 "$temporary/metacity.log" >&2
    exit 1
fi

cd -- "$project"
/usr/bin/python3 -m unittest discover -s tests -p test_desktop.py -v
/usr/bin/python3 -m unittest discover -s tests -p test_background.py -v
SESSION
