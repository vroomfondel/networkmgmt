"""Utility functions for QNAP switch management."""

import hashlib
import re

ENABLE_SALT = "qNaPQsw"
ENABLE_ROUNDS = 5000
_CLEAN_SERIAL = re.compile(r"[A-Za-z0-9]+")
_B64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ORDER = (
    (0, 21, 42),
    (22, 43, 1),
    (44, 2, 23),
    (3, 24, 45),
    (25, 46, 4),
    (47, 5, 26),
    (6, 27, 48),
    (28, 49, 7),
    (50, 8, 29),
    (9, 30, 51),
    (31, 52, 10),
    (53, 11, 32),
    (12, 33, 54),
    (34, 55, 13),
    (56, 14, 35),
    (15, 36, 57),
    (37, 58, 16),
    (59, 17, 38),
    (18, 39, 60),
    (40, 61, 19),
    (62, 20, 41),
)


def clean_serial(serial: str) -> str:
    """Return the leading alphanumeric part of a serial number.

    QSS 1.3.2 returns the serial followed by U+FFFD garbage; the enable password
    is derived from the clean part only.

    Args:
        serial: Serial number as reported by the switch.

    Returns:
        The serial up to the first non-alphanumeric character.
    """
    match = _CLEAN_SERIAL.match(serial.strip())
    return match.group(0) if match else ""


def _b64_crypt(word: int, chars: int) -> str:
    return "".join(_B64[(word >> (6 * i)) & 0x3F] for i in range(chars))


def sha512_crypt(password: bytes, salt: bytes, rounds: int = ENABLE_ROUNDS) -> str:
    """Compute a ``$6$`` sha512-crypt hash without the removed ``crypt`` module.

    Implements Drepper's SHA-crypt specification; the result matches
    ``openssl passwd -6 -salt <salt>`` for non-empty passwords.

    Args:
        password: Password bytes.
        salt: Salt bytes, truncated to 16 as the specification demands.
        rounds: Number of rounds (the ``rounds=`` prefix is omitted for the default).

    Returns:
        The full crypt string ``$6$<salt>$<hash>``.
    """
    salt = salt[:16]
    plen = len(password)
    digest_b = hashlib.sha512(password + salt + password).digest()
    hash_a = hashlib.sha512(password + salt)
    hash_a.update(digest_b * (plen // 64) + digest_b[: plen % 64])
    bits = plen
    while bits:
        hash_a.update(digest_b if bits & 1 else password)
        bits >>= 1
    digest_a = hash_a.digest()
    seq_p = (hashlib.sha512(password * plen).digest() * (plen // 64 + 1))[:plen]
    seq_s = (hashlib.sha512(salt * (16 + digest_a[0])).digest() * (len(salt) // 64 + 1))[: len(salt)]
    digest_c = digest_a
    for i in range(rounds):
        hash_c = hashlib.sha512(seq_p if i & 1 else digest_c)
        if i % 3:
            hash_c.update(seq_s)
        if i % 7:
            hash_c.update(seq_p)
        hash_c.update(digest_c if i & 1 else seq_p)
        digest_c = hash_c.digest()
    encoded = "".join(_b64_crypt((digest_c[i] << 16) | (digest_c[j] << 8) | digest_c[k], 4) for i, j, k in _ORDER)
    encoded += _b64_crypt(digest_c[63], 2)
    prefix = "$6$" if rounds == ENABLE_ROUNDS else f"$6$rounds={rounds}$"
    return f"{prefix}{salt.decode()}${encoded}"


def generate_enable_password(serial: str) -> str:
    """Generate the enable-mode password from the switch serial number.

    The QNAP QSW hidden CLI uses sha512-crypt (5000 rounds, salt ``qNaPQsw``) of the
    serial, written as ``$<hash>$`` (same as ``openssl passwd -6 -salt qNaPQsw``).
    The same password is the ``debug`` SSH login, which opens a root shell; ``admin``
    and ``guest`` land in the restricted CLI.

    Args:
        serial: The switch serial number (garbage after the alphanumeric part is dropped).

    Returns:
        The enable password string.

    Raises:
        ValueError: The serial contains no usable characters.
    """
    cleaned = clean_serial(serial)
    if not cleaned:
        raise ValueError("Serial number is empty after removing non-alphanumeric characters")
    digest = sha512_crypt(cleaned.encode(), ENABLE_SALT.encode()).split("$")[-1]
    return f"${digest}$"
