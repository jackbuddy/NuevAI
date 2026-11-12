#!/usr/bin/env python
"""ONE-TIME, LOCAL helper: get the OAuth refresh token the CI job uses to read Sheets/Docs shared with the ingest Gmail account.

    GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... python scripts/google_auth_setup.py
    python scripts/google_auth_setup.py            # prompts for the client id and (hidden) secret instead
    python scripts/google_auth_setup.py --no-browser   # prints the sign-in URL instead of opening a browser

What it does (installed-app / loopback flow with PKCE): starts a tiny web server on 127.0.0.1, opens Google's consent page, you sign in AS the ingest
account (nuevai.updates@gmail.com) and approve READ-ONLY access to Drive and Sheets, Google redirects back to 127.0.0.1, the script exchanges the code and
prints ONLY the refresh token, once. Nothing is written to disk or to the repo; the client secret and the access token are never printed.
Then add the three values as GitHub repository secrets: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN.

IMPORTANT: for an External OAuth app whose publishing status is 'Testing', Google expires refresh tokens after 7 DAYS. Set the consent screen to
'In production' (Google Cloud Console > APIs & Services > OAuth consent screen > Publish app; it may stay unverified, you will just see an
'unverified app' warning once) BEFORE running this script, or the Action turns red a week later with "refresh token expired".
"""
import argparse
import base64
import getpass
import hashlib
import http.server
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests   # noqa: E402

from pipeline import gapi   # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(gapi.SCOPES), "access_type": "offline",
         "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, verifier: str, session=None) -> str:
    """Authorization code -> refresh token. Raises SystemExit with a plain message on any problem (never echoing secrets)."""
    s = session or requests
    r = s.post(gapi.TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": redirect_uri,
                                     "grant_type": "authorization_code", "code_verifier": verifier}, timeout=30)
    if r.status_code != 200:
        reason, _ = gapi._err_fields(r)
        raise SystemExit(f"Google refused the code exchange ({reason or 'HTTP ' + str(r.status_code)}). Check the client id/secret and that the OAuth client type is 'Desktop app'.")
    j = r.json()
    rt = j.get("refresh_token")
    if not rt:
        raise SystemExit("Google returned no refresh token. Remove this app's access at https://myaccount.google.com/permissions (signed in as the ingest account) and run the script again.")
    got = set((j.get("scope") or "").split())
    missing = [x for x in gapi.SCOPES if x not in got]
    if missing:
        raise SystemExit("The consent did not include all read-only scopes (Drive + Sheets). Run the script again and tick every permission.")
    return rt


class _Handler(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in q or "error" in q:
            type(self).result.update({k: v[0] for k, v in q.items()})
            body = b"<html><body><h3>Done. You can close this tab and go back to the terminal.</h3></body></html>"
        else:
            body = b"<html><body>waiting</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):          # no request logging: the redirect URL carries the one-time code
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--timeout", type=int, default=300, help="seconds to wait for the browser sign-in (default 300)")
    a = ap.parse_args()
    cid = (os.getenv("GOOGLE_OAUTH_CLIENT_ID") or input("OAuth client id: ")).strip()
    csec = (os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or getpass.getpass("OAuth client secret (hidden): ")).strip()
    if not cid or not csec:
        print("client id and secret are required")
        return 2
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    state, (verifier, challenge) = secrets.token_urlsafe(24), pkce_pair()
    _Handler.result = {}
    url = build_auth_url(cid, redirect, state, challenge)
    print("Sign in AS the ingest Gmail account and approve read-only access to Drive and Sheets.")
    if a.no_browser or not webbrowser.open(url):
        print("Open this URL in your browser:\n" + url)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    for _ in range(a.timeout * 5):
        if _Handler.result:
            break
        threading.Event().wait(0.2)
    srv.shutdown()
    res = _Handler.result
    if not res:
        print("timed out waiting for the sign-in")
        return 1
    if "error" in res:
        print(f"Google reported an error: {res['error']}")
        return 1
    if res.get("state") != state:
        print("state mismatch: aborting (the response did not come from this run)")
        return 1
    try:
        rt = exchange_code(cid, csec, res["code"], redirect, verifier)
    except SystemExit as e:
        print(str(e))
        return 1
    print("\nREFRESH TOKEN (shown once; put it in the GitHub secret GOOGLE_OAUTH_REFRESH_TOKEN, do not save it in a file):\n")
    print(rt)
    print("\nAlso set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET. Reminder: the consent screen must be 'In production', not 'Testing' (tokens expire after 7 days otherwise).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
