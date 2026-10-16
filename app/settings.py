"""Allowlisted nonsecret preferences; credentials never enter QSettings."""

from pathlib import Path

from PySide6.QtCore import QSettings

from storage.database import REPOSITORY_ROOT


class AppSettings:
    def __init__(self, path=None):
        path = Path(path) if path else REPOSITORY_ROOT / "data" / "desktop.ini"
        self.store = QSettings(str(path), QSettings.Format.IniFormat)

    @property
    def theme(self):
        return "dark" if self.store.value("theme", "light") == "dark" else "light"

    @property
    def key_path(self):
        return str(self.store.value("key_path", ""))

    @property
    def bot_admin_id(self):
        return str(self.store.value("bot_admin_id", ""))

    @property
    def bot_key_path(self):
        return str(self.store.value("bot_key_path", ""))

    @property
    def remember_token(self):
        return self.store.value("remember_token", False, type=bool)

    def save(self, *, theme=None, key_path=None, bot_admin_id=None, bot_key_path=None, remember_token=None):
        if theme is not None:
            self.store.setValue("theme", "dark" if theme == "dark" else "light")
        if key_path is not None:
            self.store.setValue("key_path", str(key_path))
        for key, value in (("bot_admin_id", bot_admin_id), ("bot_key_path", bot_key_path), ("remember_token", remember_token)):
            if value is not None:
                self.store.setValue(key, value)
        self.store.sync()
        if self.store.status() != QSettings.Status.NoError:
            raise OSError("Unable to save desktop preferences.")
