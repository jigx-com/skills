"""Find a Jigx Management API token without ever storing a password.

Source order:
  1. JIGX_AUTH_TOKEN in the environment (a raw IdToken).
  2. ddcheck's own cache (~/.cache/ddcheck/token.json) while it is unexpired.
  3. The jigx_debug MCP's login (its state.json) while it is unexpired.
  4. A refresh of that login with the MCP's refresh token. The app client id is read from the
     installed @jigx/jigx-debug-mcp package at run time and is never stored here. The new token
     goes to ddcheck's cache; the MCP's state file is only ever read, never written.
Otherwise it exits and says how to log in: the jigx_debug `login_debug_user` tool, or
JIGX_AUTH_TOKEN.

The MCP login is sticky: it is whoever last logged in there, possibly for another customer.
`ddcheck whoami` prints the identity and API base in use (never the token).
"""
import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

CACHE_FILE = pathlib.Path.home() / ".cache/ddcheck/token.json"
DEFAULT_API = "https://us-east-1.api.jigx.com/v2.0"
REFRESH_SKEW_S = 5 * 60


def _jwt_claims(token):
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def _fresh(token):
    return bool(token) and _jwt_claims(token).get("exp", 0) - time.time() > REFRESH_SKEW_S


def mcp_state_path():
    """Same resolution as @jigx/jigx-debug-mcp dist/lib/paths.js."""
    if os.environ.get("JIGX_DEBUG_MCP_STATE_PATH", "").strip():
        return pathlib.Path(os.environ["JIGX_DEBUG_MCP_STATE_PATH"].strip())
    home = os.environ.get("JIGX_DEBUG_MCP_HOME", "").strip()
    if home:
        base = pathlib.Path(home)
    elif sys.platform == "darwin":
        base = pathlib.Path.home() / "Library/Application Support/jigx-debug-mcp"
    elif sys.platform == "win32":
        base = pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home() / "AppData/Roaming")) / "jigx-debug-mcp"
    else:
        base = pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local/state")) / "jigx-debug-mcp"
    return base / "state.json"


def read_mcp_state():
    """The MCP state, or {} when absent or unreadable. The file also holds captured events and
    a private key; only `auth` and `selectedOrganization` are used."""
    path = mcp_state_path()
    try:
        # raw_decode tolerates trailing garbage, which this file has been seen to carry.
        state, _ = json.JSONDecoder().raw_decode(path.read_text())
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def mcp_package_dir():
    if os.environ.get("JIGX_DEBUG_MCP_DIR"):
        return pathlib.Path(os.environ["JIGX_DEBUG_MCP_DIR"])
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    path = pathlib.Path(root) / "@jigx/jigx-debug-mcp"
    return path if path.exists() else None


def mcp_constants():
    """{clientId, region, regions: {regionId: apiBaseUrl}} from the installed MCP, or {}."""
    pkg = mcp_package_dir()
    f = pkg / "dist/lib/constants.js" if pkg else None
    if not f or not f.exists():
        return {}
    text = f.read_text()
    out = {"regions": dict(re.findall(r"regionId:\s*'([^']+)'[^}]*?apiBaseUrl:\s*'([^']+)'", text, re.S))}
    m = re.search(r"PROD_\w+\s*=\s*\{[^}]*?region:\s*'([^']+)'[^}]*?clientId:\s*'([^']+)'", text, re.S)
    if m:
        out["region"], out["clientId"] = m.group(1), m.group(2)
    return out


def _save_cache(cache):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache))
    os.chmod(CACHE_FILE, 0o600)


def _refresh(refresh_token):
    consts = mcp_constants()
    if not consts.get("clientId"):
        return None, "cannot refresh: @jigx/jigx-debug-mcp is not installed globally (npm root -g)"
    url = f"https://cognito-idp.{consts['region']}.amazonaws.com/"
    req = urllib.request.Request(url, method="POST", data=json.dumps({
        "AuthFlow": "REFRESH_TOKEN_AUTH", "ClientId": consts["clientId"],
        "AuthParameters": {"REFRESH_TOKEN": refresh_token}}).encode(),
        headers={"Content-Type": "application/x-amz-json-1.1",
                 "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read()).get("AuthenticationResult") or {}
    except urllib.error.HTTPError as e:
        return None, f"refresh failed: HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"refresh failed: {e.reason}"
    if result.get("RefreshToken") and result["RefreshToken"] != refresh_token:
        print("ddcheck: the login service rotated the refresh token; if jigx_debug tools start "
              "failing, run its login_debug_user again.", file=sys.stderr)
    return result.get("IdToken"), None


def token():
    """(token, source label). Exits with instructions when no login is available."""
    env = os.environ.get("JIGX_AUTH_TOKEN", "").strip()
    if env:
        return env, "JIGX_AUTH_TOKEN"
    cache = {}
    try:
        cache = json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        pass
    state = read_mcp_state()
    auth = state.get("auth") or {}
    user = auth.get("username")
    # The cache is only valid for the user the MCP is logged in as now.
    if _fresh(cache.get("idToken")) and cache.get("username") == user:
        return cache["idToken"], "ddcheck cache"
    if _fresh(auth.get("jwtToken")):
        return auth["jwtToken"], "jigx_debug MCP login"
    reason = "no jigx_debug MCP login found"
    if auth.get("refreshToken"):
        new, reason = _refresh(auth["refreshToken"])
        if new:
            _save_cache({"idToken": new, "username": user})
            return new, "jigx_debug MCP login (refreshed)"
    sys.exit(f"ddcheck: no Jigx login ({reason}). Log in with the jigx_debug MCP "
             "(login_debug_user), or export JIGX_AUTH_TOKEN=<raw IdToken>.")


def identity():
    auth = read_mcp_state().get("auth") or {}
    return auth.get("username")


def api_base(org):
    """JIGX_API_BASE, else the org's region per the MCP login and the MCP's region table,
    else the US region."""
    if os.environ.get("JIGX_API_BASE"):
        return os.environ["JIGX_API_BASE"].rstrip("/")
    state = read_mcp_state()
    region = None
    for o in (state.get("auth") or {}).get("organizations") or []:
        if o.get("organizationId") == org:
            region = o.get("regionId")
    sel = state.get("selectedOrganization") or {}
    if not region and sel.get("organizationId") == org:
        region = sel.get("regionId")
    if region:
        base = mcp_constants().get("regions", {}).get(region)
        if base:
            return base.rstrip("/")
        return f"https://{region}.api.jigx.com/v2.0"
    return DEFAULT_API
