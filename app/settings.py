"""Only nonsecret desktop preferences; never read bot configuration."""

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

    def save(self, *, theme=None, key_path=None):
        if theme is not None:
            self.store.setValue("theme", "dark" if theme == "dark" else "light")
        if key_path is not None:
            self.store.setValue("key_path", str(key_path))
        self.store.sync()
        if self.store.status() != QSettings.Status.NoError:
            raise OSError("Unable to save desktop preferences.")
