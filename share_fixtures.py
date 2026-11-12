"""Offline stand-ins for Google's APIs, used by test_share_ingest.py (no network, no credentials, no real files).

  tab(...) / book(...)        build a spreadsheets.get()-shaped response (grid data, merges, hidden rows/columns, number formats)
  csv_tab(text, title)        the same from CSV text (e.g. the public team sheets the pipeline already downloads)
  FakeGoogle                  a requests.Session look-alike that answers the token endpoint, Drive files.list (sharedWithMe, folder children,
                              pagination), files.get, files.export and Sheets spreadsheets.get, enforcing bearer tokens and able to inject
                              401s, 429s, 5xx and an invalid_grant, so the REAL pipeline/gapi.py client code is what the tests exercise
"""
import csv
import io
import json
import re
from urllib.parse import unquote, urlparse

from pipeline import gapi

DOC_HTML = "<html><head><title>x</title></head><body>{}</body></html>"


def cell(text=None, number=None, fmt=None):
    c = {}
    if text is not None:
        c["formattedValue"] = text
        c["effectiveValue"] = {"stringValue": text}
    if number is not None:
        c["effectiveValue"] = {"numberValue": number}
        if fmt:
            c["effectiveFormat"] = {"numberFormat": {"type": fmt}}
    return c


def tab(title, rows, sheet_id=0, merges=None, hidden_rows=(), hidden_cols=(), hidden=False, start_row=0, start_col=0, filtered_rows=()):
    """rows: list of lists whose items are str, None, or a ready-made cell dict. merges: [(r0, r1, c0, c1)] (end exclusive, 0-based)."""
    row_data, row_meta = [], []
    for i, r in enumerate(rows):
        row_data.append({"values": [x if isinstance(x, dict) else (cell(x) if x not in (None, "") else {}) for x in r]})
        row_meta.append({"hiddenByUser": (start_row + i) in hidden_rows, "hiddenByFilter": (start_row + i) in filtered_rows})
    width = max((len(r) for r in rows), default=0)
    return {"properties": {"sheetId": sheet_id, "title": title, "hidden": hidden, "gridProperties": {"rowCount": len(rows) + 20, "columnCount": max(width, 10)}},
            "merges": [{"sheetId": sheet_id, "startRowIndex": a, "endRowIndex": b, "startColumnIndex": c, "endColumnIndex": d} for a, b, c, d in (merges or [])],
            "data": [{"startRow": start_row, "startColumn": start_col, "rowData": row_data, "rowMetadata": row_meta,
                      "columnMetadata": [{"hiddenByUser": (start_col + j) in hidden_cols} for j in range(width)]}]}


def csv_tab(text, title, sheet_id=0, **kw):
    return tab(title, [list(r) for r in csv.reader(io.StringIO(text))], sheet_id, **kw)


def book(*tabs, title="Workbook"):
    return {"properties": {"title": title, "timeZone": "America/Los_Angeles"}, "sheets": list(tabs)}


class Resp:
    def __init__(self, status=200, body=None, text=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.content = self.text.encode()

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def err(status, reason, message="x"):
    return Resp(status, {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}})


