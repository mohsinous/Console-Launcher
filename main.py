import sys
import os
import re
import json
import copy
import uuid
import shutil
import subprocess
import shlex
import ctypes
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QPixmap, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel, QPushButton, QLineEdit,
    QFileDialog, QMessageBox, QDialog, QFormLayout, QComboBox,
    QDialogButtonBox, QScrollArea, QFrame, QGridLayout, QCheckBox,
    QSizePolicy
)

APP_NAME = "Console Launcher"
IS_WINDOWS = sys.platform.startswith("win")


# ---------------------------------------------------------------------------
# Portable locations — everything lives next to the app.
# ---------------------------------------------------------------------------

def _app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
CONFIG_FILE = APP_DIR / "config.json"
ICONS_DIR = APP_DIR / "icons"
EMULATORS_DIR = APP_DIR / "emulators"


def _ensure_icons_dir():
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    return ICONS_DIR


def import_icon(source_path, emulator_name):
    src = Path(source_path)
    if not src.exists() or not src.is_file():
        return ""

    _ensure_icons_dir()

    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", emulator_name or "").strip("_")
    if not safe:
        safe = "icon"

    suffix = src.suffix.lower() or ".png"
    dest_name = f"{safe}_{uuid.uuid4().hex[:8]}{suffix}"
    dest = ICONS_DIR / dest_name

    try:
        shutil.copy2(src, dest)
        return dest_name
    except Exception:
        return ""


def resolve_icon(stored):
    if not stored:
        return None

    p = Path(stored)

    if p.is_absolute():
        return p if p.exists() else None

    candidate = ICONS_DIR / p
    return candidate if candidate.exists() else None


def delete_icon(stored):
    if not stored:
        return
    p = Path(stored)
    if p.is_absolute():
        return
    target = ICONS_DIR / p
    try:
        if target.exists():
            target.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Icon auto-matching by name
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".ico", ".bmp", ".gif"}


