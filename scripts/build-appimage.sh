#!/usr/bin/env bash
# Package target/release/jumpkut as an x86_64 AppImage. Release builds run on
# Ubuntu 22.04, so the binary needs glibc 2.35 or newer.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
output_dir="$project/dist"
binary="$project/target/release/jumpkut"
while (($#)); do
    case "$1" in
        --output-dir) output_dir=${2:?--output-dir needs a directory}; shift 2 ;;
        --binary) binary=${2:?--binary needs a file}; shift 2 ;;
        --help|-h) echo "Usage: $0 [--output-dir DIRECTORY] [--binary FILE]"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
if [[ $(uname -m) != x86_64 ]]; then
    echo "The AppImage build currently supports x86_64 only." >&2
    exit 1
fi
for command in curl sha256sum desktop-file-validate; do
    if ! command -v "$command" >/dev/null; then
        echo "Missing build dependency: $command. See docs/packaging.md." >&2
        exit 1
    fi
done
if [[ ! -x $binary ]]; then
    echo "$binary not found; build it first with: cargo build --release --locked" >&2
    exit 1
fi
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/jumpkut-appimage-build.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
appdir="$work/Jumpkut.AppDir"
tools="$work/tools"
mkdir -p "$appdir" "$tools"

# Immutable releases, each verified before execution. No floating tools.
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
download appimagetool-x86_64.AppImage \
    https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage \
    ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
download runtime-x86_64 \
    https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64 \
    2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d
download runtime-LICENSE \
    https://raw.githubusercontent.com/AppImage/type2-runtime/dd6cebedcbddde9c82f89b011e8e1d40b6e43868/LICENSE \
    aa154fc9070614bbe7921f89db11efd1dba7a1f3a41685958110e2230f9c0ca1
# Extract the tool once; building never requires FUSE or root.
mkdir "$tools/appimagetool"
(cd "$tools/appimagetool" && ../appimagetool-x86_64.AppImage --appimage-extract >/dev/null)

mkdir -p "$appdir/usr/bin" "$appdir/usr/share/applications" "$appdir/usr/share/icons/hicolor/scalable/apps" \
    "$appdir/usr/share/doc/jumpkut"
install -m 755 "$binary" "$appdir/usr/bin/jumpkut"
install -m 755 "$project/packaging/AppRun" "$appdir/AppRun"
install -m 644 "$project/packaging/jumpkut.desktop" "$appdir/usr/share/applications/jumpkut.desktop"
install -m 644 "$project/assets/jumpkut.svg" "$appdir/usr/share/icons/hicolor/scalable/apps/jumpkut.svg"
desktop-file-validate "$appdir/usr/share/applications/jumpkut.desktop"
ln -s usr/share/applications/jumpkut.desktop "$appdir/jumpkut.desktop"
ln -s usr/share/icons/hicolor/scalable/apps/jumpkut.svg "$appdir/jumpkut.svg"
ln -s jumpkut.svg "$appdir/.DirIcon"
install -m 644 "$project/LICENSE" "$appdir/usr/share/doc/jumpkut/copyright"
install -m 644 "$tools/runtime-LICENSE" "$appdir/usr/share/doc/jumpkut/AppImage-runtime-LICENSE"

version=$(sed -n '/^\[package\]/,/^\[/s/^version *= *"\([^"]*\)".*/\1/p' "$project/Cargo.toml" | head -1)
artifact="$output_dir/Jumpkut-$version-x86_64.AppImage"
ARCH=x86_64 VERSION="$version" "$tools/appimagetool/squashfs-root/AppRun" --no-appstream \
    --runtime-file "$tools/runtime-x86_64" --mksquashfs-opt -processors --mksquashfs-opt 2 \
    "$appdir" "$artifact"
chmod +x "$artifact"
APPIMAGE_EXTRACT_AND_RUN=1 "$artifact" --version
printf '%s\n' "$artifact"
