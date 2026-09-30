from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QPushButton, QWidget)

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
        self.error = QLabel("")
        self.error.setStyleSheet("color: #f87171")

        form = QFormLayout(self)
        form.addRow("Picker hotkey", self.hotkey)
        form.addRow("Discord size limit", self.limit)
        form.addRow("", self.paste)
        form.addRow("Archive folder", row)
        form.addRow("", QLabel("Changing the archive folder takes effect after a restart."))
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
        s.save()
        self.ctx.apply_settings()
        self.accept()
