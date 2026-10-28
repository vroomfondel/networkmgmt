"""Helpers for Q-BRIDGE ``PortList`` bitmaps (most significant bit of the first octet is port 1)."""


def portlist_to_ports(octets: bytes) -> set[int]:
    """Decode a PortList bitmap into the set of 1-based port numbers.

    Args:
        octets: Raw PortList octets.

    Returns:
        Port numbers whose bit is set.
    """
    return {i * 8 + j + 1 for i, byte in enumerate(octets) for j in range(8) if byte & (0x80 >> j)}


def ports_to_portlist(ports: set[int] | frozenset[int], length: int) -> bytes:
    """Encode port numbers into a PortList bitmap of fixed length.

    Args:
        ports: 1-based port numbers.
        length: Number of octets of the result.

    Returns:
        The bitmap; all bits not in ``ports`` are 0.
    """
    out = bytearray(length)
    for idx in ports:
        out[(idx - 1) // 8] |= 0x80 >> ((idx - 1) % 8)
    return bytes(out)


def portlist_patch(octets: bytes, length: int, bits: dict[int, bool]) -> bytes:
    """Change single bits of a PortList and leave every other bit untouched.

    The GS108Tv2 keeps the CPU port (bit 13) set in every untagged bitmap and refuses further untagged
    edits of a VLAN once that bit was cleared, so only the bits of ports that actually move may be written.

    Args:
        octets: Current raw bitmap; padded with zeros or truncated to ``length``.
        length: Number of octets of the result.
        bits: Port number mapped to the new bit value.

    Returns:
        The patched bitmap.
    """
    out = bytearray(octets.ljust(length, b"\x00")[:length])
    for idx, on in bits.items():
        mask = 0x80 >> ((idx - 1) % 8)
        pos = (idx - 1) // 8
        out[pos] = (out[pos] | mask) if on else (out[pos] & ~mask & 0xFF)
    return bytes(out)
