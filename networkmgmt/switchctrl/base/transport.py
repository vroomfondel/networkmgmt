"""Abstract base transport for switch communication."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self


class BaseTransport(ABC):
    """Abstract base class for switch transports."""

    def __init__(self, host: str, username: str, password: str, port: int | None = None):
        self.host = host
        self.username = username
        self.password = password
        self.port = port

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the switch."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection to the switch."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if transport is currently connected."""

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> None:
        self.disconnect()
