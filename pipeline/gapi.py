"""Minimal Google Drive / Sheets client over plain HTTPS (requests), authenticating AS the ingest account with an OAuth2 refresh token.

Files shared with nuevai.updates@gmail.com can only be read by signing in as that account, so the owner does the installed-app consent
flow ONCE (scripts/google_auth_setup.py) and gives CI three secrets: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET,
GOOGLE_OAUTH_REFRESH_TOKEN. Scopes are read-only (drive.readonly, spreadsheets.readonly).

  * the access token is refreshed in-process (and again after a 401); no token, secret or response body is ever logged or put in an exception
  * 429 / 5xx / rate-limit 403s are retried with exponential backoff (Retry-After honoured) up to MAX_TRIES
  * GoogleAuthError is a SYSTEMIC failure (the run must go red with instructions); GoogleApiError is about one request / one file
"""
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE = "https://www.googleapis.com/drive/v3"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = ("https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/spreadsheets.readonly")
MIME_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_DOC = "application/vnd.google-apps.document"
MIME_FOLDER = "application/vnd.google-apps.folder"
MAX_TRIES = 5
MAX_FOLDERS = 200
MAX_FOLDER_DEPTH = 5
MAX_FILES = 2000
MAX_EXPORT_BYTES = 8_000_000

NOT_CONFIGURED = ("Google ingestion not configured: set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REFRESH_TOKEN "
                  "(GitHub Actions secrets or local environment variables; see README > Auto-ingestion). Skipping Sheets/Docs; email ingest is unaffected.")

REAUTH = ("Google refused the stored refresh token ({why}). Re-authorize: run `python scripts/google_auth_setup.py` on your machine, signed in as the "
          "ingest Gmail account, and put the new token in the GitHub secret GOOGLE_OAUTH_REFRESH_TOKEN. If this keeps happening every 7 days, the OAuth "
          "consent screen is still in 'Testing': publish the app ('In production') in Google Cloud Console > APIs & Services > OAuth consent screen.")


class GoogleAuthError(Exception):
    """Systemic: credentials are missing, revoked, expired or lack access. The run must fail so the Action turns red."""


class GoogleApiError(Exception):
    def __init__(self, message: str, status: int = 0, reason: str = ""):
        super().__init__(message)
        self.status, self.reason = status, reason


@dataclass
class GoogleConfig:
    client_id: str
    client_secret: str
    refresh_token: str

    def __repr__(self):                     # never let a stray print show a secret
        return "GoogleConfig(client_id=..., client_secret=..., refresh_token=...)"


