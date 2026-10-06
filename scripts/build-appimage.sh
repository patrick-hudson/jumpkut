#!/usr/bin/env bash
# Bundle Python/GTK on Ubuntu 22.04 to keep the release glibc baseline at 2.35.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
output_dir="$project/dist"
while (($#)); do
    case "$1" in
        --output-dir) output_dir=${2:?--output-dir needs a directory}; shift 2 ;;
        --help|-h) echo "Usage: $0 [--output-dir DIRECTORY]"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
if [[ $(uname -m) != x86_64 ]]; then
    echo "The AppImage build currently supports x86_64 only." >&2
    exit 1
fi
for command in curl sha256sum file pkg-config dpkg-query dpkg-architecture patchelf desktop-file-validate; do
    if ! command -v "$command" >/dev/null; then
        echo "Missing build dependency: $command. See docs/packaging.md." >&2
        exit 1
    fi
done
pkg-config --exists gtk+-3.0 librsvg-2.0 gobject-introspection-1.0
/usr/bin/python3 -c 'import gi, Xlib'
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/jumpkut-appimage-build.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
appdir="$work/Jumpkut.AppDir"
tools="$work/tools"
mkdir -p "$appdir" "$tools"

# Immutable releases/commit, each verified before execution. No floating tools.
download() {
    local name=$1 url=$2 digest=$3
    if [[ -n ${JUMPKUT_APPIMAGE_TOOL_CACHE:-} && -f $JUMPKUT_APPIMAGE_TOOL_CACHE/$name ]]; then
        cp "$JUMPKUT_APPIMAGE_TOOL_CACHE/$name" "$tools/$name"
    else
        curl --fail --silent --show-error --location --retry 3 "$url" -o "$tools/$name"
    fi
    printf '%s  %s\n' "$digest" "$tools/$name" | sha256sum --check --status
    chmod +x "$tools/$name"
}
download linuxdeploy-x86_64.AppImage \
    https://github.com/linuxdeploy/linuxdeploy/releases/download/1-alpha-20251107-1/linuxdeploy-x86_64.AppImage \
    c20cd71e3a4e3b80c3483cef793cda3f4e990aca14014d23c544ca3ce1270b4d
download linuxdeploy-plugin-gtk.sh \
    https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/7a3fbc31a9e5075073ff8790f26effbac5f84453/linuxdeploy-plugin-gtk.sh \
    b0f4cbc684a0103a9651f0955b635eaea0096b3a66c0f5a2c2aa337960375171
download appimagetool-x86_64.AppImage \
    https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage \
    ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
download runtime-x86_64 \
    https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64 \
    2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d
download runtime-LICENSE \
    https://raw.githubusercontent.com/AppImage/type2-runtime/dd6cebedcbddde9c82f89b011e8e1d40b6e43868/LICENSE \
    aa154fc9070614bbe7921f89db11efd1dba7a1f3a41685958110e2230f9c0ca1

# Extract build tools once; building itself never requires FUSE or root.
mkdir "$tools/linuxdeploy" "$tools/appimagetool"
(cd "$tools/linuxdeploy" && ../linuxdeploy-x86_64.AppImage --appimage-extract >/dev/null)
(cd "$tools/appimagetool" && ../appimagetool-x86_64.AppImage --appimage-extract >/dev/null)
linuxdeploy="$tools/linuxdeploy/squashfs-root/usr/bin/linuxdeploy"
cp "$tools/linuxdeploy-plugin-gtk.sh" "$(dirname "$linuxdeploy")/linuxdeploy-plugin-gtk.sh"

mkdir -p "$appdir/usr/bin" "$appdir/usr/lib/python3/dist-packages" "$appdir/usr/share/jumpkut" \
    "$appdir/usr/share/applications" "$appdir/usr/share/icons/hicolor/scalable/apps"
cp -L /usr/bin/python3 "$appdir/usr/bin/python3"
cat > "$appdir/usr/bin/jumpkut" <<'LAUNCHER'
#!/bin/sh
exec "$(dirname "$(readlink -f -- "$0")")/../../AppRun" "$@"
LAUNCHER
chmod +x "$appdir/usr/bin/jumpkut"
# Copy the complete standard library and only Jumpkut's Python dependencies.
# Ignore caches and dereference distro symlinks so they cannot escape the image.
/usr/bin/python3 "$project/packaging/appimage-support.py" copy "$project" "$appdir"
cp "$project/packaging/jumpkut.desktop" "$appdir/usr/share/applications/jumpkut.desktop"
cp "$project/jumpkut/assets/jumpkut.svg" "$appdir/usr/share/icons/hicolor/scalable/apps/jumpkut.svg"
desktop-file-validate "$appdir/usr/share/applications/jumpkut.desktop"

