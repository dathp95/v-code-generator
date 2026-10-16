"""The result of issuing an activation."""

from dataclasses import dataclass
from typing import Any


@dataclass
class IssuedActivation:
    activation_key: str
    payload: dict[str, Any]
