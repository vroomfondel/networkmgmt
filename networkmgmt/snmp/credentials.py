"""SNMP credentials (v2c community or v3 user) with protocol selection."""

from dataclasses import dataclass, field

AUTH_PROTOCOLS = ("md5", "sha", "sha256", "sha512")
PRIV_PROTOCOLS = ("des", "aes128")


@dataclass(frozen=True)
class SnmpCredentials:
    """Credentials for one SNMP agent.

    Either ``community`` (SNMPv2c) or ``user`` (SNMPv3) must be set. SNMPv3 runs as authPriv when
    ``priv_key`` is set and as authNoPriv otherwise.

    Attributes:
        community: v2c community string, used when ``user`` is unset.
        user: SNMPv3 user name.
        auth_key: SNMPv3 authentication passphrase (required with ``user``).
        auth_protocol: One of ``md5``, ``sha``, ``sha256``, ``sha512``.
        priv_key: SNMPv3 privacy passphrase; unset means authNoPriv.
        priv_protocol: One of ``des``, ``aes128``.
    """

    community: str | None = field(default=None, repr=False)
    user: str | None = None
    auth_key: str | None = field(default=None, repr=False)
    auth_protocol: str = "sha512"
    priv_key: str | None = field(default=None, repr=False)
    priv_protocol: str = "aes128"

    def __post_init__(self) -> None:
        """Validate the combination of fields.

        Raises:
            ValueError: If neither community nor user is given, the key is missing or a protocol is unknown.
        """
        if not self.user and not self.community:
            raise ValueError("either community (v2c) or user (v3) is required")
        if self.user:
            if not self.auth_key:
                raise ValueError("SNMPv3 requires auth_key")
            if self.auth_protocol not in AUTH_PROTOCOLS:
                raise ValueError(f"auth_protocol must be one of {AUTH_PROTOCOLS}, got {self.auth_protocol!r}")
            if self.priv_key and self.priv_protocol not in PRIV_PROTOCOLS:
                raise ValueError(f"priv_protocol must be one of {PRIV_PROTOCOLS}, got {self.priv_protocol!r}")

    @property
    def version(self) -> str:
        """Return ``"v3"`` when a user is set, else ``"v2c"``."""
        return "v3" if self.user else "v2c"

    @classmethod
    def v2c(cls, community: str) -> "SnmpCredentials":
        """Build SNMPv2c credentials.

        Args:
            community: Community string.

        Returns:
            The credentials.
        """
        return cls(community=community)

    @classmethod
    def v3(
        cls,
        user: str,
        auth_key: str,
        auth_protocol: str = "sha512",
        priv_key: str | None = None,
        priv_protocol: str = "aes128",
    ) -> "SnmpCredentials":
        """Build SNMPv3 credentials.

        Args:
            user: USM user name.
            auth_key: Authentication passphrase.
            auth_protocol: One of ``md5``, ``sha``, ``sha256``, ``sha512``.
            priv_key: Privacy passphrase; None means authNoPriv.
            priv_protocol: One of ``des``, ``aes128``.

        Returns:
            The credentials.
        """
        return cls(
            user=user,
            auth_key=auth_key,
            auth_protocol=auth_protocol,
            priv_key=priv_key,
            priv_protocol=priv_protocol,
        )
