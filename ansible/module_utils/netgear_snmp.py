import asyncio
import socket
import time

try:
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData,
        ContextData,
        Integer,
        ObjectIdentity,
        ObjectType,
        OctetString,
        SnmpEngine,
        UdpTransportTarget,
        Unsigned32,
        UsmUserData,
        bulk_walk_cmd,
        get_cmd,
        set_cmd,
        usmAesCfb128Protocol,
        usmDESPrivProtocol,
        usmHMACMD5AuthProtocol,
        usmHMACSHAAuthProtocol,
        usmHMAC192SHA256AuthProtocol,
        usmHMAC384SHA512AuthProtocol,
    )

    HAS_PYSNMP = True
except ImportError:
    HAS_PYSNMP = False

CONNECTION_ARGS = dict(
    host=dict(type="str", required=True),
    port=dict(type="int", default=161),
    timeout=dict(type="float", default=5.0),
    retries=dict(type="int", default=1),
    community=dict(type="str", no_log=True),
    snmp_user=dict(type="str"),
    auth_key=dict(type="str", no_log=True),
    auth_protocol=dict(type="str", default="sha512", choices=["md5", "sha", "sha256", "sha512"]),
    priv_key=dict(type="str", no_log=True),
    priv_protocol=dict(type="str", default="aes128", choices=["des", "aes128"]),
)
CONNECTION_CONSTRAINTS = dict(
    required_one_of=[["community", "snmp_user"]],
    required_by={"snmp_user": "auth_key"},
)


SAVE_CONFIG = "1.3.6.1.4.1.4526.11.1.3.1.0"
SAVE_STATUS = "1.3.6.1.4.1.4526.11.1.3.11.0"
SAVE_COMPLETE, SAVE_FAILED = 3, 4


class SnmpError(Exception):
    pass


def portlist_decode(octets):
    return {i * 8 + j + 1 for i, byte in enumerate(octets) for j in range(8) if byte & (0x80 >> j)}


