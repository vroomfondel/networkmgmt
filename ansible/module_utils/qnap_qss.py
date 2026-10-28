import base64

try:
    import requests
    import urllib3

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class QssError(Exception):
    pass


class QssClient:
    def __init__(self, host, port, validate_certs, timeout):
        self.base = "https://%s:%d/api/v1" % (host, port)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = validate_certs
        if not validate_certs:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _call(self, method, path, **kwargs):
        resp = self.session.request(method, "%s/%s" % (self.base, path), timeout=self.timeout, **kwargs)
        if resp.status_code == 200 and not resp.content:
            return None
        try:
            body = resp.json()
        except ValueError:
            raise QssError("%s %s: HTTP %d, no JSON" % (method, path, resp.status_code))
        if resp.status_code != 200 or body.get("error_code") != 200:
            raise QssError(
                "%s %s: HTTP %d, error_code %s: %s"
                % (method, path, resp.status_code, body.get("error_code"), body.get("error_message"))
            )
        return body.get("result")

    def login(self, username, password):
        pw_b64 = base64.b64encode(password.encode()).decode()
        token = self._call("POST", "users/login", json={"username": username, "password": pw_b64})
        if not token:
            raise QssError("login returned no token")
        self.session.headers["Authorization"] = "Bearer " + token

    def logout(self):
        try:
            self._call("POST", "users/exit", json={})
        except (QssError, requests.RequestException):
            pass

    def get(self, path, **kwargs):
        return self._call("GET", path, **kwargs)

    def post(self, path, data):
        return self._call("POST", path, json=data)

    def put(self, path, data):
        return self._call("PUT", path, json=data)

    def delete(self, path, data):
        return self._call("DELETE", path, json=data)
