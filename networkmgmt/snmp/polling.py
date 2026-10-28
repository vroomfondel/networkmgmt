"""Read-back polling for SNMP writes that the agent applies with delay."""

import time
from collections.abc import Callable

from networkmgmt.snmp.errors import SnmpSettleTimeoutError


def poll_until[T](
    read: Callable[[], T],
    done: Callable[[T], bool],
    *,
    timeout: float = 30.0,
    interval: float = 1.0,
    what: str = "value",
) -> T:
    """Call ``read`` until ``done`` accepts the result.

    Args:
        read: Reads the current state from the switch.
        done: Returns True once the state matches the desired one.
        timeout: Seconds to wait in total.
        interval: Seconds between two reads.
        what: Description used in the timeout message.

    Returns:
        The last value read, for which ``done`` returned True.

    Raises:
        SnmpSettleTimeoutError: If ``done`` never accepted a value within ``timeout``.
    """
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if done(value):
            return value
        if time.monotonic() > deadline:
            raise SnmpSettleTimeoutError(f"switch did not apply {what} within {timeout:g}s")
        time.sleep(interval)
