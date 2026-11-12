"""A small in-memory stand-in for the Supabase client, for the offline tests (test_share_ingest.py).

Tables are lists of dicts. Supports the calls the ingest stores and rag.py's calendar lookup make: select with 'alias:metadata->>key' columns,
eq / neq / in_ / like / ilike / gte / lte / or_ / range / limit / order, insert / upsert / update / delete. A table listed in `missing` raises
like a table that does not exist.
"""
import re


class FakeSB:
    def __init__(self, missing=()):
        self.tables, self.deleted, self.missing, self.writes = {}, [], set(missing), []

    def table(self, name):
        return _Q(self, name)


def _like(value, pat, ci):
    if value is None:
        return False
    rx = "^" + "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pat) + "$"
    return bool(re.match(rx, str(value), (re.I if ci else 0) | re.S))


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.flt, self.op, self.payload, self.cols = db, name, [], "select", None, "*"
        self.lo = self.hi = self.lim = self.order_by = None
        self.on_conflict = None

    @staticmethod
    def val(row, col):
        if col.startswith("metadata->>"):
            v = (row.get("metadata") or {}).get(col[len("metadata->>"):])
            return None if v is None else ("true" if v is True else "false" if v is False else str(v))
        return row.get(col)

    def select(self, cols="*", **kw): self.cols = cols; return self
    def eq(self, c, v): self.flt.append(lambda r: self.val(r, c) == (v if not isinstance(v, bool) else str(v).lower())); return self
    def neq(self, c, v): self.flt.append(lambda r: self.val(r, c) != v); return self
    def in_(self, c, vals): self.flt.append(lambda r: self.val(r, c) in list(vals)); return self
    def like(self, c, pat): self.flt.append(lambda r: _like(self.val(r, c), pat, False)); return self
    def ilike(self, c, pat): self.flt.append(lambda r: _like(self.val(r, c), pat, True)); return self
    def gte(self, c, v): self.flt.append(lambda r: self.val(r, c) is not None and str(self.val(r, c)) >= str(v)); return self
    def lte(self, c, v): self.flt.append(lambda r: self.val(r, c) is not None and str(self.val(r, c)) <= str(v)); return self

    def or_(self, expr):
        parts = [p.split(".", 2) for p in expr.split(",")]
        self.flt.append(lambda r: any(_like(self.val(r, c), v, True) for c, op, v in parts if op == "ilike"))
        return self

    def range(self, a, b): self.lo, self.hi = a, b; return self
    def limit(self, n): self.lim = n; return self
    def order(self, c, desc=False): self.order_by = (c, desc); return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def upsert(self, rows, on_conflict=None): self.op, self.payload, self.on_conflict = "upsert", rows, on_conflict; return self
    def delete(self): self.op = "delete"; return self
    def update(self, row): self.op, self.payload = "update", row; return self

    def execute(self):
        db = self.db
        if self.name in db.missing:
            raise RuntimeError(f'relation "{self.name}" does not exist')
        rows = db.tables.setdefault(self.name, [])
        hit = [r for r in rows if all(f(r) for f in self.flt)]
        res = type("R", (), {})()
        res.data = []
        if self.op == "insert":
            rows.append(dict(self.payload))
            db.writes.append((self.name, "insert"))
        elif self.op == "upsert":
            key = self.on_conflict or "chunk_id"
            for p in (self.payload if isinstance(self.payload, list) else [self.payload]):
                rows[:] = [r for r in rows if r.get(key) != p.get(key)]
                rows.append(dict(p))
            db.writes.append((self.name, "upsert"))
        elif self.op == "delete":
            db.deleted += [r.get("chunk_id") for r in hit]
            db.tables[self.name] = [r for r in rows if r not in hit]
            db.writes.append((self.name, "delete"))
        elif self.op == "update":
            for r in hit:
                r.update(self.payload)
            db.writes.append((self.name, "update"))
        else:
            if self.order_by:
                c, desc = self.order_by
                hit = sorted(hit, key=lambda r: str(self.val(r, c) or ""), reverse=desc)
            if self.lo is not None:
                hit = hit[self.lo:self.hi + 1]
            if self.lim is not None:
                hit = hit[:self.lim]
            out = []
            for r in hit:
                if self.cols.strip() == "*":
                    out.append(dict(r))
                    continue
                x = {}
                for part in self.cols.split(","):
                    part = part.strip()
                    alias, expr = part.split(":", 1) if ":" in part else ("", part)
                    x[alias or expr] = r.get("metadata") if expr == "metadata" else self.val(r, expr)
                    if not alias and expr in r:
                        x[expr] = r[expr]
                out.append(x)
            res.data = out
        return res


def fake_embed_upsert(table):
    def f(sb, batch):
        rows = sb.tables.setdefault(table, [])
        for c in batch:
            rows[:] = [r for r in rows if r["chunk_id"] != c["chunk_id"]]
            rows.append({**c, "embedding": [0.0]})
        return len(batch)
    return f
