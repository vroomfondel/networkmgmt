"""Web UI session of Netgear Smart Managed Pro switches, used for static routes.

The firmware answers ``inetCidrRouteTable`` writes with ``commitFailed``, so static routes can only be
managed through the web UI. The UI allows at most four sessions (``agentHTTPMaxSessions``); always
call :meth:`NetgearWeb.close`.
"""

import ipaddress
import re
from dataclasses import dataclass

import requests

from networkmgmt.switchctrl.exceptions import SwitchError
from networkmgmt.switchctrl.vendors.netgear.models import StaticRoute

CSRF_RE = re.compile(r'NAME="CSRFToken"[^>]*VALUE="([^"]+)"', re.I)
ERR_RE = re.compile(r'NAME="err_(flag|msg)"[^>]*VALUE="([^"]*)"', re.I)
ROUTE_FIELD_RE = re.compile(r'NAME=(1\.\d+\.\d+)\.v_1_1_(\d+)\s+VALUE="([^"]*)"', re.I)
ROUTE_CHECKBOX_RE = re.compile(r'name="(1\.\d+\.\d+)\.(gecb\d+)"', re.I)
LOGIN_FORM = 'id="login-form"'
MAIN_PAGE = "/base/cheetah_login.html"
STATUS_PAGE = "/base/status.html"
ROUTE_PAGE = "/RouteConfig.html"
ROUTE_FIELDS = {1: "type", 2: "network", 3: "mask", 5: "next_hop", 8: "preference", 10: "description"}
ROUTE_FORM = {
    "submit_flag": "8",
    "submit_target": "RouteConfig.html",
    "err_flag": "0",
    "err_msg": "",
    "clazz_information": "RouteConfig.html",
}


class WebError(SwitchError):
    """The web UI refused a request or answered unexpectedly."""


@dataclass
class _RouteRow:
    row: str
    type: str | None
    dest: str | None
    next_hop: str
    checkbox: str | None
    preference: int | None
    description: str


