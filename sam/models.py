"""Data models for SAM for Linux."""

from dataclasses import dataclass


@dataclass
class Game:
    appid: int
    name: str
    install_path: str = ""

    def __str__(self):
        return f"{self.name} ({self.appid})"