class FakeGoogle:
    """files: {id: {name, mimeType, modifiedTime, version, owner, parents, trashed, shared (bool), book (for sheets), html (for docs)}}."""

    def __init__(self, files=None, page_size=None):
        self.files = files or {}
        self.requests = []                 # (method, path, params) of every call
        self.tokens_issued = 0
        self.refresh_fail = None           # e.g. "invalid_grant"
        self.fail_next = []                # [(match substring, Resp)] consumed in order
        self.revoke_after_tokens = None
        self.page_size = page_size
        self.valid = set()

    # a requests.Session look-alike
    def request(self, method, url, params=None, data=None, headers=None, timeout=None):
        u = urlparse(url)
        path = u.path
        self.requests.append((method, path, dict(params or {})))
        if u.netloc == "oauth2.googleapis.com":
            if self.refresh_fail:
                return Resp(400, {"error": self.refresh_fail, "error_description": "Token has been expired or revoked."})
            self.tokens_issued += 1
            tok = f"tok{self.tokens_issued}"
            self.valid.add(tok)
            return Resp(200, {"access_token": tok, "expires_in": 3600, "token_type": "Bearer"})
        auth = (headers or {}).get("Authorization", "")
        if auth.removeprefix("Bearer ") not in self.valid:
            return err(401, "authError")
        for i, (sub, resp) in enumerate(self.fail_next):
            if sub in path or sub in json.dumps(params or {}):
                self.fail_next.pop(i)
                if resp.status_code == 401:
                    self.valid.clear()
                return resp
        if path == "/drive/v3/files":
            return self._list(params or {})
        m = re.fullmatch(r"/drive/v3/files/([^/]+)/export", path)
        if m:
            f = self.files.get(unquote(m.group(1)))
            if not f or f.get("mimeType") != gapi.MIME_DOC:
                return err(404, "notFound")
            return Resp(200, text=f["html"])
        m = re.fullmatch(r"/drive/v3/files/([^/]+)", path)
        if m:
            f = self.files.get(unquote(m.group(1)))
            return Resp(200, self._meta(unquote(m.group(1)), f)) if f else err(404, "notFound")
        m = re.fullmatch(r"/v4/spreadsheets/([^/]+)", path)
        if m:
            f = self.files.get(unquote(m.group(1)))
            if not f or f.get("mimeType") != gapi.MIME_SHEET:
                return err(404, "notFound")
            return self._sheet(f["book"], params or {})
        return err(404, "notFound")

    @staticmethod
    def _meta(fid, f):
        return {"id": fid, "name": f["name"], "mimeType": f["mimeType"], "modifiedTime": f.get("modifiedTime", "2026-11-10T18:00:00.000Z"),
                "version": str(f.get("version", 1)), "owners": [{"emailAddress": f.get("owner", "teacher@example.org")}], "parents": f.get("parents", []),
                "trashed": bool(f.get("trashed"))}

    def _list(self, p):
        q = p.get("q", "")
        out = []
        for fid, f in self.files.items():
            if f.get("trashed") and "trashed = false" in q:
                continue
            if "sharedWithMe = true" in q:
                if not f.get("shared"):
                    continue
            else:
                m = re.search(r"'([^']+)' in parents", q)
                if not m or m.group(1) not in f.get("parents", []):
                    continue
            types = re.findall(r"mimeType = '([^']+)'", q)
            if types and f["mimeType"] not in types:
                continue
            out.append(self._meta(fid, f))
        out.sort(key=lambda x: x["modifiedTime"], reverse=True)
        size = self.page_size or int(p.get("pageSize", 100))
        start = int(p.get("pageToken") or 0)
        body = {"files": out[start:start + size]}
        if start + size < len(out):
            body["nextPageToken"] = str(start + size)
        return Resp(200, body)

    @staticmethod
    def _sheet(bk, p):
        if "ranges" not in p:
            return Resp(200, {"properties": bk.get("properties", {}),
                              "sheets": [{"properties": s["properties"]} for s in bk["sheets"]]})
        m = re.match(r"'((?:[^']|'')*)'!", p["ranges"])
        title = m.group(1).replace("''", "'") if m else ""
        for s in bk["sheets"]:
            if s["properties"]["title"] == title:
                return Resp(200, {"sheets": [s]})
        return err(400, "badRequest")


def sheet_file(name, bk, modified="2026-11-10T18:00:00.000Z", shared=True, **kw):
    return {"name": name, "mimeType": gapi.MIME_SHEET, "modifiedTime": modified, "book": bk, "shared": shared, **kw}


def doc_file(name, html_body, modified="2026-11-10T18:00:00.000Z", shared=True, **kw):
    return {"name": name, "mimeType": gapi.MIME_DOC, "modifiedTime": modified, "html": DOC_HTML.format(html_body), "shared": shared, **kw}


def folder_file(name, shared=True, **kw):
    return {"name": name, "mimeType": gapi.MIME_FOLDER, "modifiedTime": "2026-11-01T00:00:00.000Z", "shared": shared, **kw}
