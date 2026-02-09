"""Cisco-style CLI transport over SSH (shared by QNAP, Netgear, etc.)."""

from __future__ import annotations

import logging
import re
import time

import paramiko

from networkmgmt.switchctrl.base.transport import BaseTransport
from networkmgmt.switchctrl.exceptions import AuthenticationError, SSHError

logger = logging.getLogger(__name__)

# Prompt patterns for the Cisco-style CLI
PROMPT_PATTERN = re.compile(r"[\r\n][\w\-]+[>#]\s*$")
CONFIG_PROMPT_PATTERN = re.compile(r"[\r\n][\w\-]+\(config[^\)]*\)#\s*$")
ENABLE_PROMPT_PATTERN = re.compile(r"[\r\n][\w\-]+#\s*$")

DEFAULT_TIMEOUT = 10
BUFFER_SIZE = 65535
READ_DELAY = 0.5


class CiscoCLITransport(BaseTransport):
    """SSH transport using paramiko interactive shell for Cisco-style CLI.

    Connects to switches that expose a Cisco IOS-like CLI over SSH.
    Credentials are passed explicitly — no default credentials.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 22,
        enable_password: str | None = None,
    ):
        super().__init__(host, username, password, port)
        self.enable_password = enable_password
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

        self._shell = self._client.invoke_shell()
        self._shell.settimeout(DEFAULT_TIMEOUT)

        # Wait for initial prompt
        self._read_until_prompt()
        logger.info("SSH connected to %s", self.host)

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

    def enter_enable_mode(self, enable_password: str | None = None) -> str:
        """Enter privileged EXEC mode (enable).

        Args:
            enable_password: Override the enable password set during init.

        Returns:
            CLI output after entering enable mode.
        """
        password = enable_password or self.enable_password
        if not password:
            raise SSHError("No enable password provided")

        output = self.send_command("enable")
        if "Password:" in output or "password:" in output:
            output = self.send_command(password)

        if not ENABLE_PROMPT_PATTERN.search(output):
            raise SSHError(f"Failed to enter enable mode. Output: {output}")

        logger.info("Entered enable mode")
        return output

    def send_command(self, command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
        """Send a single command and wait for the prompt.

        Args:
            command: CLI command to execute.
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
        if lines and PROMPT_PATTERN.search("\n" + lines[-1]):
            lines = lines[:-1]

        return "\n".join(lines).strip()

    def send_config_commands(self, commands: list[str], timeout: int = DEFAULT_TIMEOUT) -> str:
        """Enter config mode, send commands, then exit.

        Args:
            commands: List of configuration commands.
            timeout: Timeout per command.

        Returns:
            Combined output from all commands.
        """
        outputs: list[str] = []

        # Enter config mode
        outputs.append(self.send_command("configure terminal", timeout=timeout))

        for cmd in commands:
            outputs.append(self.send_command(cmd, timeout=timeout))

        # Exit config mode
        outputs.append(self.send_command("end", timeout=timeout))

        return "\n".join(outputs)

    def _read_until_prompt(self, timeout: int = DEFAULT_TIMEOUT) -> str:
        """Read shell output until a CLI prompt is detected.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            All output read from the shell.
        """
        output = ""
        start = time.time()
        assert self._shell is not None

        while time.time() - start < timeout:
            if self._shell.recv_ready():
                chunk = self._shell.recv(BUFFER_SIZE).decode("utf-8", errors="replace")
                output += chunk

                # Check for any known prompt pattern
                if PROMPT_PATTERN.search(output) or CONFIG_PROMPT_PATTERN.search(output):
                    break
            else:
                time.sleep(READ_DELAY)

        return output

    def _ensure_connected(self) -> None:
        if not self.is_connected():
            raise SSHError("Not connected. Call connect() first.")
