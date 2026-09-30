from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QPushButton, QWidget)

from .. import winapp
from ..hotkeyspec import parse_hotkey


class SettingsDialog(QDialog):
    def __init__(self, ctx, parent=None, focus_hotkey: bool = False):
        super().__init__(parent)
        self.ctx = ctx
        s = ctx.settings
        self.setWindowTitle("reelgrab archive settings")
        self.hotkey = QKeySequenceEdit(QKeySequence(s.hotkey))
        self.hotkey.setMaximumSequenceLength(1)
        self.limit = QDoubleSpinBox(minimum=1, maximum=500, decimals=0, suffix=" MB", value=s.limit_mb)
        self.paste = QCheckBox("Enter in the picker pastes straight into the previous window")
        self.paste.setChecked(s.auto_paste)
        self.folder = QLineEdit(s.archive_dir or str(ctx.paths.root))
        browse = QPushButton("Browse…", clicked=self._browse)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.folder, 1)
        rl.addWidget(browse)
        self.startup = QCheckBox("Launch at Windows startup (hidden in the tray)")
        self.startup.setChecked(winapp.is_startup_enabled())
        self.startup.setEnabled(winapp.IS_WIN)
        shortcuts = QPushButton("Create Desktop && Start-menu shortcuts", clicked=self._shortcuts)
        shortcuts.setEnabled(winapp.IS_WIN)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #f87171")

        form = QFormLayout(self)
        form.addRow("Picker hotkey", self.hotkey)
        form.addRow("Discord size limit", self.limit)
        form.addRow("", self.paste)
        form.addRow("Archive folder", row)
        form.addRow("", QLabel("Changing the archive folder takes effect after a restart."))
        form.addRow("", self.startup)
        form.addRow("", shortcuts)
        form.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        if focus_hotkey:
            self.hotkey.setFocus()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Archive folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def _shortcuts(self):
        try:
            made = winapp.create_shortcuts()
        except OSError as e:
            self.error.setText(f"Shortcuts: {e}")
            return
        self.error.setStyleSheet("color: #4ade80")
        self.error.setText(f"Created {len(made)} shortcut(s) – look for \"reelgrab archive\" on the Desktop and Start menu")

    def _save(self):
        text = self.hotkey.keySequence().toString(QKeySequence.PortableText)
        try:
            parse_hotkey(text)
        except ValueError as e:
            self.error.setText(f"Hotkey: {e}")
            return
        s = self.ctx.settings
        s.hotkey = text
        s.limit_mb = float(self.limit.value())
        s.auto_paste = self.paste.isChecked()
        folder = self.folder.text().strip()
        s.archive_dir = None if folder == str(self.ctx.paths.root) and not s.archive_dir else folder or None
        if self.startup.isChecked() != winapp.is_startup_enabled():
            try:
                winapp.set_startup(self.startup.isChecked())
            except OSError as e:
                self.error.setStyleSheet("color: #f87171")
                self.error.setText(f"Startup setting: {e}")
                self.startup.setChecked(winapp.is_startup_enabled())
                return
        s.save()
        self.ctx.apply_settings()
        self.accept()
