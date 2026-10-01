"""One-time: trade a Google sign-in for an AdMob refresh token.

Run on your own machine, once. It opens a browser, you approve read-only
access to AdMob, and it prints a refresh token that GitHub Actions can use
unattended thereafter.

    python scripts/admob_consent.py <client-id> <client-secret>

Why this exists at all: the AdMob API refuses service accounts, because an
AdMob account belongs to a Google user rather than a Cloud project. A stored
refresh token is the only way to read it from CI.

The token is printed and nothing else. It is not written to disk, not copied
anywhere, and not sent to anything but Google — put it straight into a GitHub
secret and close the window. Anyone holding it can read your AdMob earnings
until you revoke it at myaccount.google.com/permissions.
"""

import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import requests

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/admob.readonly"
PORT = 8723                      # loopback only; Google allows any port here


class Catch(http.server.BaseHTTPRequestHandler):
    """Receives the one redirect Google makes back to this machine."""

    result = {}

    def do_GET(self):
        Catch.result = dict(urllib.parse.parse_qsl(
            urllib.parse.urlparse(self.path).query))
        ok = "code" in Catch.result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<h2>Done - you can close this tab.</h2>" if ok
            else b"<h2>No authorisation code came back.</h2>")

    def log_message(self, *_):
        pass                     # the default logger would print the code


def main(client_id, client_secret):
    # Guards against a stray request to the loopback port being mistaken for
    # Google's redirect.
    state = secrets.token_urlsafe(16)
    redirect = f"http://localhost:{PORT}"

    server = http.server.HTTPServer(("localhost", PORT), Catch)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        # Without both of these Google returns an access token only, and the
        # whole point here is the refresh token.
        "access_type": "offline",
        "prompt": "consent",
    })
    print("Opening your browser to approve read-only AdMob access...")
    print(f"If it does not open, visit:\n  {url}\n")
    webbrowser.open(url)

    thread.join(timeout=300)
    if not Catch.result:
        sys.exit("Timed out waiting for the browser to come back.")

    if Catch.result.get("state") != state:
        sys.exit("State did not match — ignoring this response.")
    code = Catch.result.get("code")
    if not code:
        sys.exit(f"No code returned: {Catch.result.get('error', 'unknown')}")

    r = requests.post(TOKEN, timeout=30, data={
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    })
    if r.status_code != 200:
        sys.exit(f"Token exchange failed ({r.status_code}): {r.text[:200]}")

    refresh = r.json().get("refresh_token")
    if not refresh:
        sys.exit("No refresh token came back. Google issues one only on first "
                 "consent — revoke the app at myaccount.google.com/permissions "
                 "and run this again.")

    print("\nRefresh token (store it; do not paste it anywhere else):\n")
    print(refresh)
    print("\nThen set the four secrets, pasting each value when prompted:")
    for name in ("ADMOB_REFRESH_TOKEN", "ADMOB_CLIENT_ID",
                 "ADMOB_CLIENT_SECRET", "ADMOB_PUBLISHER_ID"):
        print(f"  gh secret set {name} --repo Compo-CF/app-portfolio")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: python scripts/admob_consent.py <client-id> <client-secret>")
    main(sys.argv[1], sys.argv[2])