def portlist_patch(octets_, length, bits):
    out = bytearray(octets_.ljust(length, b"\x00")[:length])
    for idx, on in bits.items():
        mask = 0x80 >> ((idx - 1) % 8)
        out[(idx - 1) // 8] = out[(idx - 1) // 8] | mask if on else out[(idx - 1) // 8] & ~mask
    return bytes(out)


def portlist_encode(indexes, length):
    out = bytearray(length)
    for idx in indexes:
        out[(idx - 1) // 8] |= 0x80 >> ((idx - 1) % 8)
    return bytes(out)


class SnmpSession:
    def __init__(self, params):
        self.p = params
        self.engine = SnmpEngine()
        self.auth = self._auth(params)
        self.target = None

    @staticmethod
    def _auth(p):
        if not p["snmp_user"]:
            return CommunityData(p["community"], mpModel=1)
        auth = {
            "md5": usmHMACMD5AuthProtocol,
            "sha": usmHMACSHAAuthProtocol,
            "sha256": usmHMAC192SHA256AuthProtocol,
            "sha512": usmHMAC384SHA512AuthProtocol,
        }[p["auth_protocol"]]
        kw = dict(authKey=p["auth_key"], authProtocol=auth)
        if p["priv_key"]:
            kw.update(privKey=p["priv_key"], privProtocol={"des": usmDESPrivProtocol, "aes128": usmAesCfb128Protocol}[p["priv_protocol"]])
        return UsmUserData(p["snmp_user"], **kw)

    async def open(self):
        self.target = await UdpTransportTarget.create((self.p["host"], self.p["port"]), timeout=self.p["timeout"], retries=self.p["retries"])

    def close(self):
        self.engine.close_dispatcher()

    async def walk(self, base, binary=False):
        rows = {}
        async for err_ind, err_stat, _, var_binds in bulk_walk_cmd(
            self.engine, self.auth, self.target, ContextData(), 0, 25, ObjectType(ObjectIdentity(base)), lexicographicMode=False
        ):
            if err_ind or err_stat:
                raise SnmpError("walk %s: %s" % (base, err_ind or err_stat.prettyPrint()))
            for oid, val in var_binds:
                rows[str(oid)[len(base) + 1 :]] = bytes(val.asOctets()) if binary else val.prettyPrint()
        return rows

    async def get(self, oid):
        err_ind, err_stat, _, var_binds = await get_cmd(self.engine, self.auth, self.target, ContextData(), ObjectType(ObjectIdentity(oid)))
        if err_ind or err_stat:
            raise SnmpError("get %s: %s" % (oid, err_ind or err_stat.prettyPrint()))
        return var_binds[0][1]

    async def set(self, *pairs):
        objs = [ObjectType(ObjectIdentity(oid), value) for oid, value in pairs]
        err_ind, err_stat, err_idx, _ = await set_cmd(self.engine, self.auth, self.target, ContextData(), *objs)
        if err_ind or err_stat:
            where = pairs[int(err_idx) - 1][0] if err_stat and int(err_idx) else pairs[0][0]
            raise SnmpError("set %s: %s" % (where, err_ind or err_stat.prettyPrint()))


def integer(value):
    return Integer(value)


def unsigned(value):
    return Unsigned32(value)


def octets(value):
    return OctetString(value)


async def save_config(session, timeout=60):
    await session.set((SAVE_CONFIG, Integer(1)))
    deadline = time.monotonic() + timeout
    while True:
        await asyncio.sleep(1)
        status = int(await session.get(SAVE_STATUS))
        if status == SAVE_COMPLETE:
            return
        if status == SAVE_FAILED or time.monotonic() > deadline:
            raise SnmpError("saving the configuration failed (agentSaveConfigStatus=%d)" % status)

VLAN_STATIC_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"
VLAN_STATIC_EGRESS = "1.3.6.1.2.1.17.7.1.4.3.1.2"
VLAN_STATIC_UNTAGGED = "1.3.6.1.2.1.17.7.1.4.3.1.4"
VLAN_CURRENT_EGRESS = "1.3.6.1.2.1.17.7.1.4.2.1.4"
VLAN_CURRENT_UNTAGGED = "1.3.6.1.2.1.17.7.1.4.2.1.5"


async def read_vlans(session):
    names = await session.walk(VLAN_STATIC_NAME)
    egress = await session.walk(VLAN_STATIC_EGRESS, binary=True)
    untagged = await session.walk(VLAN_STATIC_UNTAGGED, binary=True)
    # GS108T keeps factory VLANs out of the static table
    static = set(egress)
    for (tbl, base) in ((egress, VLAN_CURRENT_EGRESS), (untagged, VLAN_CURRENT_UNTAGGED)):
        for k, v in (await session.walk(base, binary=True)).items():
            vid = k.rsplit(".", 1)[1]
            if vid not in static:
                tbl[vid] = v
    vlans = {}
    for vid, octets_ in egress.items():
        members = portlist_decode(octets_)
        vlans[int(vid)] = dict(
            name=names.get(vid, ""),
            egress=members,
            untagged=portlist_decode(untagged.get(vid, b"")) & members,
            untagged_raw=untagged.get(vid, b""),
            static=vid in static,
        )
    length = len(next(iter(egress.values()))) if egress else 0
    return vlans, length


ROUTING_MODE = "1.3.6.1.4.1.4526.11.2.2.1.0"
IP_VLAN_IFINDEX = "1.3.6.1.4.1.4526.11.2.2.5.1.2"
IP_VLAN_STATUS = "1.3.6.1.4.1.4526.11.2.2.5.1.3"
IP_IF_ADDRESS = "1.3.6.1.4.1.4526.11.2.2.3.1.3"
IP_IF_NETMASK = "1.3.6.1.4.1.4526.11.2.2.3.1.4"
IP_IF_ROUTING = "1.3.6.1.4.1.4526.11.2.2.3.1.6"
IP_IF_MTU = "1.3.6.1.4.1.4526.11.2.2.3.1.8"
CIDR_ROUTE = "1.3.6.1.2.1.4.24.7.1"
CIDR_ROUTE_IFINDEX, CIDR_ROUTE_TYPE, CIDR_ROUTE_PROTO, CIDR_ROUTE_METRIC1, CIDR_ROUTE_STATUS = 7, 8, 9, 12, 17
PROTO_NETMGMT = 3


def mask_to_prefix(mask):
    return sum(bin(int(o)).count("1") for o in mask.split("."))


def prefix_to_mask(prefix):
    bits = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return ".".join(str((bits >> s) & 0xFF) for s in (24, 16, 8, 0))


def route_index(dest, prefix, next_hop):
    return "1.4.%s.%d.2.0.0.1.4.%s" % (dest, prefix, next_hop)


def parse_route_index(suffix):
    parts = [int(x) for x in suffix.split(".")]
    if parts[:2] != [1, 4]:
        return None
    dest = ".".join(map(str, parts[2:6]))
    prefix = parts[6]
    rest = parts[7:]
    rest = rest[1 + rest[0]:]
    if rest[:2] != [1, 4]:
        return None
    return dest, prefix, ".".join(map(str, rest[2:6]))


async def read_routing(session):
    mode = await session.walk(ROUTING_MODE.rsplit(".", 1)[0])
    vlan_ifindex = {int(k): int(v) for k, v in (await session.walk(IP_VLAN_IFINDEX)).items()}
    addr = await session.walk(IP_IF_ADDRESS)
    mask = await session.walk(IP_IF_NETMASK)
    rmode = await session.walk(IP_IF_ROUTING)
    mtu = await session.walk(IP_IF_MTU)
    interfaces = {}
    for vid, idx in sorted(vlan_ifindex.items()):
        k = str(idx)
        address = addr.get(k, "0.0.0.0")
        interfaces[vid] = dict(
            ifindex=idx,
            address=None if address == "0.0.0.0" else "%s/%d" % (address, mask_to_prefix(mask.get(k, "0.0.0.0"))),
            routing=rmode.get(k) == "1",
            mtu=int(mtu.get(k, "0")),
        )
    proto = await session.walk("%s.%d" % (CIDR_ROUTE, CIDR_ROUTE_PROTO))
    metric = await session.walk("%s.%d" % (CIDR_ROUTE, CIDR_ROUTE_METRIC1))
    routes = []
    for suffix, p in proto.items():
        parsed = parse_route_index(suffix)
        if parsed:
            dest, prefix, nh = parsed
            routes.append(dict(dest="%s/%d" % (dest, prefix), next_hop=nh, proto=int(p), metric=int(metric.get(suffix, "0"))))
    return dict(enabled=mode.get("0") == "1", interfaces=interfaces, routes=sorted(routes, key=lambda r: (r["dest"], r["next_hop"])))


LOGIN_SESSION = "1.3.6.1.4.1.4526.11.1.2.1.1"
LOGIN_SESSION_USER, LOGIN_SESSION_TYPE, LOGIN_SESSION_INET = 2, 4, 9
LOGIN_TYPE_HTTP, LOGIN_TYPE_HTTPS = 4, 5
HTTP_MAX_SESSIONS = "1.3.6.1.4.1.4526.11.1.2.18.3.0"
HTTP_SOFT_TIMEOUT = "1.3.6.1.4.1.4526.11.1.2.18.5.0"


def _inet_text(raw):
    if len(raw) in (4, 16):
        return socket.inet_ntop(socket.AF_INET if len(raw) == 4 else socket.AF_INET6, raw)
    return raw.hex() or "?"


async def read_web_sessions(session):
    types = await session.walk("%s.%d" % (LOGIN_SESSION, LOGIN_SESSION_TYPE))
    users = await session.walk("%s.%d" % (LOGIN_SESSION, LOGIN_SESSION_USER))
    inet = await session.walk("%s.%d" % (LOGIN_SESSION, LOGIN_SESSION_INET), binary=True)
    return [
        dict(user=users.get(k, "?"), address=_inet_text(inet.get(k, b"")), type="https" if int(v) == LOGIN_TYPE_HTTPS else "http")
        for k, v in sorted(types.items())
        if int(v) in (LOGIN_TYPE_HTTP, LOGIN_TYPE_HTTPS)
    ]
