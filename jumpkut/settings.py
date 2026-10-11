"""Validated preferences and XDG desktop autostart integration."""

from __future__ import annotations

import configparser
import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    hotkey: str = "<Alt>c"
    history_limit: int = 30  # Remembered custom length for the quick picker.
    sticky: bool = False
    auto_paste: bool = True
    persist: bool = True
    tray_limit: int = 10
    custom_quick_limit: bool = False
    custom_tray_limit: bool = False
    resume_last_selection: bool = True

    @property
    def quick_history_limit(self) -> int:
        return self.history_limit if self.custom_quick_limit else 30

    @property
    def tray_history_limit(self) -> int:
        return self.tray_limit if self.custom_tray_limit else 10


def _xdg_root(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable)
    # The XDG specification requires absolute paths. Empty or relative values
    # fall back to the standard location instead of following the current cwd.
    if value and Path(value).is_absolute():
        return Path(value)
    return Path.home() / fallback


def _config_root() -> Path:
    return _xdg_root("XDG_CONFIG_HOME", ".config")


def data_path() -> Path:
    return _xdg_root("XDG_DATA_HOME", ".local/share") / "jumpkut/history.json"


def log_path() -> Path:
    return _xdg_root("XDG_STATE_HOME", ".local/state") / "jumpkut/jumpkut.log"


def autostart_path() -> Path:
    return _config_root() / "autostart/jumpkut.desktop"


def portable_launcher() -> Path | None:
    """The persistent AppImage (or extracted AppRun), supplied by AppRun."""
    value = os.environ.get("JUMPKUT_LAUNCHER")
    return Path(value) if value and Path(value).is_absolute() else None


def _validate(settings: Settings) -> None:
    if not isinstance(settings, Settings):
        raise ValueError("Preferences must be a Settings value")
    if not isinstance(settings.hotkey, str) or not settings.hotkey.strip():
        raise ValueError("The keyboard shortcut cannot be empty")
    if (
        type(settings.history_limit) is not int
        or not 1 <= settings.history_limit <= 10000
    ):
        raise ValueError("Quick history length must be an integer between 1 and 10000")
    if type(settings.tray_limit) is not int or not 1 <= settings.tray_limit <= 10000:
        raise ValueError("Tray history length must be an integer between 1 and 10000")
    for name in ("sticky", "auto_paste", "persist", "custom_quick_limit", "custom_tray_limit", "resume_last_selection"):
        if type(getattr(settings, name)) is not bool:
            raise ValueError(f"{name} must be true or false")


def _atomic_write(path: Path, content: str, *, private_directory: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if private_directory:
        path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Config:
    """Load defaults on invalid data without modifying the original file."""

    def __init__(self, path: Path | None = None):
        self.path = (
            Path(path) if path is not None else _config_root() / "jumpkut/config.json"
        )
        self.settings = Settings()
        self.load_error: str | None = None
        self._load()

    def _load(self) -> None:
        try:
            content = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except (OSError, UnicodeError) as error:
            self.load_error = f"Could not read preferences: {error}"
            return
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("Preferences must be a JSON object")
            unknown = data.keys() - asdict(Settings()).keys()
            if unknown:
                raise ValueError(
                    f"Unrecognized preference: {', '.join(sorted(unknown))}"
                )
            settings = Settings(**data)
            _validate(settings)
            if "custom_quick_limit" not in data and "history_limit" in data:
                # Legacy settings used 200 as the default for both menus. Use
                # the new default, keeping its checkbox off for a matching 30.
                # Preserve previously chosen lengths that differ from it.
                # Validate first so migration cannot disguise an invalid value.
                settings = (
                    replace(settings, history_limit=30)
                    if settings.history_limit in (200, 30)
                    else replace(settings, custom_quick_limit=True)
                )
        except (ValueError, TypeError) as error:
            self.load_error = f"Invalid preferences: {error}; original file left unchanged"
            return
        self.settings = settings

    def save(self, settings: Settings) -> None:
        _validate(settings)
        content = json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n"
        _atomic_write(self.path, content, private_directory=True)
        self.settings = settings
        self.load_error = None


def _quote_exec_argument(argument: str) -> str:
    """Quote one Exec argument through both desktop-entry escaping layers.

    Exec values are not shell commands. First quote according to the Exec
    grammar, then escape backslashes for the desktop entry's string parser.
    Percent signs must be doubled so filenames cannot introduce field codes.
    """
    argument = argument.replace("%", "%%")
    escaped = "".join(
        "\\" + character if character in '\\"`$' else character
        for character in argument
    )
    escaped = (
        escaped.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def autostart_enabled() -> bool:
    path = autostart_path()
    if not path.is_file():
        return False
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with path.open(encoding="utf-8") as source:
            parser.read_file(source)
        return parser.has_section("Desktop Entry") and not parser.getboolean(
            "Desktop Entry", "Hidden", fallback=False
        )
    except (OSError, UnicodeError, ValueError, configparser.Error):
        return False


def set_autostart(enabled: bool) -> None:
    if type(enabled) is not bool:
        raise ValueError("Autostart must be true or false")
    path = autostart_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    launcher = portable_launcher()
    run_path = Path(__file__).resolve().parents[1] / "run.py"
    extraction = "APPIMAGE_EXTRACT_AND_RUN=1 " if os.environ.get("APPIMAGE_EXTRACT_AND_RUN") == "1" else ""
    command = (
        f"/usr/bin/env {extraction}{_quote_exec_argument(str(launcher))}"
        if launcher else f"/usr/bin/python3 {_quote_exec_argument(str(run_path))}"
    )
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Jumpkut\n"
        "Comment=Clipboard history and quick selection\n"
        f"Exec={command} --daemon\n"
        "Icon=edit-paste\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    _atomic_write(path, content, private_directory=False)