class NetgearWeb:
    """Minimal client for the route configuration page of the web UI."""

    def __init__(self, host: str, password: str, timeout: float = 20.0, session: requests.Session | None = None):
        """Create the client; nothing is sent before :meth:`login`.

        Args:
            host: Switch address.
            password: Web UI password of the admin user.
            timeout: HTTP timeout in seconds.
            session: Optional preconfigured ``requests.Session``.
        """
        self.base = f"http://{host}"
        self.password = password
        self.timeout = timeout
        self.session = session or requests.Session()
        self.logged_in = False

    def get(self, path: str, referer: str = "/") -> str:
        """GET a page with the same-origin Referer the firmware insists on.

        Args:
            path: Page path.
            referer: Page that holds the link, else HTTP 403.

        Returns:
            The HTML.

        Raises:
            WebError: On non-200 or an expired session.
        """
        resp = self.session.get(self.base + path, headers={"Referer": self.base + referer}, timeout=self.timeout)
        if resp.status_code != 200:
            raise WebError(f"GET {path}: HTTP {resp.status_code}")
        if self.logged_in and LOGIN_FORM in resp.text:
            raise WebError(f"GET {path}: web session expired")
        return resp.text

    def post(self, page: str, action: str, data: dict[str, str]) -> None:
        """POST a form; ``page`` is the Referer, ``action`` the target.

        A dropped connection is ignored (seen when adding an already existing route); callers verify by
        reading back.

        Raises:
            WebError: On HTTP errors or an error flag in the answer.
        """
        try:
            resp = self.session.post(
                self.base + action,
                data=data,
                headers={"Referer": self.base + page, "Origin": self.base},
                timeout=self.timeout,
            )
        except requests.ConnectionError, requests.exceptions.ChunkedEncodingError:
            return
        if resp.status_code not in (200, 204):
            raise WebError(f"POST {action}: HTTP {resp.status_code}")
        err = dict(ERR_RE.findall(resp.text))
        if err.get("flag", "0") != "0" or err.get("msg"):
            raise WebError(f"POST {action}: {err.get('msg') or 'error flag set'}")

    @staticmethod
    def _csrf(html: str, what: str) -> str:
        m = CSRF_RE.search(html)
        if not m:
            raise WebError(f"{what}: no CSRF token")
        return m.group(1)

    def login(self) -> None:
        """Log in.

        Raises:
            WebError: If the password is wrong or all HTTP sessions are in use.
        """
        token = self._csrf(self.get("/"), "login page")
        resp = self.session.post(
            self.base + MAIN_PAGE,
            data={"CSRFToken": token, "pwd": self.password, "err_flag": "0", "err_msg": "", "submt": ""},
            headers={"Referer": self.base + "/"},
            timeout=self.timeout,
        )
        if "SID" not in self.session.cookies or LOGIN_FORM in resp.text:
            raise WebError("web login refused: wrong password, or all HTTP sessions (agentHTTPMaxSessions) in use")
        self.logged_in = True

    def close(self) -> None:
        """Log out; never raises."""
        if not self.logged_in:
            return
        try:
            token = self._csrf(self.get(STATUS_PAGE, referer=MAIN_PAGE), "logout")
            self.post(STATUS_PAGE, STATUS_PAGE, {"CSRFToken": token, "sessionID": ""})
        except WebError, requests.RequestException:
            pass
        self.logged_in = False

    def _route_page(self) -> tuple[str, list[_RouteRow]]:
        html = self.get(ROUTE_PAGE)
        rows: dict[str, dict[str, str]] = {}
        for row, field, value in ROUTE_FIELD_RE.findall(html):
            if int(field) in ROUTE_FIELDS:
                rows.setdefault(row, {})[ROUTE_FIELDS[int(field)]] = value
        for row, checkbox in ROUTE_CHECKBOX_RE.findall(html):
            rows.setdefault(row, {})["checkbox"] = checkbox
        routes: list[_RouteRow] = []
        for row, r in sorted(rows.items()):
            dest: str | None
            if r.get("type") == "Static":
                dest = str(ipaddress.IPv4Network(f"{r['network']}/{r['mask']}"))
            elif r.get("type") == "Default":
                dest = "0.0.0.0/0"
            else:
                dest = None
            pref = r.get("preference", "")
            routes.append(
                _RouteRow(
                    row=row,
                    type=r.get("type"),
                    dest=dest,
                    next_hop=r.get("next_hop", ""),
                    checkbox=r.get("checkbox"),
                    preference=int(pref) if pref.isdigit() else None,
                    description=r.get("description", ""),
                )
            )
        return self._csrf(html, "route page"), routes

    def read_static_routes(self) -> list[StaticRoute]:
        """Return the configured static and default routes (connected/dynamic rows are skipped)."""
        return [
            StaticRoute(dest=r.dest, next_hop=r.next_hop, preference=r.preference, description=r.description)
            for r in self._route_page()[1]
            if r.dest
        ]

    def add_static_route(self, dest: str, next_hop: str, preference: int = 1, description: str = "") -> None:
        """Add a static route; ``0.0.0.0/0`` creates the default route."""
        net = ipaddress.IPv4Network(dest)
        token, _ = self._route_page()
        if net.prefixlen == 0:
            target = {"v_g_1_1_11": "0.0.0.0", "v_g_1_1_12": "0.0.0.0"}
        else:
            target = {"v_g_1_1_2": str(net.network_address), "v_g_1_1_3": str(net.netmask)}
        data = {
            "CSRFToken": token,
            "v_g_1_1_1": "Default" if net.prefixlen == 0 else "Static",
            "v_g_1_1_5": next_hop,
            "v_g_1_1_8": str(preference),
            "v_g_1_1_9": "Add",
            "v_g_1_1_10": description,
            "v_4_1_1": "ADD",
        }
        self.post(ROUTE_PAGE, ROUTE_PAGE + "/a1", {**data, **target, **ROUTE_FORM})

    def delete_static_route(self, dest: str, next_hop: str) -> None:
        """Delete a static route; unknown routes are ignored."""
        token, routes = self._route_page()
        r = next((r for r in routes if r.dest == dest and r.next_hop == next_hop), None)
        if r is None:
            return
        net, p = ipaddress.IPv4Network(dest), r.row
        data = {
            "CSRFToken": token,
            f"{p}.{r.checkbox}": "on",
            f"{p}.v_1_1_1": r.type or "",
            f"{p}.v_1_1_5": next_hop,
            f"{p}.v_1_1_8": str(r.preference),
            f"{p}.v_1_1_9": "Delete",
            "v_4_3_1": "DELETE",
        }
        if net.prefixlen == 0:
            data.update({f"{p}.v_1_1_11": "0.0.0.0", f"{p}.v_1_1_12": "0.0.0.0"})
        else:
            data.update({f"{p}.v_1_1_2": str(net.network_address), f"{p}.v_1_1_3": str(net.netmask)})
        self.post(ROUTE_PAGE, ROUTE_PAGE + "/a1", {**data, **ROUTE_FORM})
