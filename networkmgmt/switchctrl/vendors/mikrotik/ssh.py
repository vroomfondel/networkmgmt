"""RouterOS SSH transport for MikroTik switches."""

from __future__ import annotations

import logging
import re
import time

import paramiko

from networkmgmt.switchctrl.base.transport import BaseTransport
from networkmgmt.switchctrl.exceptions import AuthenticationError, SSHError

logger = logging.getLogger(__name__)

# RouterOS prompt pattern: [admin@MikroTik] > or [admin@hostname] /interface>
ROUTEROS_PROMPT = re.compile(r"\[[\w\-]+@[\w\-]+\]\s*[/\w]*>\s*$")

DEFAULT_TIMEOUT = 10
BUFFER_SIZE = 65535
READ_DELAY = 0.5


class RouterOSTransport(BaseTransport):
    """SSH transport for MikroTik RouterOS CLI.

    RouterOS uses its own CLI syntax (not Cisco-style).
    There is no enable-mode concept.
    """

    def __init__(
        self,
        host: str,
        username: str = "admin",
        password: str = "",
        port: int = 22,
    ):
        super().__init__(host, username, password, port)
        self._client: paramiko.SSHClient | None = None
        self._shell: paramiko.Channel | None = None

    def connect(self) -> None:
        """Establish SSH connection and open interactive shell."""
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            self._client.connect(
                hostname=self.host,
                port=self.port or 22,
                username=self.username,
                password=self.password,
                look_for_keys=False,
                allow_agent=False,
                timeout=DEFAULT_TIMEOUT,
            )
        except paramiko.AuthenticationException as e:
            raise AuthenticationError(f"SSH authentication failed: {e}") from e
        except Exception as e:
            raise SSHError(f"SSH connection failed: {e}") from e

        self._shell = self._client.invoke_shell(width=200)
        self._shell.settimeout(DEFAULT_TIMEOUT)

        # Wait for initial prompt
        self._read_until_prompt()
        logger.info("RouterOS SSH connected to %s", self.host)

    def disconnect(self) -> None:
        """Close SSH shell and connection."""
        if self._shell:
            try:
                self._shell.close()
            except Exception:
                pass
            self._shell = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def is_connected(self) -> bool:
        """Check if SSH connection and shell are active."""
        if self._client is None or self._shell is None:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active() and not self._shell.closed

    def send_command(self, command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
        """Send a single command and wait for the prompt.

        Args:
            command: RouterOS CLI command to execute.
            timeout: Maximum seconds to wait for response.

        Returns:
            Command output (prompt stripped).
        """
        self._ensure_connected()
        assert self._shell is not None
        self._shell.send((command + "\n").encode())
        output = self._read_until_prompt(timeout=timeout)

        # Strip the echoed command from output
        lines = output.splitlines()
        if lines and command in lines[0]:
            lines = lines[1:]
        # Strip trailing prompt line
        if lines and ROUTEROS_PROMPT.search(lines[-1]):
            lines = lines[:-1]

        return "\n".join(lines).strip()

    def _read_until_prompt(self, timeout: int = DEFAULT_TIMEOUT) -> str:
        """Read shell output until a RouterOS prompt is detected."""
        output = ""
        start = time.time()
        assert self._shell is not None

        while time.time() - start < timeout:
            if self._shell.recv_ready():
                chunk = self._shell.recv(BUFFER_SIZE).decode("utf-8", errors="replace")
                output += chunk

                if ROUTEROS_PROMPT.search(output):
                    break
            else:
                time.sleep(READ_DELAY)

        return output

    def _ensure_connected(self) -> None:
        if not self.is_connected():
            raise SSHError("Not connected. Call connect() first.")
