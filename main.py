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

        exe_row = QHBoxLayout()
        exe_row.addWidget(self.exe_edit)
        exe_row.addWidget(exe_browse)

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

        return {
            "name": name,
            "platform": self.platform_combo.currentText(),
            "executable": self.exe_edit.text().strip(),
            "icon": stored_icon,
            "arguments": self.args_edit.text().strip(),
            "run_as_admin": self.admin_check.isChecked()
        }


# ---------------------------------------------------------------------------
# Compact clickable tile — sized to fit a full 1080p screen without scrolling.
# ---------------------------------------------------------------------------

class EmulatorCard(QFrame):
    """Compact clickable tile. One click launches the emulator."""

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

        add.clicked.connect(self.add_emulator)
        edit.clicked.connect(self.edit_emulator)
        remove.clicked.connect(self.remove_emulator)
        test.clicked.connect(self.test_emulator)

        buttons.addWidget(add)
        buttons.addWidget(edit)
        buttons.addWidget(remove)
        buttons.addWidget(test)
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

        if autolink_icons(self.config):
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

        # No QScrollArea — the whole grid fits on a 1080p screen.
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

            QScrollBar:vertical {
                background:#091321;
                width:10px;
                border-radius:5px;
            }

            QScrollBar::handle:vertical {
                background:#29415f;
                border-radius:5px;
                min-height:30px;
            }

            QScrollBar::handle:vertical:hover {
                background:#3a5c83;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height:0;
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

        # 8 columns is plenty for your current library; if a category
        # ever grows beyond that it will wrap to a second row and the
        # window will still fit on a 1080p screen for up to ~16 per cat.
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
            if autolink_icons(self.config):
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
