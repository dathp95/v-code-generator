"""Versioned stdin-only startup configuration; no environment or endpoint overrides."""
from dataclasses import dataclass, field
from pathlib import Path
import re

VERSION = 1


@dataclass(frozen=True)
class BotConfig:
    token: str = field(repr=False)
    admin_id: int
    key_path: str
    password: str = field(repr=False)
    db_path: str

    @classmethod
    def parse(cls, message):
        if message.get("version") != VERSION or message.get("command") != "start":
            raise ValueError("Invalid startup protocol.")
        config = cls(**message["config"])
        if (not isinstance(config.admin_id, int) or isinstance(config.admin_id, bool)
                or config.admin_id <= 0 or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", config.token)
                or not config.password or not Path(config.key_path).is_absolute()
                or not Path(config.db_path).is_absolute()):
            raise ValueError("Invalid bot configuration.")
        return config
