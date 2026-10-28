"""QNAP QSS VLAN management over the REST API.

QSS has no PVID: a port's untagged membership is its native/access VLAN, so a port is
untagged in at most one VLAN. All writes are serialised by the transport's request delay.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypedDict

from networkmgmt.switchctrl.base.managers import BaseVLANManager
from networkmgmt.switchctrl.exceptions import APIError, VLANError
from networkmgmt.switchctrl.models.vlan import VLAN, TrunkConfig
from networkmgmt.switchctrl.vendors.qnap.rest import API_PATH_V1, QNAPRESTTransport

logger = logging.getLogger(__name__)

VLAN_ENDPOINT = f"{API_PATH_V1}/vlan"
SAVE_ENDPOINT = f"{API_PATH_V1}/system/save"
DEFAULT_VLAN = 1

VlanTable = dict[int, dict[str, bool]]


class VlanMemberPayload(TypedDict):
    """One port membership as exchanged with ``/api/v1/vlan``."""

    Port: str
    Tagged: bool


class VlanEntryPayload(TypedDict):
    """One VLAN as exchanged with ``/api/v1/vlan``."""

    key: str
    val: list[VlanMemberPayload]


@dataclass
class PortMembership:
    """Desired VLAN membership of one port.

    Attributes:
        untagged: Native/access VLAN, or None for a pure trunk.
        tagged: VLANs carried tagged.
    """

    untagged: int | None = None
    tagged: Sequence[int] = ()


@dataclass
class VlanApplyResult:
    """Outcome of :meth:`QNAPRESTVLANManager.apply_port_membership`.

    Attributes:
        changed: Whether the switch differs from the desired table.
        created: VLAN IDs created.
        edited: VLAN IDs whose membership was written.
        deleted: VLAN IDs deleted.
        unmanaged_vlans: VLANs on the switch outside ``vlans`` that were kept.
        diff_before: Rendered table before the change.
        diff_after: Rendered table after the change.
        saved: Whether ``system/save`` ran.
    """

    changed: bool = False
    created: list[int] = field(default_factory=list)
    edited: list[int] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)
    unmanaged_vlans: list[int] = field(default_factory=list)
    diff_before: str = ""
    diff_after: str = ""
    saved: bool = False


def _port_key(port: str) -> tuple[int, int, str]:
    return (0, int(port), "") if port.isdigit() else (1, 0, port)


def _fmt_ports(ports: Sequence[str]) -> str:
    nums = sorted(int(p) for p in ports if p.isdigit())
    spans: list[list[int]] = []
    for n in nums:
        if spans and spans[-1][1] == n - 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    out = [str(a) if a == b else f"{a}-{b}" for a, b in spans]
    out += sorted(p for p in ports if not p.isdigit())
    return ",".join(out) or "-"


def render_table(table: VlanTable) -> str:
    """Render a VLAN table as one line per VLAN."""
    lines = []
    for vid in sorted(table):
        members = table[vid]
        tagged = [p for p, t in members.items() if t]
        untagged = [p for p, t in members.items() if not t]
        lines.append(f"VLAN {vid:4d}  tagged {_fmt_ports(tagged):<24} untagged {_fmt_ports(untagged)}")
    return "\n".join(lines) + "\n"


def parse_table(result: object) -> VlanTable:
    """Convert the ``result`` of ``GET /api/v1/vlan`` into a VLAN table.

    Raises:
        VLANError: The payload is not a list of VLAN entries.
    """
    if not isinstance(result, list):
        raise VLANError(f"Unexpected VLAN payload: {type(result).__name__}")
    table: VlanTable = {}
    for entry in result:
        members = entry.get("val") or []
        table[int(entry["key"])] = {str(m["Port"]): bool(m["Tagged"]) for m in members}
    return table


def desired_table(
    current: VlanTable,
    vlans: Sequence[int],
    ports: Mapping[str, PortMembership],
    purge_vlans: bool = False,
) -> VlanTable:
    """Compute the target table; declared ports are owned completely, others untouched.

    Raises:
        VLANError: A port references a VLAN outside ``vlans`` or lists a VLAN both tagged and untagged.
    """
    desired = {vid: dict(members) for vid, members in current.items()}
    for vid in vlans:
        desired.setdefault(vid, {})
    if purge_vlans:
        for vid in list(desired):
            if vid not in vlans and vid != DEFAULT_VLAN:
                del desired[vid]

    for port, cfg in ports.items():
        tagged = list(cfg.tagged)
        if cfg.untagged is not None and cfg.untagged in tagged:
            raise VLANError(f"Port {port}: VLAN {cfg.untagged} is both tagged and untagged")
        for vid in tagged + ([cfg.untagged] if cfg.untagged is not None else []):
            if vid not in desired:
                raise VLANError(f"Port {port} references VLAN {vid}, which is not in the VLAN list")
        for members in desired.values():
            members.pop(port, None)
        for vid in tagged:
            desired[vid][port] = True
        if cfg.untagged is not None:
            desired[cfg.untagged][port] = False
    return desired


class QNAPRESTVLANManager(BaseVLANManager):
    """VLAN operations via the QSS REST API (no SSH, no enable password)."""

    def __init__(self, transport: QNAPRESTTransport):
        self._transport = transport

    def read_table(self) -> VlanTable:
        """Read the current VLAN membership table."""
        return parse_table(self._transport.get(VLAN_ENDPOINT).get("result"))

    def list_vlans(self) -> list[VLAN]:
        """List all VLANs with their tagged and untagged ports."""
        vlans = []
        for vid, members in sorted(self.read_table().items()):
            ports = sorted(members, key=_port_key)
            vlans.append(
                VLAN(
                    vlan_id=vid,
                    tagged_ports=[p for p in ports if members[p]],
                    untagged_ports=[p for p in ports if not members[p]],
                )
            )
        return vlans

    def create_vlan(self, vlan_id: int, name: str = "") -> None:
        """Create a VLAN; QSS stores no VLAN names, ``name`` is ignored."""
        if name:
            logger.debug("QSS has no VLAN names, ignoring %r for VLAN %d", name, vlan_id)
        current = self.read_table()
        if vlan_id in current:
            return
        self.apply_port_membership([*current, vlan_id], {})

    def delete_vlan(self, vlan_id: int) -> None:
        """Delete a VLAN (VLAN 1 is refused)."""
        if vlan_id == DEFAULT_VLAN:
            raise VLANError("VLAN 1 cannot be deleted")
        current = self.read_table()
        if vlan_id not in current:
            return
        self.apply_port_membership([v for v in current if v != vlan_id], {}, purge_vlans=True)

    def assign_port_to_vlan(self, port: str, vlan_id: int, tagged: bool = False) -> None:
        """Add a port to a VLAN, keeping its other memberships.

        An untagged assignment replaces the port's previous untagged VLAN.
        """
        current = self.read_table()
        vlans = sorted({*current, vlan_id})
        untagged = next((v for v, m in current.items() if m.get(port) is False), None)
        tagged_in = [v for v, m in current.items() if m.get(port) is True and v != vlan_id]
        if tagged:
            membership = PortMembership(
                untagged=untagged if untagged != vlan_id else None, tagged=[*tagged_in, vlan_id]
            )
        else:
            membership = PortMembership(untagged=vlan_id, tagged=tagged_in)
        self.apply_port_membership(vlans, {port: membership})

    def configure_trunk(self, config: TrunkConfig) -> None:
        """Make the port untagged in ``native_vlan`` and tagged in the other allowed VLANs."""
        current = self.read_table()
        allowed = {*config.allowed_vlans, config.native_vlan}
        vlans = sorted({*current, *allowed})
        membership = PortMembership(
            untagged=config.native_vlan, tagged=sorted(v for v in allowed if v != config.native_vlan)
        )
        self.apply_port_membership(vlans, {config.port: membership})

    def apply_port_membership(
        self,
        vlans: Sequence[int],
        ports: Mapping[str, PortMembership],
        purge_vlans: bool = False,
        mgmt_vlan: int = DEFAULT_VLAN,
        protect_ports: Sequence[str] = (),
        save: bool = True,
        check_mode: bool = False,
    ) -> VlanApplyResult:
        """Reconcile VLAN membership declaratively.

        A declared port is owned completely: its memberships not listed are removed. Undeclared
        ports (e.g. LAGs) are untouched. One write per changed VLAN, then readback and ``system/save``.

        Args:
            vlans: VLAN IDs that must exist.
            ports: Port name as the switch reports it (e.g. ``"3"``) to its desired membership.
            purge_vlans: Delete VLANs not in ``vlans`` (VLAN 1 never).
            mgmt_vlan: VLAN carrying the management IP.
            protect_ports: Ports whose membership in ``mgmt_vlan`` must not change.
            save: Persist the config after a change.
            check_mode: Compute and return the diff without writing.

        Returns:
            What changed, with a rendered before/after diff.

        Raises:
            VLANError: Invalid input, a protected port would change, or the readback differs
                from what was written.
            APIError: The switch rejected a request.
        """
        current = self.read_table()
        desired = desired_table(current, vlans, ports, purge_vlans)

        for port in protect_ports:
            before = current.get(mgmt_vlan, {}).get(port)
            after = desired.get(mgmt_vlan, {}).get(port)
            if before != after:
                raise VLANError(
                    f"Refusing to change protected port {port} in mgmt VLAN {mgmt_vlan} ({before} -> {after})"
                )

        to_create = sorted(v for v in desired if v not in current)
        to_delete = sorted(v for v in current if v not in desired)
        to_edit = sorted(v for v in desired if v in current and desired[v] != current[v])
        result = VlanApplyResult(
            created=to_create,
            edited=[v for v in to_create if desired[v]] + to_edit,
            deleted=to_delete,
            unmanaged_vlans=sorted(v for v in current if v not in vlans and v in desired),
            diff_before=render_table(current),
            diff_after=render_table(desired),
        )
        if not (to_create or to_delete or to_edit):
            return result

        result.changed = True
        if check_mode:
            return result

        for vid in to_create:
            self._transport.post(VLAN_ENDPOINT, {"data": [{"key": str(vid), "val": []}]})
        for vid in result.edited:
            self._transport.put(VLAN_ENDPOINT, {"data": [self._entry(vid, desired[vid])]})
        if to_delete:
            self._transport.delete(VLAN_ENDPOINT, {"idx": [str(v) for v in to_delete]})

        readback = self.read_table()
        if readback != desired:
            raise VLANError(
                "Switch did not apply the VLAN table as written\n"
                f"wanted:\n{render_table(desired)}read back:\n{render_table(readback)}"
            )

        if save:
            self._save()
            result.saved = True
        return result

    def _save(self) -> None:
        try:
            self._transport.put(SAVE_ENDPOINT, {})
        except APIError as e:
            raise VLANError(f"VLAN table applied but system/save failed: {e}") from e

    @staticmethod
    def _entry(vid: int, members: Mapping[str, bool]) -> VlanEntryPayload:
        ordered = sorted(members.items(), key=lambda kv: _port_key(kv[0]))
        return VlanEntryPayload(key=str(vid), val=[VlanMemberPayload(Port=p, Tagged=t) for p, t in ordered])