# PyGI loads GTK through typelibs, so include GTK/GDK explicitly as well as all
# extension-module ELF dependencies. The GTK plugin adds typelibs, modules,
# schemas, SVG loaders and their dependencies.
gtk_libdir=$(pkg-config --variable=libdir gtk+-3.0)
gdk_libdir=$(pkg-config --variable=libdir gdk-pixbuf-2.0)
gdk_query="$gdk_libdir/gdk-pixbuf-2.0/gdk-pixbuf-query-loaders"
gtk_query=""
for candidate in "$gtk_libdir/libgtk-3-0/gtk-query-immodules-3.0" "$gtk_libdir/libgtk-3-0t64/gtk-query-immodules-3.0" "$gtk_libdir/gtk-3.0/gtk-query-immodules-3.0"; do
    if [[ -x $candidate ]]; then gtk_query=$candidate; break; fi
done
if [[ ! -x $gdk_query || -z $gtk_query ]]; then
    echo "Missing GTK loader tools. Install libgtk-3-bin and libgdk-pixbuf2.0-bin." >&2
    exit 1
fi
cp -L "$gdk_query" "$appdir/usr/bin/gdk-pixbuf-query-loaders"
cp -L "$gtk_query" "$appdir/usr/bin/gtk-query-immodules-3.0"
# linuxdeploy normally excludes these desktop libraries. Bundle them explicitly
# so the image does not borrow an older host GLib/font stack. Keep glibc and the
# graphics driver's GL/EGL libraries provided by the host.
runtime_libraries=()
for library in libglib-2.0.so.0 libharfbuzz.so.0 libfontconfig.so.1 libfreetype.so.6 \
    libfribidi.so.0 libstdc++.so.6 libgcc_s.so.1 libz.so.1 libexpat.so.1 \
    libX11.so.6 libxcb.so.1 libXext.so.6 libXrender.so.1; do
    runtime_libraries+=(--library "$gtk_libdir/$library")
done
DEPLOY_GTK_VERSION=3 "$linuxdeploy" --appdir "$appdir" \
    --executable "$appdir/usr/bin/python3" \
    --library "$gtk_libdir/libgtk-3.so.0" --library "$gtk_libdir/libgdk-3.so.0" \
    "${runtime_libraries[@]}" \
    --desktop-file "$appdir/usr/share/applications/jumpkut.desktop" --custom-apprun "$project/packaging/AppRun" \
    --icon-file "$appdir/usr/share/icons/hicolor/scalable/apps/jumpkut.svg" --plugin gtk
# Some PyGI/Debian installations place GdkX11 alongside Gtk, not in the
# gobject-introspection pkg-config directory. Require it explicitly.
test -f "$appdir/usr/lib/girepository-1.0/GdkX11-3.0.typelib"
test -f "$appdir/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader-svg.so"
rm -f "$appdir/AppRun"
cp "$project/packaging/AppRun" "$appdir/AppRun"
chmod +x "$appdir/AppRun"
rm -rf "$appdir/apprun-hooks"
/usr/bin/python3 "$project/packaging/appimage-support.py" licenses "$project" "$appdir"
cp "$tools/runtime-LICENSE" "$appdir/usr/share/doc/jumpkut/third-party/AppImage-runtime-LICENSE"

version=$(cd "$project" && /usr/bin/python3 -c 'from jumpkut import __version__; print(__version__)')
artifact="$output_dir/Jumpkut-$version-x86_64.AppImage"
# Limit mksquashfs parallelism on machines/containers with large host CPU counts.
ARCH=x86_64 VERSION="$version" "$tools/appimagetool/squashfs-root/AppRun" \
    --runtime-file "$tools/runtime-x86_64" --mksquashfs-opt -processors --mksquashfs-opt 2 \
    "$appdir" "$artifact"
chmod +x "$artifact"
APPIMAGE_EXTRACT_AND_RUN=1 "$artifact" --version
printf '%s\n' "$artifact"
