import ipaddress
import re

try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

CSRF_RE = re.compile(r'NAME="CSRFToken"[^>]*VALUE="([^"]+)"', re.I)
ERR_RE = re.compile(r'NAME="err_(flag|msg)"[^>]*VALUE="([^"]*)"', re.I)
ROUTE_FIELD_RE = re.compile(r'NAME=(1\.\d+\.\d+)\.v_1_1_(\d+)\s+VALUE="([^"]*)"', re.I)
ROUTE_CHECKBOX_RE = re.compile(r'name="(1\.\d+\.\d+)\.(gecb\d+)"', re.I)
LOGIN_FORM = 'id="login-form"'
MAIN_PAGE = "/base/cheetah_login.html"
STATUS_PAGE = "/base/status.html"
ROUTE_PAGE = "/RouteConfig.html"
ROUTE_FIELDS = {1: "type", 2: "network", 3: "mask", 5: "next_hop", 8: "preference", 10: "description"}
ROUTE_FORM = {"submit_flag": "8", "submit_target": "RouteConfig.html", "err_flag": "0", "err_msg": "", "clazz_information": "RouteConfig.html"}


class WebError(Exception):
    pass


class NetgearWeb:
    def __init__(self, host, password, timeout=20):
        self.base = "http://%s" % host
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.logged_in = False

    # every request needs a same-origin Referer, a POST the page that holds the form, else HTTP 403
    def get(self, path, referer="/"):
        resp = self.session.get(self.base + path, headers={"Referer": self.base + referer}, timeout=self.timeout)
        if resp.status_code != 200:
            raise WebError("GET %s: HTTP %d" % (path, resp.status_code))
        if self.logged_in and LOGIN_FORM in resp.text:
            raise WebError("GET %s: web session expired" % path)
        return resp.text

    def post(self, page, action, data):
        try:
            resp = self.session.post(
                self.base + action, data=data, headers={"Referer": self.base + page, "Origin": self.base}, timeout=self.timeout
            )
        except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError):
            # seen once when adding an already existing route; callers verify by reading back
            return
        if resp.status_code not in (200, 204):
            raise WebError("POST %s: HTTP %d" % (action, resp.status_code))
        err = dict(ERR_RE.findall(resp.text))
        if err.get("flag", "0") != "0" or err.get("msg"):
            raise WebError("POST %s: %s" % (action, err.get("msg") or "error flag set"))

    @staticmethod
    def _csrf(html, what):
        m = CSRF_RE.search(html)
        if not m:
            raise WebError("%s: no CSRF token" % what)
        return m.group(1)

    def login(self):
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

    def close(self):
        if not self.logged_in:
            return
        try:
            token = self._csrf(self.get(STATUS_PAGE, referer=MAIN_PAGE), "logout")
            self.post(STATUS_PAGE, STATUS_PAGE, {"CSRFToken": token, "sessionID": ""})
        except (WebError, requests.RequestException):
            pass
        self.logged_in = False

    def _route_page(self):
        html = self.get(ROUTE_PAGE)
        rows = {}
        for row, field, value in ROUTE_FIELD_RE.findall(html):
            if int(field) in ROUTE_FIELDS:
                rows.setdefault(row, {})[ROUTE_FIELDS[int(field)]] = value
        for row, checkbox in ROUTE_CHECKBOX_RE.findall(html):
            rows.setdefault(row, {})["checkbox"] = checkbox
        routes = []
        for row, r in sorted(rows.items()):
            route = dict(row=row, type=r.get("type"), next_hop=r.get("next_hop"), checkbox=r.get("checkbox"), description=r.get("description", ""))
            route["preference"] = int(r["preference"]) if r.get("preference", "").isdigit() else None
            if r.get("type") == "Static":
                route["dest"] = str(ipaddress.IPv4Network("%s/%s" % (r["network"], r["mask"])))
            elif r.get("type") == "Default":
                route["dest"] = "0.0.0.0/0"
            else:
                route["dest"] = None
            routes.append(route)
        return self._csrf(html, "route page"), routes

    def read_static_routes(self):
        return [r for r in self._route_page()[1] if r["dest"]]

    def add_static_route(self, dest, next_hop, preference, description):
        net = ipaddress.IPv4Network(dest)
        token, _ = self._route_page()
        # the default route is its own type in the form, without network and mask
        target = {"v_g_1_1_11": "0.0.0.0", "v_g_1_1_12": "0.0.0.0"} if net.prefixlen == 0 else {"v_g_1_1_2": str(net.network_address), "v_g_1_1_3": str(net.netmask)}
        data = {
            "CSRFToken": token,
            "v_g_1_1_1": "Default" if net.prefixlen == 0 else "Static",
            "v_g_1_1_5": next_hop,
            "v_g_1_1_8": str(preference),
            "v_g_1_1_9": "Add",
            "v_g_1_1_10": description,
            "v_4_1_1": "ADD",
        }
        self.post(ROUTE_PAGE, ROUTE_PAGE + "/a1", dict(data, **dict(target, **ROUTE_FORM)))

    def delete_static_route(self, dest, next_hop):
        token, routes = self._route_page()
        r = next((r for r in routes if r["dest"] == dest and r["next_hop"] == next_hop), None)
        if r is None:
            return
        net, p = ipaddress.IPv4Network(dest), r["row"]
        data = {
            "CSRFToken": token,
            "%s.%s" % (p, r["checkbox"]): "on",
            "%s.v_1_1_1" % p: r["type"],
            "%s.v_1_1_5" % p: next_hop,
            "%s.v_1_1_8" % p: str(r["preference"]),
            "%s.v_1_1_9" % p: "Delete",
            "v_4_3_1": "DELETE",
        }
        if net.prefixlen == 0:
            data.update({"%s.v_1_1_11" % p: "0.0.0.0", "%s.v_1_1_12" % p: "0.0.0.0"})
        else:
            data.update({"%s.v_1_1_2" % p: str(net.network_address), "%s.v_1_1_3" % p: str(net.netmask)})
        self.post(ROUTE_PAGE, ROUTE_PAGE + "/a1", dict(data, **ROUTE_FORM))
