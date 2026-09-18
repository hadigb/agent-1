"""SQLite-backed persistent store for scanned files, the code graph, endpoints,
the LLM analysis cache and token usage records.

Design notes
------------
* Parse results are cached per file *content hash*: a file whose hash has not
  changed is never re-read or re-parsed.
* Symbols / edges / endpoints are rebuilt from the cached parse results on
  every ``scan`` (cheap, in-memory) so cross-file resolution is always fresh.
* All LLM-related cache keys are derived from *content*, never from row ids,
  so renames/moves that keep content intact still hit the cache.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ..javaparse.model import JavaFile

SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    package     TEXT,
    parsed_json TEXT,
    parse_error TEXT,
    scanned_at  REAL NOT NULL,
    source_text TEXT
);

CREATE TABLE IF NOT EXISTS symbols (
    qname       TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,          -- type | method | field | constant
    name        TEXT NOT NULL,
    type_kind   TEXT,                   -- class | interface | enum | record | annotation (for kind=type)
    file_path   TEXT NOT NULL,
    parent      TEXT,
    start_line  INTEGER,
    end_line    INTEGER,
    data_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);
CREATE INDEX IF NOT EXISTS idx_symbols_parent ON symbols(parent);

CREATE TABLE IF NOT EXISTS edges (
    src   TEXT NOT NULL,
    dst   TEXT NOT NULL,
    kind  TEXT NOT NULL,
    meta  TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src, kind);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst, kind);

CREATE TABLE IF NOT EXISTS endpoints (
    id            TEXT PRIMARY KEY,
    http_method   TEXT NOT NULL,
    path          TEXT NOT NULL,
    framework     TEXT NOT NULL,
    handler       TEXT NOT NULL,         -- method symbol qname
    type_qname    TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    data_json     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    key            TEXT PRIMARY KEY,
    unit_kind      TEXT NOT NULL,
    unit_id        TEXT NOT NULL,
    unit_hash      TEXT NOT NULL,
    provider       TEXT,
    model          TEXT,
    prompt_version TEXT,
    response_json  TEXT NOT NULL,
    input_tokens   INTEGER DEFAULT 0,
    output_tokens  INTEGER DEFAULT 0,
    created_at     REAL NOT NULL,
    last_used_at   REAL NOT NULL,
    hits           INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cache_unit ON analysis_cache(unit_kind, unit_id);

CREATE TABLE IF NOT EXISTS llm_calls (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  REAL NOT NULL,
    provider            TEXT,
    model               TEXT,
    kind                TEXT,
    unit_ids            TEXT,
    input_tokens        INTEGER DEFAULT 0,
    output_tokens       INTEGER DEFAULT 0,
    cache_read_tokens   INTEGER DEFAULT 0,
    cache_write_tokens  INTEGER DEFAULT 0,
    duration_ms         INTEGER DEFAULT 0,
    status              TEXT,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS doc_snapshots (
    endpoint_id TEXT PRIMARY KEY,
    doc_hash    TEXT NOT NULL,
    title       TEXT,
    rendered_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ucs_documents (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at         REAL NOT NULL,
    mode               TEXT NOT NULL,
    endpoint_id        TEXT,
    new_requirement    TEXT,
    previous_analysis  TEXT,
    html               TEXT NOT NULL,
    payload_json       TEXT NOT NULL
);
"""


@dataclass
class SymbolRow:
    qname: str
    kind: str
    name: str
    type_kind: Optional[str]
    file_path: str
    parent: Optional[str]
    start_line: int
    end_line: int
    data: Dict[str, Any]


@dataclass
class EndpointRow:
    id: str
    http_method: str
    path: str
    framework: str
    handler: str
    type_qname: str
    file_path: str
    data: Dict[str, Any]


class GraphStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(files)")}
        if "source_text" not in cols:
            self.conn.execute("ALTER TABLE files ADD COLUMN source_text TEXT")
        ver = self.get_meta("schema_version")
        if ver is None or int(ver) != SCHEMA_VERSION:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    # ------------------------------------------------------------------ files
    def file_hashes(self) -> Dict[str, str]:
        return {r["path"]: r["sha256"] for r in self.conn.execute("SELECT path, sha256 FROM files")}

    def upsert_file(self, path: str, sha256: str, size: int, mtime: float, parsed: Optional[JavaFile],
                    error: Optional[str], source: Optional[str] = None) -> bool:
        """Persist a parsed file. Returns False when the content hash is unchanged."""
        row = self.conn.execute("SELECT sha256 FROM files WHERE path=?", (path,)).fetchone()
        if row and row["sha256"] == sha256:
            return False
        self.conn.execute(
            "INSERT OR REPLACE INTO files(path, sha256, size, mtime, package, parsed_json, parse_error, scanned_at, source_text)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (path, sha256, size, mtime, parsed.package if parsed else None,
             json.dumps(parsed.to_dict(), ensure_ascii=False) if parsed else None, error, time.time(),
             source),
        )
        return True

    def touch_file(self, path: str) -> None:
        self.conn.execute("UPDATE files SET scanned_at=? WHERE path=?", (time.time(), path))

    def delete_files(self, paths: Iterable[str]) -> None:
        self.conn.executemany("DELETE FROM files WHERE path=?", [(p,) for p in paths])

    def iter_parsed_files(self) -> Iterator[JavaFile]:
        for r in self.conn.execute("SELECT path, parsed_json FROM files WHERE parsed_json IS NOT NULL ORDER BY path"):
            jf = JavaFile.from_dict(json.loads(r["parsed_json"]))
            jf.path = r["path"]
            yield jf

    def file_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def file_source(self, path: str) -> Optional[str]:
        row = self.conn.execute("SELECT source_text FROM files WHERE path=?", (path,)).fetchone()
        return row["source_text"] if row else None

    def paths_missing_source(self) -> List[str]:
        return [r["path"] for r in self.conn.execute(
            "SELECT path FROM files WHERE source_text IS NULL OR source_text = ''"
        )]

    def update_file_source(self, path: str, source: str) -> None:
        self.conn.execute("UPDATE files SET source_text=? WHERE path=?", (source, path))

    def files_with_errors(self) -> List[Tuple[str, str]]:
        return [(r["path"], r["parse_error"]) for r in
                self.conn.execute("SELECT path, parse_error FROM files WHERE parse_error IS NOT NULL AND parse_error != ''")]

    # ------------------------------------------------------------------ graph
    def replace_graph(self, symbols: Iterable[SymbolRow], edges: Iterable[Tuple[str, str, str, Optional[Dict[str, Any]]]],
                      endpoints: Iterable[EndpointRow]) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM symbols")
        cur.execute("DELETE FROM edges")
        cur.execute("DELETE FROM endpoints")
        cur.executemany(
            "INSERT OR REPLACE INTO symbols(qname, kind, name, type_kind, file_path, parent, start_line, end_line, data_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [(s.qname, s.kind, s.name, s.type_kind, s.file_path, s.parent, s.start_line, s.end_line,
              json.dumps(s.data, ensure_ascii=False)) for s in symbols],
        )
        cur.executemany(
            "INSERT INTO edges(src, dst, kind, meta) VALUES (?,?,?,?)",
            [(src, dst, kind, json.dumps(meta, ensure_ascii=False) if meta else None) for src, dst, kind, meta in edges],
        )
        cur.executemany(
            "INSERT OR REPLACE INTO endpoints(id, http_method, path, framework, handler, type_qname, file_path, data_json)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(e.id, e.http_method, e.path, e.framework, e.handler, e.type_qname, e.file_path,
              json.dumps(e.data, ensure_ascii=False)) for e in endpoints],
        )
        self.conn.commit()

    def _row_to_symbol(self, r: sqlite3.Row) -> SymbolRow:
        return SymbolRow(qname=r["qname"], kind=r["kind"], name=r["name"], type_kind=r["type_kind"],
                         file_path=r["file_path"], parent=r["parent"], start_line=r["start_line"],
                         end_line=r["end_line"], data=json.loads(r["data_json"]))

    def get_symbol(self, qname: str) -> Optional[SymbolRow]:
        r = self.conn.execute("SELECT * FROM symbols WHERE qname=?", (qname,)).fetchone()
        return self._row_to_symbol(r) if r else None

    def find_symbols(self, kind: Optional[str] = None, name: Optional[str] = None,
                     name_like: Optional[str] = None, file_path: Optional[str] = None) -> List[SymbolRow]:
        q = "SELECT * FROM symbols WHERE 1=1"
        args: List[Any] = []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if name:
            q += " AND name=?"
            args.append(name)
        if name_like:
            q += " AND qname LIKE ?"
            args.append(name_like)
        if file_path:
            q += " AND file_path=?"
            args.append(file_path)
        q += " ORDER BY qname"
        return [self._row_to_symbol(r) for r in self.conn.execute(q, args)]

    def children(self, parent: str) -> List[SymbolRow]:
        return [self._row_to_symbol(r) for r in
                self.conn.execute("SELECT * FROM symbols WHERE parent=? ORDER BY start_line", (parent,))]

    def edges_from(self, src: str, kind: Optional[str] = None) -> List[Tuple[str, str, Optional[Dict[str, Any]]]]:
        if kind:
            rows = self.conn.execute("SELECT dst, kind, meta FROM edges WHERE src=? AND kind=?", (src, kind))
        else:
            rows = self.conn.execute("SELECT dst, kind, meta FROM edges WHERE src=?", (src,))
        return [(r["dst"], r["kind"], json.loads(r["meta"]) if r["meta"] else None) for r in rows]

    def edges_to(self, dst: str, kind: Optional[str] = None) -> List[Tuple[str, str, Optional[Dict[str, Any]]]]:
        if kind:
            rows = self.conn.execute("SELECT src, kind, meta FROM edges WHERE dst=? AND kind=?", (dst, kind))
        else:
            rows = self.conn.execute("SELECT src, kind, meta FROM edges WHERE dst=?", (dst,))
        return [(r["src"], r["kind"], json.loads(r["meta"]) if r["meta"] else None) for r in rows]

    def all_edges(self) -> Iterator[Tuple[str, str, str, Optional[Dict[str, Any]]]]:
        for r in self.conn.execute("SELECT src, dst, kind, meta FROM edges"):
            yield r["src"], r["dst"], r["kind"], (json.loads(r["meta"]) if r["meta"] else None)

    def all_symbols(self, kind: Optional[str] = None) -> Iterator[SymbolRow]:
        if kind:
            rows = self.conn.execute("SELECT * FROM symbols WHERE kind=? ORDER BY qname", (kind,))
        else:
            rows = self.conn.execute("SELECT * FROM symbols ORDER BY qname")
        for r in rows:
            yield self._row_to_symbol(r)

    def graph_stats(self) -> Dict[str, Any]:
        c = self.conn
        stats: Dict[str, Any] = {
            "files": c.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "symbols": c.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
            "edges": c.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
            "endpoints": c.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0],
        }
        stats["symbols_by_kind"] = {r[0]: r[1] for r in c.execute("SELECT kind, COUNT(*) FROM symbols GROUP BY kind")}
        stats["edges_by_kind"] = {r[0]: r[1] for r in c.execute("SELECT kind, COUNT(*) FROM edges GROUP BY kind")}
        stats["unresolved_edges"] = c.execute("SELECT COUNT(*) FROM edges WHERE dst LIKE 'ext:%'").fetchone()[0]
        return stats

    # ------------------------------------------------------------------ endpoints
    def list_endpoints(self) -> List[EndpointRow]:
        return [self._row_to_endpoint(r) for r in
                self.conn.execute("SELECT * FROM endpoints ORDER BY path, http_method, id")]

    def get_endpoint(self, eid: str) -> Optional[EndpointRow]:
        r = self.conn.execute("SELECT * FROM endpoints WHERE id=?", (eid,)).fetchone()
        return self._row_to_endpoint(r) if r else None

    @staticmethod
    def _row_to_endpoint(r: sqlite3.Row) -> EndpointRow:
        return EndpointRow(id=r["id"], http_method=r["http_method"], path=r["path"], framework=r["framework"],
                           handler=r["handler"], type_qname=r["type_qname"], file_path=r["file_path"],
                           data=json.loads(r["data_json"]))

    # ------------------------------------------------------------------ analysis cache
    def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        """Pure read (no write transaction is left open - important with several connections/threads)."""
        r = self.conn.execute("SELECT response_json FROM analysis_cache WHERE key=?", (key,)).fetchone()
        if not r:
            return None
        return json.loads(r["response_json"])

    def cache_touch(self, keys: Iterable[str]) -> None:
        """Record cache hits for statistics (one short transaction)."""
        now = time.time()
        self.conn.executemany("UPDATE analysis_cache SET last_used_at=?, hits=hits+1 WHERE key=?", [(now, k) for k in keys])
        self.conn.commit()

    def cache_put(self, key: str, unit_kind: str, unit_id: str, unit_hash: str, provider: str, model: str,
                  prompt_version: str, response: Dict[str, Any], input_tokens: int = 0, output_tokens: int = 0) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT OR REPLACE INTO analysis_cache(key, unit_kind, unit_id, unit_hash, provider, model, prompt_version,"
            " response_json, input_tokens, output_tokens, created_at, last_used_at, hits) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (key, unit_kind, unit_id, unit_hash, provider, model, prompt_version,
             json.dumps(response, ensure_ascii=False), input_tokens, output_tokens, now, now),
        )
        self.conn.commit()

    def cache_latest_for_unit(self, unit_kind: str, unit_id: str) -> Optional[Dict[str, Any]]:
        """Most recent cached analysis for a unit id regardless of hash (used as a stale fallback)."""
        r = self.conn.execute(
            "SELECT response_json FROM analysis_cache WHERE unit_kind=? AND unit_id=? ORDER BY created_at DESC LIMIT 1",
            (unit_kind, unit_id)).fetchone()
        return json.loads(r["response_json"]) if r else None

    def cache_stats(self) -> Dict[str, Any]:
        c = self.conn
        return {
            "entries": c.execute("SELECT COUNT(*) FROM analysis_cache").fetchone()[0],
            "by_kind": {r[0]: r[1] for r in c.execute("SELECT unit_kind, COUNT(*) FROM analysis_cache GROUP BY unit_kind")},
            "hits": c.execute("SELECT COALESCE(SUM(hits),0) FROM analysis_cache").fetchone()[0],
            "tokens_stored_input": c.execute("SELECT COALESCE(SUM(input_tokens),0) FROM analysis_cache").fetchone()[0],
            "tokens_stored_output": c.execute("SELECT COALESCE(SUM(output_tokens),0) FROM analysis_cache").fetchone()[0],
        }

    def cache_clear(self, unit_kind: Optional[str] = None, unit_id: Optional[str] = None) -> int:
        q = "DELETE FROM analysis_cache WHERE 1=1"
        args: List[Any] = []
        if unit_kind:
            q += " AND unit_kind=?"
            args.append(unit_kind)
        if unit_id:
            q += " AND unit_id=?"
            args.append(unit_id)
        n = self.conn.execute(q, args).rowcount
        self.conn.commit()
        return n

    def cache_prune(self, keep_keys: Iterable[str]) -> int:
        """Remove cache rows whose key is not in ``keep_keys`` (stale versions of changed units)."""
        keep = set(keep_keys)
        rows = [r[0] for r in self.conn.execute("SELECT key FROM analysis_cache")]
        stale = [k for k in rows if k not in keep]
        self.conn.executemany("DELETE FROM analysis_cache WHERE key=?", [(k,) for k in stale])
        self.conn.commit()
        return len(stale)

    # ------------------------------------------------------------------ llm call log
    def log_call(self, provider: str, model: str, kind: str, unit_ids: List[str], input_tokens: int, output_tokens: int,
                 cache_read_tokens: int, cache_write_tokens: int, duration_ms: int, status: str,
                 error: Optional[str] = None) -> None:
        self.conn.execute(
            "INSERT INTO llm_calls(ts, provider, model, kind, unit_ids, input_tokens, output_tokens, cache_read_tokens,"
            " cache_write_tokens, duration_ms, status, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), provider, model, kind, json.dumps(unit_ids), input_tokens, output_tokens, cache_read_tokens,
             cache_write_tokens, duration_ms, status, error),
        )
        self.conn.commit()

    def usage_totals(self, since: Optional[float] = None) -> Dict[str, Any]:
        q = ("SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens),0) AS input_tokens,"
             " COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,"
             " COALESCE(SUM(cache_write_tokens),0) AS cache_write_tokens,"
             " SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) AS failed FROM llm_calls")
        args: List[Any] = []
        if since is not None:
            q += " WHERE ts>=?"
            args.append(since)
        r = self.conn.execute(q, args).fetchone()
        return {k: (r[k] or 0) for k in r.keys()}

    def usage_by_model(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT provider, model, COUNT(*) AS calls, COALESCE(SUM(input_tokens),0) AS input_tokens,"
            " COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens"
            " FROM llm_calls GROUP BY provider, model")
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ doc snapshots
    def get_snapshots(self) -> Dict[str, Tuple[str, str]]:
        return {r["endpoint_id"]: (r["doc_hash"], r["title"] or "") for r in
                self.conn.execute("SELECT endpoint_id, doc_hash, title FROM doc_snapshots")}

    def save_snapshots(self, snaps: Dict[str, Tuple[str, str]]) -> None:
        self.conn.execute("DELETE FROM doc_snapshots")
        now = time.time()
        self.conn.executemany(
            "INSERT INTO doc_snapshots(endpoint_id, doc_hash, title, rendered_at) VALUES (?,?,?,?)",
            [(eid, h, t, now) for eid, (h, t) in snaps.items()],
        )
        self.conn.commit()

    def save_ucs(self, mode: str, endpoint_id: Optional[str], new_requirement: str,
                 previous_analysis: str, html: str, payload: Dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO ucs_documents(created_at, mode, endpoint_id, new_requirement, previous_analysis, html, payload_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (time.time(), mode, endpoint_id, new_requirement, previous_analysis, html,
             json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_ucs(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, created_at, mode, endpoint_id, new_requirement FROM ucs_documents ORDER BY id DESC LIMIT ?",
            (limit,))
        return [dict(r) for r in rows]

    def get_ucs(self, doc_id: int) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM ucs_documents WHERE id=?", (doc_id,)).fetchone()
        if not r:
            return None
        data = dict(r)
        data["payload"] = json.loads(data.pop("payload_json"))
        return data