def _normalize(name):
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _icon_index():
    index = {}
    if not ICONS_DIR.exists():
        return index

    for entry in sorted(ICONS_DIR.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in _IMAGE_EXTS:
            continue

        key = _normalize(entry.stem)
        if key and key not in index:
            index[key] = entry

    return index


def find_icon_for_name(emulator_name, index=None):
    if not emulator_name:
        return ""

    if index is None:
        index = _icon_index()

    if not index:
        return ""

    def variants(n):
        base = _normalize(n)
        yield base
        stripped = re.sub(r"[0-9]+$", "", base)
        if stripped and stripped != base:
            yield stripped

    for key in variants(emulator_name):
        if key in index:
            return index[key].name

    for key in variants(emulator_name):
        if not key:
            continue

        candidates = []
        for stem_key, path in index.items():
            if stem_key.startswith(key) or key.startswith(stem_key):
                score = abs(len(stem_key) - len(key))
                candidates.append((score, path))

        if candidates:
            candidates.sort(key=lambda t: t[0])
            return candidates[0][1].name

    return ""


def autolink_icons(config):
    index = _icon_index()
    if not index:
        return False

    changed = False

    for emulator in config.get("emulators", []):
        if emulator.get("icon"):
            continue

        found = find_icon_for_name(
            emulator.get("name", ""),
            index
        )

        if found:
            emulator["icon"] = found
            changed = True

    return changed


# ---------------------------------------------------------------------------
# Emulator executable auto-discovery
# ---------------------------------------------------------------------------

_GENERIC_EXE_TOKENS = {
    "qt", "sdl", "sdl2", "sdl3", "windows", "win", "win64", "x64", "x86",
    "launcher", "app", "gui", "main", "release", "debug", "portable",
    "setup", "install", "uninstall", "update", "updater", "canary",
}


def _candidate_exes(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return

    roots = [folder]
    for child in folder.iterdir():
        if child.is_dir():
            roots.append(child)

    seen = set()

    for root in roots:
        try:
            for entry in root.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix.lower() != ".exe":
                    continue

                low = entry.name.lower()
                if any(bad in low for bad in (
                    "unins", "install", "updat", "setup",
                    "crashhandler", "crashreport", "vcredist",
                )):
                    continue

                key = str(entry.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)

                yield entry
        except Exception:
            continue


def _score_exe(exe, emulator_name, folder_name):
    stem_key = _normalize(exe.stem)
    name_key = _normalize(emulator_name)
    folder_key = _normalize(folder_name)

    if not stem_key:
        return 0

    score = 0

    if name_key and stem_key.startswith(name_key):
        score += 100
        if stem_key == name_key:
            score += 40

    if folder_key and stem_key.startswith(folder_key):
        score += 80
        if stem_key == folder_key:
            score += 40

    if name_key and name_key in stem_key:
        score += 20
    if folder_key and folder_key in stem_key:
        score += 15

    if name_key and name_key not in stem_key and folder_key not in stem_key:
        if stem_key in _GENERIC_EXE_TOKENS:
            return 0

    return score


def _iter_emulator_folders():
    roots = []

    if EMULATORS_DIR.is_dir():
        roots.append(EMULATORS_DIR)

    roots.append(APP_DIR)

    seen = set()

    for root in roots:
        try:
            for entry in sorted(root.iterdir()):
                if not entry.is_dir():
                    continue

                if entry.name.lower() in {
                    "icons", "emulators", "build", "dist",
                    "__pycache__", ".git", ".venv", "venv",
                }:
                    continue

                key = str(entry.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)

                yield entry
        except Exception:
            continue


def find_executable_for_emulator(emulator_name):
    if not emulator_name:
        return None

    name_key = _normalize(emulator_name)
    if not name_key:
        return None

    best = None

    for folder in _iter_emulator_folders():
        folder_key = _normalize(folder.name)

        if not (
            folder_key == name_key
            or folder_key.startswith(name_key)
            or name_key.startswith(folder_key)
            or name_key in folder_key
            or folder_key in name_key
        ):
            continue

        for exe in _candidate_exes(folder):
            score = _score_exe(exe, emulator_name, folder.name)

            if score <= 0:
                continue

            if exe.parent == folder:
                score += 5

            if best is None or score > best[0]:
                best = (score, exe)

    return best[1] if best else None


def autolink_executables(config):
    changed = False

    for emulator in config.get("emulators", []):
        current = (emulator.get("executable") or "").strip()

        if current and Path(current).exists():
            continue

        found = find_executable_for_emulator(
            emulator.get("name", "")
        )

        if found and found.exists():
            emulator["executable"] = str(found)
            changed = True

    return changed


# ---------------------------------------------------------------------------
# Console / platform auto-detection
# ---------------------------------------------------------------------------

# Aliases are checked in order — longer, more specific ones first so
# "playstation 2" is caught before "playstation", "xbox 360" before "xbox", etc.
_PLATFORM_ALIASES = [
    # SONY
    ("playstation 5", "PlayStation 5"),
    ("playstation5", "PlayStation 5"),
    ("ps5", "PlayStation 5"),
    ("playstation 4", "PlayStation 4"),
    ("playstation4", "PlayStation 4"),
    ("ps4", "PlayStation 4"),
    ("shadps4", "PlayStation 4"),
    ("playstation 3", "PlayStation 3"),
    ("playstation3", "PlayStation 3"),
    ("ps3", "PlayStation 3"),
    ("rpcs3", "PlayStation 3"),
    ("playstation 2", "PlayStation 2"),
    ("playstation2", "PlayStation 2"),
    ("ps2", "PlayStation 2"),
    ("pcsx2", "PlayStation 2"),
    ("playstation vita", "PlayStation Vita"),
    ("playstationvita", "PlayStation Vita"),
    ("ps vita", "PlayStation Vita"),
    ("psvita", "PlayStation Vita"),
    ("vita3k", "PlayStation Vita"),
    ("psp", "PlayStation Portable"),
    ("playstation portable", "PlayStation Portable"),
    ("playstationportable", "PlayStation Portable"),
    ("ppsspp", "PlayStation Portable"),
    ("playstation 1", "PlayStation 1"),
    ("playstation1", "PlayStation 1"),
    ("ps1", "PlayStation 1"),
    ("psx", "PlayStation 1"),
    ("duckstation", "PlayStation 1"),

    # MICROSOFT
    ("xbox 360", "Xbox 360"),
    ("xbox360", "Xbox 360"),
    ("xenia edge", "Xbox 360"),
    ("xenia canary", "Xbox 360"),
    ("xenia", "Xbox 360"),
    ("xbox", "Xbox"),
    ("xemu", "Xbox"),

    # NINTENDO
    ("nintendo switch", "Nintendo Switch"),
    ("nintendoswitch", "Nintendo Switch"),
    ("switch", "Nintendo Switch"),
    ("ryujinx", "Nintendo Switch"),
    ("citron", "Nintendo Switch"),
    ("yuzu", "Nintendo Switch"),
    ("wii u", "Wii U"),
    ("wiiu", "Wii U"),
    ("cemu", "Wii U"),
    ("gamecube", "GameCube"),
    ("dolphin", "GameCube"),
    ("wii", "Wii"),
    ("nintendo 3ds", "Nintendo 3DS"),
    ("nintendo3ds", "Nintendo 3DS"),
    ("3ds", "Nintendo 3DS"),
    ("azahar", "Nintendo 3DS"),
    ("citra", "Nintendo 3DS"),
    ("nintendo ds", "Nintendo DS"),
    ("nintendods", "Nintendo DS"),
    ("nds", "Nintendo DS"),
    ("melonds", "Nintendo DS"),
    ("nintendo 64", "Nintendo 64"),
    ("nintendo64", "Nintendo 64"),
    ("n64", "Nintendo 64"),
    ("gopher", "Nintendo 64"),
    ("project64", "Nintendo 64"),
    ("project 64", "Nintendo 64"),

    # SEGA
    ("sega saturn", "Sega Saturn"),
    ("segasaturn", "Sega Saturn"),
    ("saturn", "Sega Saturn"),
    ("ymir", "Sega Saturn"),
    ("sega dreamcast", "Sega Dreamcast"),
    ("segadreamcast", "Sega Dreamcast"),
    ("dreamcast", "Sega Dreamcast"),
    ("flycast", "Sega Dreamcast"),
    ("redream", "Sega Dreamcast"),

    # HANDHELD
    ("game boy advance", "Game Boy Advance"),
    ("gameboyadvance", "Game Boy Advance"),
    ("gba", "Game Boy Advance"),
    ("mgba", "Game Boy Advance"),
    ("visualboyadvance", "Game Boy Advance"),
    ("visual boy advance", "Game Boy Advance"),
]


def detect_platform(*hints):
    """Return a canonical platform name from any of the given hints.

    Hints can be a folder name, an exe stem, or an emulator name. The
    longest matching alias wins, so "xenia canary" beats "xenia".
    """
    best = None  # (alias_length, platform)

    for hint in hints:
        if not hint:
            continue

        key = _normalize(hint)
        if not key:
            continue

        for alias, platform in _PLATFORM_ALIASES:
            alias_key = _normalize(alias)

            if alias_key in key:
                length = len(alias_key)
                if best is None or length > best[0]:
                    best = (length, platform)

    return best[1] if best else None


def autolink_platforms(config):
    """Fill in / correct the platform for every emulator based on its
    name, executable, and folder. Returns True if anything changed.
    """
    changed = False

    for emulator in config.get("emulators", []):
        current = (emulator.get("platform") or "").strip()

        # Already known and valid → leave alone.
        if current and current in PLATFORMS:
            continue

        name = emulator.get("name", "")
        exe = emulator.get("executable", "")

        hints = [name, Path(exe).stem if exe else ""]

        # Add the folder the executable lives in (or its parent).
        if exe:
            try:
                exe_path = Path(exe)
                if exe_path.exists():
                    hints.append(exe_path.parent.name)
                    hints.append(exe_path.parent.parent.name)
            except Exception:
                pass

        detected = detect_platform(*hints)

        if detected and detected != current:
            emulator["platform"] = detected
            changed = True

    return changed


def discover_new_emulators(config):
    """Add any emulator folder that isn't already in config.json.

    Each new entry gets a name derived from the folder, a matching
    platform if one can be detected, and an auto-linked executable.
    Returns True if entries were added.
    """
    known_paths = set()
    known_names = set()

    for emulator in config.get("emulators", []):
        name = emulator.get("name", "")
        if name:
            known_names.add(_normalize(name))

        exe = emulator.get("executable", "")
        if exe:
            try:
                known_paths.add(str(Path(exe).resolve()).lower())
            except Exception:
                pass

    added = False

    for folder in _iter_emulator_folders():
        # Skip folders that have no runnable exe at all.
        exes = list(_candidate_exes(folder))
        if not exes:
            continue

        folder_key = _normalize(folder.name)

        # Already represented by name?
        if folder_key in known_names:
            continue

        # Pick the best exe inside this folder using the folder name
        # as the emulator name.
        best = None
        for exe in exes:
            score = _score_exe(exe, folder.name, folder.name)
            if score <= 0:
                continue
            if exe.parent == folder:
                score += 5
            if best is None or score > best[0]:
                best = (score, exe)

        if best is None:
            # Fall back to the first exe in the folder.
            best = (0, exes[0])

        chosen = best[1]

        # Already represented by executable path?
        try:
            if str(chosen.resolve()).lower() in known_paths:
                continue
        except Exception:
            pass

        platform = detect_platform(
            folder.name,
            chosen.stem,
            chosen.parent.name,
        ) or "Other"

        config["emulators"].append({
            "name": folder.name,
            "platform": platform,
            "executable": str(chosen),
            "icon": find_icon_for_name(folder.name) or "",
            "arguments": "",
            "run_as_admin": False,
        })

        known_names.add(folder_key)
        added = True

    return added


# ---------------------------------------------------------------------------
# Categories / platform mapping
# ---------------------------------------------------------------------------

CATEGORY_ORDER = [
    "SONY",
    "MICROSOFT",
    "NINTENDO",
    "SEGA",
    "HANDHELD",
    "OTHER",
]

CATEGORY_SYMBOLS = {
    "SONY": "◉",
    "MICROSOFT": "●",
    "NINTENDO": "◆",
    "SEGA": "▣",
    "HANDHELD": "▭",
    "OTHER": "⚙",
}

PLATFORM_GROUPS = {
    "PlayStation 1": "SONY",
    "PlayStation 2": "SONY",
    "PlayStation 3": "SONY",
    "PlayStation 4": "SONY",
    "PlayStation 5": "SONY",
    "PlayStation Portable": "SONY",
    "PlayStation Vita": "SONY",

    "Xbox": "MICROSOFT",
    "Xbox 360": "MICROSOFT",

    "Nintendo 64": "NINTENDO",
    "GameCube": "NINTENDO",
    "Wii": "NINTENDO",
    "Wii U": "NINTENDO",
    "Nintendo Switch": "NINTENDO",
    "Nintendo DS": "NINTENDO",
    "Nintendo 3DS": "NINTENDO",

    "Game Boy Advance": "HANDHELD",

    "Sega Saturn": "SEGA",
    "Sega Dreamcast": "SEGA",
}

PLATFORMS = list(PLATFORM_GROUPS.keys())

DEFAULT_CONFIG = {
    "maximized": True,
    "emulators": []
}


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def load_config():
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return copy.deepcopy(DEFAULT_CONFIG)

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        QMessageBox.warning(
            None,
            "Could Not Read Config",
            f"Failed to read:\n{CONFIG_FILE}\n\n{e}\n\nStarting empty."
        )
        return copy.deepcopy(DEFAULT_CONFIG)

    if not isinstance(data, dict):
        return copy.deepcopy(DEFAULT_CONFIG)

    data.setdefault("maximized", True)
    data.setdefault("emulators", [])

    for emulator in data["emulators"]:
        emulator.setdefault("name", "Unnamed Emulator")
        emulator.setdefault("platform", "Other")
        emulator.setdefault("executable", "")
        emulator.setdefault("icon", "")
        emulator.setdefault("arguments", "")
        emulator.setdefault("run_as_admin", False)

    return data


def save_config(config):
    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        QMessageBox.critical(
            None,
            "Could Not Save Config",
            f"Failed to write:\n{CONFIG_FILE}\n\n{e}"
        )
        raise


def category_for_platform(platform):
    if not platform:
        return "OTHER"

    if platform in PLATFORM_GROUPS:
        return PLATFORM_GROUPS[platform]

    value = platform.lower()

    if any(x in value for x in ("playstation", "ps1", "ps2", "ps3", "ps4", "ps5", "psp", "vita")):
        return "SONY"
    if "xbox" in value:
        return "MICROSOFT"
    if any(x in value for x in ("nintendo", "gamecube", "wii")):
        return "NINTENDO"
    if "sega" in value or "dreamcast" in value:
        return "SEGA"
    if any(x in value for x in ("game boy", "gba", "handheld")):
        return "HANDHELD"

    return "OTHER"


def make_icon_pixmap(symbol, size=96):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setPen(QPen(Qt.GlobalColor.white, 2))
    font = QFont("Segoe UI Symbol", int(size * 0.48))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(
        pixmap.rect(),
        Qt.AlignmentFlag.AlignCenter,
        symbol
    )

    painter.end()
    return pixmap


# ---------------------------------------------------------------------------
# Add / edit emulator dialog
# ---------------------------------------------------------------------------

class EmulatorDialog(QDialog):
    def __init__(self, parent=None, emulator=None):
        super().__init__(parent)

        self.emulator = emulator or {}
        self._original_icon = self.emulator.get("icon", "")

        self.setWindowTitle(
            "Edit Emulator" if emulator else "Add Emulator"
        )
        self.resize(680, 310)

        layout = QFormLayout(self)
        layout.setSpacing(10)

        self.name_edit = QLineEdit(
            self.emulator.get("name", "")
        )

        self.platform_combo = QComboBox()
        self.platform_combo.addItems(PLATFORMS)

        platform = self.emulator.get("platform") or "Other"
        index = self.platform_combo.findText(platform)

        if index < 0:
            self.platform_combo.addItem(platform)
            index = self.platform_combo.findText(platform)

        self.platform_combo.setCurrentIndex(index)

        self.exe_edit = QLineEdit(
            self.emulator.get("executable", "")
        )

        exe_browse = QPushButton("Browse...")
        exe_browse.clicked.connect(self.browse_executable)

        exe_auto = QPushButton("Auto-find")
        exe_auto.clicked.connect(self.autofind_executable)

        exe_row = QHBoxLayout()
        exe_row.addWidget(self.exe_edit)
        exe_row.addWidget(exe_browse)
        exe_row.addWidget(exe_auto)

        self.icon_edit = QLineEdit()
        resolved = resolve_icon(self.emulator.get("icon", ""))
        if resolved:
            self.icon_edit.setText(str(resolved))

        icon_browse = QPushButton("Browse...")
        icon_browse.clicked.connect(self.browse_icon)

        icon_clear = QPushButton("Clear")
        icon_clear.clicked.connect(lambda: self.icon_edit.clear())

        icon_row = QHBoxLayout()
        icon_row.addWidget(self.icon_edit)
        icon_row.addWidget(icon_browse)
        icon_row.addWidget(icon_clear)

        self.args_edit = QLineEdit(
            self.emulator.get("arguments", "")
        )
        self.args_edit.setPlaceholderText(
            "Optional, e.g. --fullscreen"
        )

        self.admin_check = QCheckBox(
            "Run as administrator"
        )
        self.admin_check.setChecked(
            bool(self.emulator.get("run_as_admin", False))
        )

        if not IS_WINDOWS:
            self.admin_check.setEnabled(False)
            self.admin_check.setToolTip(
                "Administrator elevation is only supported on Windows."
            )

        layout.addRow("Name:", self.name_edit)
        layout.addRow("Console:", self.platform_combo)
        layout.addRow("Executable:", exe_row)
        layout.addRow("Custom Icon:", icon_row)
        layout.addRow("Arguments:", self.args_edit)
        layout.addRow("", self.admin_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout.addRow(buttons)

    def browse_executable(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Emulator Executable",
            "",
            "Executable (*.exe);;All Files (*)"
        )

        if path:
            self.exe_edit.setText(path)

            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(path).stem)

            self.maybe_autofill_platform()

    def autofind_executable(self):
        name = self.name_edit.text().strip()

        if not name:
            QMessageBox.information(
                self,
                "Name Required",
                "Enter the emulator name first, then click Auto-find."
            )
            return

        found = find_executable_for_emulator(name)

        if not found:
            QMessageBox.information(
                self,
                "Not Found",
                f'Could not find an .exe matching "{name}" in the '
                f"folders next to the app."
            )
            return

        self.exe_edit.setText(str(found))
        self.maybe_autofill_platform()

    def maybe_autofill_platform(self):
        """If the user hasn't chosen a platform yet, try to detect one."""
        current = self.platform_combo.currentText()

        if current and current != "Other":
            return

        name = self.name_edit.text().strip()
        exe = self.exe_edit.text().strip()

        hints = [name]
        if exe:
            hints.append(Path(exe).stem)
            try:
                hints.append(Path(exe).parent.name)
                hints.append(Path(exe).parent.parent.name)
            except Exception:
                pass

        detected = detect_platform(*hints)

        if detected:
            index = self.platform_combo.findText(detected)
            if index >= 0:
                self.platform_combo.setCurrentIndex(index)

    def browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Emulator Icon",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.ico *.bmp *.gif);;All Files (*)"
        )

        if path:
            self.icon_edit.setText(path)

    def validate_and_accept(self):
        name = self.name_edit.text().strip()
        exe = self.exe_edit.text().strip()

        if not name:
            QMessageBox.warning(
                self,
                "Missing Name",
                "Please enter an emulator name."
            )
            return

        if not exe:
            QMessageBox.warning(
                self,
                "Missing Executable",
                "Please select the emulator executable."
            )
            return

        if not Path(exe).exists():
            answer = QMessageBox.question(
                self,
                "Executable Not Found",
                "The executable does not currently exist.\n\n"
                "Save this path anyway?",
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No
            )

            if answer != QMessageBox.StandardButton.Yes:
                return

        self.accept()

    def values(self):
        name = self.name_edit.text().strip()
        icon_field = self.icon_edit.text().strip()

        stored_icon = ""

        if icon_field:
            current_resolved = resolve_icon(self._original_icon)
            chosen = Path(icon_field)

            if current_resolved and chosen.resolve() == current_resolved.resolve():
                stored_icon = self._original_icon
            else:
                imported = import_icon(icon_field, name)
                if imported:
                    if self._original_icon:
                        delete_icon(self._original_icon)
                    stored_icon = imported
                else:
                    stored_icon = self._original_icon
        else:
            if self._original_icon:
                delete_icon(self._original_icon)
            stored_icon = ""

        if not stored_icon:
            stored_icon = find_icon_for_name(name)

        platform = self.platform_combo.currentText()

        # Last-chance auto detection if it's still "Other".
        if not platform or platform == "Other":
            exe = self.exe_edit.text().strip()
            hints = [name]
            if exe:
                hints.append(Path(exe).stem)
                try:
                    hints.append(Path(exe).parent.name)
                    hints.append(Path(exe).parent.parent.name)
                except Exception:
                    pass
            detected = detect_platform(*hints)
            if detected:
                platform = detected

        return {
            "name": name,
            "platform": platform,
            "executable": self.exe_edit.text().strip(),
            "icon": stored_icon,
            "arguments": self.args_edit.text().strip(),
            "run_as_admin": self.admin_check.isChecked()
        }


# ---------------------------------------------------------------------------
# Compact clickable tile
# ---------------------------------------------------------------------------

class EmulatorCard(QFrame):
    CARD_W = 132
    CARD_H = 132

    def __init__(self, emulator, launcher):
        super().__init__()

        self.emulator = emulator
        self.launcher = launcher

        self.setObjectName("EmulatorCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFixedHeight(78)
        self.icon_label.setStyleSheet("background:transparent;")

        icon_path = resolve_icon(emulator.get("icon", ""))

        if not icon_path:
            auto = find_icon_for_name(emulator.get("name", ""))
            if auto:
                icon_path = ICONS_DIR / auto

        if icon_path and icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                self.icon_label.setPixmap(
                    pixmap.scaled(
                        QSize(72, 72),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                )
            else:
                self.set_default_icon()
        else:
            self.set_default_icon()

        layout.addWidget(self.icon_label)

        self.name_label = QLabel(
            emulator.get("name", "Unnamed")
        )
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setWordWrap(True)
        self.name_label.setFont(
            QFont("Segoe UI", 9, QFont.Weight.Bold)
        )
        self.name_label.setStyleSheet(
            "background:transparent; color:#edf4ff;"
        )
        layout.addWidget(self.name_label)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            f'{emulator.get("name", "")} — '
            f'{emulator.get("platform", "")}'
        )

    def set_default_icon(self):
        platform = self.emulator.get("platform", "Other")
        category = category_for_platform(platform)

        symbol = CATEGORY_SYMBOLS.get(category, "⚙")
        pixmap = make_icon_pixmap(symbol, 72)
        self.icon_label.setPixmap(pixmap)

    def enterEvent(self, event):
        self.setStyleSheet("""
            QFrame#EmulatorCard {
                background:#0f1f35;
                border:1px solid #2c5c95;
                border-radius:10px;
            }
        """)
        self.name_label.setStyleSheet(
            "background:transparent; color:#ffffff;"
        )
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet("""
            QFrame#EmulatorCard {
                background:transparent;
                border:1px solid transparent;
                border-radius:10px;
            }
        """)
        self.name_label.setStyleSheet(
            "background:transparent; color:#edf4ff;"
        )
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.launcher.launch_emulator(self.emulator)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, config, launcher=None, parent=None):
        super().__init__(parent)

        self.config = config
        self.launcher = launcher

        self.setWindowTitle("Emulator Settings")
        self.resize(850, 560)

        root = QVBoxLayout(self)

        title = QLabel("Configured Emulators")
        title.setFont(
            QFont("Segoe UI", 17, QFont.Weight.Bold)
        )
        root.addWidget(title)

        self.list = QListWidget()
        root.addWidget(self.list, 1)

        buttons = QHBoxLayout()

        add = QPushButton("＋ Add")
        edit = QPushButton("✎ Edit")
        remove = QPushButton("－ Remove")
        test = QPushButton("▶ Test Launch")
        relink = QPushButton("⟳ Auto-find")
        scan = QPushButton("⌕ Scan folders")

        add.clicked.connect(self.add_emulator)
        edit.clicked.connect(self.edit_emulator)
        remove.clicked.connect(self.remove_emulator)
        test.clicked.connect(self.test_emulator)
        relink.clicked.connect(self.autofind_all)
        scan.clicked.connect(self.scan_folders)

        buttons.addWidget(add)
        buttons.addWidget(edit)
        buttons.addWidget(remove)
        buttons.addWidget(test)
        buttons.addWidget(relink)
        buttons.addWidget(scan)
        buttons.addStretch()

        root.addLayout(buttons)

        dialog_buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )

        dialog_buttons.accepted.connect(self.accept)
        dialog_buttons.rejected.connect(self.reject)

        root.addWidget(dialog_buttons)

        self.refresh()

    def refresh(self):
        self.list.clear()

        for emulator in self.config["emulators"]:
            platform = emulator.get("platform", "Other")
            category = category_for_platform(platform)

            item = QListWidgetItem(
                f'{emulator.get("name", "Unnamed")}   •   '
                f'{category} / {platform}\n'
                f'{emulator.get("executable", "")}'
            )

            item.setSizeHint(QSize(0, 68))
            self.list.addItem(item)

    def selected_index(self):
        row = self.list.currentRow()

        if row < 0 or row >= len(self.config["emulators"]):
            return None

        return row

    def add_emulator(self):
        dialog = EmulatorDialog(self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config["emulators"].append(
                dialog.values()
            )
            self.refresh()

    def edit_emulator(self):
        index = self.selected_index()

        if index is None:
            QMessageBox.information(
                self,
                "Select Emulator",
                "Select an emulator first."
            )
            return

        dialog = EmulatorDialog(
            self,
            self.config["emulators"][index]
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config["emulators"][index] = dialog.values()
            self.refresh()

    def remove_emulator(self):
        index = self.selected_index()

        if index is None:
            return

        emulator = self.config["emulators"][index]

        answer = QMessageBox.question(
            self,
            "Remove Emulator",
            f'Remove "{emulator.get("name", "Unnamed")}"?',
            QMessageBox.StandardButton.Yes |
            QMessageBox.StandardButton.No
        )

        if answer == QMessageBox.StandardButton.Yes:
            delete_icon(emulator.get("icon", ""))
            self.config["emulators"].pop(index)
            self.refresh()

    def test_emulator(self):
        index = self.selected_index()

        if index is None:
            return

        if self.launcher is None:
            return

        self.launcher.launch_emulator(
            self.config["emulators"][index]
        )

    def autofind_all(self):
        exes = autolink_executables(self.config)
        plats = autolink_platforms(self.config)

        if exes or plats:
            self.refresh()
            QMessageBox.information(
                self,
                "Auto-find",
                "Executables and/or platforms were updated."
            )
        else:
            QMessageBox.information(
                self,
                "Auto-find",
                "Nothing new to link — everything is already valid."
            )

    def scan_folders(self):
        added = discover_new_emulators(self.config)

        # Even if nothing new, still refresh the platforms/exes of the
        # existing entries.
        autolink_executables(self.config)
        autolink_platforms(self.config)
        autolink_icons(self.config)

        self.refresh()

        if added:
            QMessageBox.information(
                self,
                "Scan folders",
                "New emulator folders were detected and added."
            )
        else:
            QMessageBox.information(
                self,
                "Scan folders",
                "No new emulator folders found next to the app."
            )

    def accept(self):
        save_config(self.config)
        super().accept()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.config = load_config()

        # Auto-discover before first paint:
        #   1. find any new emulator folders → add them
        #   2. fix any missing executables
        #   3. auto-select the console / platform
        #   4. auto-link icons
        added = discover_new_emulators(self.config)
        exes = autolink_executables(self.config)
        plats = autolink_platforms(self.config)
        icons = autolink_icons(self.config)

        if added or exes or plats or icons:
            save_config(self.config)

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1000, 650)

        self.build_ui()
        self.apply_style()
        self.refresh()

        if self.config.get("maximized", True):
            self.showMaximized()
        else:
            self.resize(1350, 850)

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(10)

        header = QHBoxLayout()

        title_column = QVBoxLayout()
        title_column.setSpacing(0)

        title = QLabel("Console Launcher")
        title.setFont(
            QFont("Segoe UI", 20, QFont.Weight.Bold)
        )

        subtitle = QLabel("Your consoles. One place.")
        subtitle.setStyleSheet("color:#7e96b4; font-size:11px;")

        title_column.addWidget(title)
        title_column.addWidget(subtitle)

        header.addLayout(title_column)
        header.addStretch()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search emulators...")
        self.search.setFixedWidth(280)
        self.search.setFixedHeight(34)
        self.search.textChanged.connect(self.refresh)

        add = QPushButton("＋  Add Emulator")
        add.setFixedHeight(34)
        add.clicked.connect(self.quick_add)

        settings = QPushButton("⚙  Settings")
        settings.setFixedHeight(34)
        settings.clicked.connect(self.open_settings)

        header.addWidget(self.search)
        header.addWidget(add)
        header.addWidget(settings)

        root.addLayout(header)

        self.sections_widget = QWidget()
        self.sections_layout = QVBoxLayout(self.sections_widget)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(10)
        self.sections_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop
        )

        root.addWidget(self.sections_widget, 1)

    def apply_style(self):
        self.setStyleSheet("""
            QWidget {
                background:#08111f;
                color:#edf4ff;
                font-family:"Segoe UI";
            }

            QMainWindow {
                background:#08111f;
            }

            QLineEdit {
                background:#101d2f;
                border:1px solid #1e3858;
                border-radius:8px;
                padding:6px 12px;
                color:#edf4ff;
                selection-background-color:#216cff;
            }

            QLineEdit:focus {
                border:1px solid #277cff;
            }

            QListWidget {
                background:#0b1627;
                border:1px solid #142842;
                border-radius:12px;
                padding:8px;
            }

            QListWidget::item {
                padding:12px 10px;
                margin:2px 0;
                border-radius:8px;
                color:#c7d6e8;
            }

            QListWidget::item:selected {
                background:#1c4fb5;
                color:white;
            }

            QPushButton {
                background:#14253b;
                border:1px solid #244364;
                border-radius:8px;
                padding:6px 12px;
                color:#edf4ff;
                font-weight:600;
            }

            QPushButton:hover {
                background:#1b3555;
                border-color:#3575b9;
            }

            QPushButton:pressed {
                background:#0e1d30;
            }

            QFrame#EmulatorCard {
                background:transparent;
                border:1px solid transparent;
                border-radius:10px;
            }

            QFrame#CategorySection {
                background:transparent;
                border:none;
            }

            QFrame#CategoryHeader {
                background:transparent;
                border:none;
            }
        """)

    def clear_sections(self):
        while self.sections_layout.count():
            item = self.sections_layout.takeAt(0)
            widget = item.widget()

            if widget:
                widget.deleteLater()

    def build_category_section(self, category, emulators):
        section = QFrame()
        section.setObjectName("CategorySection")

        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QFrame()
        header.setObjectName("CategoryHeader")
        header.setFixedHeight(22)

        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 0)
        header_layout.setSpacing(6)

        symbol = CATEGORY_SYMBOLS.get(category, "⚙")

        symbol_label = QLabel(symbol)
        symbol_label.setFont(
            QFont("Segoe UI Symbol", 11, QFont.Weight.Bold)
        )
        symbol_label.setStyleSheet(
            "background:transparent; color:#7fb3ff;"
        )

        name_label = QLabel(category)
        name_label.setFont(
            QFont("Segoe UI", 11, QFont.Weight.Bold)
        )
        name_label.setStyleSheet("background:transparent;")

        header_layout.addWidget(symbol_label)
        header_layout.addWidget(name_label)
        header_layout.addStretch()

        layout.addWidget(header)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        grid.setAlignment(
            Qt.AlignmentFlag.AlignTop |
            Qt.AlignmentFlag.AlignLeft
        )

        columns = 8

        for index, emulator in enumerate(emulators):
            row = index // columns
            column = index % columns

            grid.addWidget(
                EmulatorCard(emulator, self),
                row,
                column
            )

        layout.addWidget(grid_widget)

        return section

    def refresh(self, *_):
        self.clear_sections()

        query = self.search.text().strip().lower()

        emulators = self.config.get("emulators", [])

        if query:
            emulators = [
                emulator for emulator in emulators
                if query in emulator.get("name", "").lower()
                or query in emulator.get("platform", "").lower()
            ]

        grouped = {category: [] for category in CATEGORY_ORDER}

        for emulator in emulators:
            category = category_for_platform(
                emulator.get("platform", "Other")
            )

            if category not in grouped:
                grouped[category] = []

            grouped[category].append(emulator)

        for category in CATEGORY_ORDER:
            bucket = grouped.get(category, [])

            if not bucket:
                continue

            section = self.build_category_section(
                category,
                bucket
            )
            self.sections_layout.addWidget(section)

        for category, bucket in grouped.items():
            if category in CATEGORY_ORDER or not bucket:
                continue

            self.sections_layout.addWidget(
                self.build_category_section(category, bucket)
            )

    def quick_add(self):
        dialog = EmulatorDialog(self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config["emulators"].append(
                dialog.values()
            )

            save_config(self.config)
            self.refresh()

    def open_settings(self):
        dialog = SettingsDialog(
            copy.deepcopy(self.config),
            launcher=self,
            parent=self
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config = load_config()
            discover_new_emulators(self.config)
            autolink_executables(self.config)
            autolink_platforms(self.config)
            autolink_icons(self.config)
            save_config(self.config)
            self.refresh()

    def launch_emulator(self, emulator):
        executable = emulator.get("executable", "").strip()

        if not executable:
            QMessageBox.warning(
                self,
                "No Executable",
                "No emulator executable has been configured."
            )
            return

        exe_path = Path(executable)

        if not exe_path.exists():
            QMessageBox.warning(
                self,
                "Executable Not Found",
                f"The emulator executable could not be found:\n\n"
                f"{executable}\n\n"
                "Open Settings and correct the path."
            )
            return

        arguments = emulator.get("arguments", "").strip()

        try:
            if emulator.get("run_as_admin", False):
                if not IS_WINDOWS:
                    raise RuntimeError(
                        "Run as administrator is only supported on Windows."
                    )

                result = ctypes.windll.shell32.ShellExecuteW(
                    None,
                    "runas",
                    str(exe_path),
                    arguments if arguments else None,
                    str(exe_path.parent),
                    1
                )

                if result <= 32:
                    raise RuntimeError(
                        "Windows refused the administrator launch."
                    )

            else:
                command = [str(exe_path)]

                if arguments:
                    command.extend(
                        shlex.split(
                            arguments,
                            posix=False
                        )
                    )

                subprocess.Popen(
                    command,
                    cwd=str(exe_path.parent)
                )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Launch Failed",
                f'Could not launch "{emulator.get("name", "emulator")}".\n\n'
                f"{e}"
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)

    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ConsoleLauncher")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