def load_config(env=None) -> GoogleConfig | None:
    env = os.environ if env is None else env
    vals = [re.sub(r"\s+", "", (env.get(k) or "").strip().strip("\"'")) for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN")]
    return GoogleConfig(*vals) if all(vals) else None


def _err_fields(r) -> tuple[str, str]:
    """(reason, short message) from a Google error body, without echoing anything but the error code words."""
    try:
        j = r.json()
    except Exception:
        return "", ""
    e = j.get("error")
    if isinstance(e, dict):
        errs = e.get("errors") or []
        reason = (errs[0].get("reason") if errs and isinstance(errs[0], dict) else "") or e.get("status", "")
        return str(reason)[:60], re.sub(r"[^\w .,:'/-]", "", str(e.get("message", "")))[:120]
    if isinstance(e, str):
        return e[:60], re.sub(r"[^\w .,:'/-]", "", str(j.get("error_description", "")))[:120]
    return "", ""


class GoogleClient:
    def __init__(self, cfg: GoogleConfig, session=None, sleep=time.sleep, clock=time.time):
        self.cfg, self.s, self._sleep, self._clock = cfg, session or requests.Session(), sleep, clock
        self._token, self._expires = "", 0.0
        self.calls = 0

    # -- auth
    def access_token(self, force: bool = False) -> str:
        if self._token and not force and self._clock() < self._expires - 60:
            return self._token
        try:
            r = self.s.request("POST", TOKEN_URL, data={"client_id": self.cfg.client_id, "client_secret": self.cfg.client_secret,
                                                         "refresh_token": self.cfg.refresh_token, "grant_type": "refresh_token"}, timeout=30)
        except requests.RequestException as e:
            raise GoogleApiError(f"token endpoint unreachable ({type(e).__name__})") from None
        if r.status_code != 200:
            reason, _ = _err_fields(r)
            if r.status_code in (400, 401) or reason in ("invalid_grant", "invalid_client", "unauthorized_client", "access_denied"):
                raise GoogleAuthError(REAUTH.format(why=reason or f"HTTP {r.status_code}"))
            raise GoogleApiError(f"token endpoint HTTP {r.status_code}", r.status_code, reason)
        try:
            j = r.json()
            self._token = j["access_token"]
            self._expires = self._clock() + float(j.get("expires_in", 3600))
        except Exception:
            raise GoogleApiError("token endpoint returned an unreadable response") from None
        return self._token

    # -- requests
    def request(self, url: str, params: dict | None = None, raw: bool = False):
        refreshed = False
        for attempt in range(MAX_TRIES):
            token = self.access_token()
            self.calls += 1
            try:
                r = self.s.request("GET", url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60)
            except requests.RequestException as e:
                if attempt == MAX_TRIES - 1:
                    raise GoogleApiError(f"network error ({type(e).__name__})") from None
                self._sleep(min(2 ** attempt, 30))
                continue
            code = r.status_code
            if code == 401 and not refreshed:
                refreshed = True
                self.access_token(force=True)
                continue
            reason, msg = _err_fields(r) if code >= 400 else ("", "")
            if code == 401:
                raise GoogleAuthError(REAUTH.format(why="HTTP 401 after refreshing the access token"))
            if code == 403 and reason in ("accessNotConfigured", "SERVICE_DISABLED", "insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"):
                raise GoogleAuthError(f"Google API access problem ({reason}): enable the Drive API and the Sheets API in the Google Cloud project, and make sure "
                                      f"the refresh token was granted the read-only Drive and Sheets scopes (re-run scripts/google_auth_setup.py).")
            if code in (429, 500, 502, 503, 504) or (code == 403 and reason in ("rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED")):
                if attempt == MAX_TRIES - 1:
                    raise GoogleApiError(f"HTTP {code} after {MAX_TRIES} tries ({reason or 'rate limited / server error'})", code, reason)
                try:
                    wait = float(r.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    wait = min(2 ** attempt, 30)
                self._sleep(min(wait, 60))
                continue
            if code >= 400:
                raise GoogleApiError(f"HTTP {code} {reason}".strip(), code, reason)
            return r if raw else r.json()
        raise GoogleApiError("retries exhausted")

    # -- Drive
    def _list(self, q: str, fields: str) -> list[dict]:
        out, token = [], None
        while True:
            params = {"q": q, "fields": f"nextPageToken, files({fields})", "pageSize": 200, "supportsAllDrives": "true",
                      "includeItemsFromAllDrives": "true", "orderBy": "modifiedTime desc"}
            if token:
                params["pageToken"] = token
            j = self.request(f"{DRIVE}/files", params)
            out += j.get("files") or []
            token = j.get("nextPageToken")
            if not token or len(out) >= MAX_FILES:
                return out

    FIELDS = "id,name,mimeType,modifiedTime,version,owners(emailAddress),parents,trashed"
    TYPES = f"(mimeType = '{MIME_SHEET}' or mimeType = '{MIME_DOC}' or mimeType = '{MIME_FOLDER}')"

    def discover(self) -> tuple[list[dict], list[str]]:
        """Everything shared with the account: Sheets and Docs directly shared, plus the Sheets/Docs inside shared FOLDERS (recursively).
        -> (files, warnings). Folders themselves are not returned. Raises on a failed top-level listing (systemic)."""
        warnings, found, folders, seen_folders = [], {}, [], set()

        def take(f: dict, depth: int = 0):
            if f.get("trashed"):
                return
            if f.get("mimeType") == MIME_FOLDER:
                if f["id"] not in seen_folders and depth <= MAX_FOLDER_DEPTH:
                    seen_folders.add(f["id"])
                    folders.append((f["id"], depth))
            elif f.get("mimeType") in (MIME_SHEET, MIME_DOC):
                found.setdefault(f["id"], f)

        for f in self._list(f"sharedWithMe = true and trashed = false and {self.TYPES}", self.FIELDS):
            take(f)
        n = 0
        while folders and n < MAX_FOLDERS:
            fid, depth = folders.pop(0)
            n += 1
            try:
                kids = self._list(f"'{fid}' in parents and trashed = false and {self.TYPES}", self.FIELDS)
            except GoogleApiError as e:
                if e.status in (403, 404):
                    warnings.append(f"a shared folder could not be listed ({e.status}); skipped")
                    continue
                raise
            for k in kids:
                take(k, depth + 1)
        if folders:
            warnings.append(f"folder scan stopped at {MAX_FOLDERS} folders")
        return list(found.values()), warnings

    def get_file(self, file_id: str) -> dict:
        return self.request(f"{DRIVE}/files/{quote(file_id)}", {"fields": self.FIELDS, "supportsAllDrives": "true"})

    # -- Sheets
    def get_spreadsheet(self, file_id: str, max_tabs: int = 25, max_rows: int = 2000, max_cols: int = 40) -> tuple[dict, list[str]]:
        """-> (a spreadsheets.get()-shaped dict with grid data for every tab, warnings). One metadata call, then one grid call per tab."""
        from .gsheets import _col_letter
        meta = self.request(f"{SHEETS}/{quote(file_id)}", {"fields": "properties(title,timeZone),sheets(properties(sheetId,title,hidden,gridProperties(rowCount,columnCount)))"})
        warnings, sheets = [], []
        tabs = meta.get("sheets") or []
        if len(tabs) > max_tabs:
            warnings.append(f"only the first {max_tabs} of {len(tabs)} tabs were read")
        fields = ("sheets(properties(sheetId,title,hidden),merges,data(startRow,startColumn,rowData(values(formattedValue,effectiveValue,"
                  "effectiveFormat(numberFormat(type)))),rowMetadata(hiddenByUser,hiddenByFilter),columnMetadata(hiddenByUser)))")
        for t in tabs[:max_tabs]:
            p = t.get("properties") or {}
            if p.get("hidden"):
                sheets.append({"properties": p})
                continue
            gp = p.get("gridProperties") or {}
            rows, cols = int(gp.get("rowCount") or max_rows), int(gp.get("columnCount") or max_cols)
            if rows > max_rows or cols > max_cols:
                warnings.append(f"tab '{str(p.get('title'))[:30]}' truncated to {min(rows, max_rows)} rows x {min(cols, max_cols)} columns (sheet has {rows} x {cols})")
            title = str(p.get("title", "")).replace("'", "''")
            rng = f"'{title}'!A1:{_col_letter(min(cols, max_cols) - 1)}{min(rows, max_rows)}"
            j = self.request(f"{SHEETS}/{quote(file_id)}", {"ranges": rng, "includeGridData": "true", "fields": fields})
            got = (j.get("sheets") or [{}])[0]
            got.setdefault("properties", p)
            sheets.append(got)
        return {"properties": meta.get("properties") or {}, "sheets": sheets}, warnings

    # -- Docs
    def export_doc_html(self, file_id: str) -> str:
        r = self.request(f"{DRIVE}/files/{quote(file_id)}/export", {"mimeType": "text/html"}, raw=True)
        if len(r.content or b"") > MAX_EXPORT_BYTES:
            raise GoogleApiError("document export larger than 8 MB")
        return r.text
