"""SQLite storage. Single-file, no ORM — enough for MVP."""
import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.environ.get("DATA_DIR", "./data"), "concierge.db"))

_lock = threading.RLock()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_conn = _connect()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  name TEXT,
  status TEXT DEFAULT 'new',
  status_detail TEXT DEFAULT '',
  pages_count INTEGER DEFAULT 0,
  config_json TEXT DEFAULT '{}',
  created_at REAL,
  indexed_at REAL
);
CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT, url TEXT, title TEXT, text TEXT
);
CREATE INDEX IF NOT EXISTS pages_site ON pages(site_id);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT, page_id INTEGER, ord INTEGER, text TEXT
);
CREATE INDEX IF NOT EXISTS chunks_site ON chunks(site_id);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  site_id TEXT,
  messages_json TEXT DEFAULT '[]',
  visitor_meta TEXT DEFAULT '{}',
  created_at REAL, updated_at REAL
);
CREATE INDEX IF NOT EXISTS conv_site ON conversations(site_id);
CREATE TABLE IF NOT EXISTS leads (
  id TEXT PRIMARY KEY,
  site_id TEXT, conversation_id TEXT,
  name TEXT, phone TEXT, email TEXT, need TEXT, summary TEXT,
  temperature TEXT, created_at REAL
);
CREATE INDEX IF NOT EXISTS leads_site ON leads(site_id);
"""

with _lock:
    _conn.executescript(SCHEMA)
    _conn.commit()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


# ---------- sites ----------

DEFAULT_CONFIG = {
    "tone": "дружелюбный, деловой, кратко",
    "language": "ru",
    "agent_name": "Консультант",
    "welcome": "Здравствуйте! Я консультант этого сайта. Подскажу по услугам, ценам и запишу к специалисту. Что вас интересует?",
    "quick_replies": ["Сколько стоит?", "Как вас найти?", "Хочу записаться"],
    "qualification": [
        "Что именно нужно посетителю (услуга/товар, детали: марка, модель, объём работ)",
        "Когда нужно (срочно / на этой неделе / просто узнаёт)",
        "Имя и телефон (или email) для связи",
    ],
    "cta": "предложить оставить телефон, чтобы менеджер перезвонил и подтвердил запись",
    "extra_knowledge": "",
    "handoff": "Менеджер свяжется с вами в рабочее время.",
    "accent": "#2563eb",
}


def create_site(url: str, name: Optional[str] = None) -> dict:
    sid = new_id("s_")
    with _lock:
        _conn.execute(
            "INSERT INTO sites(id,url,name,status,config_json,created_at) VALUES(?,?,?,?,?,?)",
            (sid, url, name or url, "new", json.dumps(DEFAULT_CONFIG, ensure_ascii=False), time.time()),
        )
        _conn.commit()
    return get_site(sid)


def get_site(sid: str) -> Optional[dict]:
    with _lock:
        row = _conn.execute("SELECT * FROM sites WHERE id=?", (sid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(d.get("config_json") or "{}"))
    except Exception:
        pass
    d["config"] = cfg
    return d


def list_sites() -> list[dict]:
    with _lock:
        rows = _rows(_conn.execute("SELECT * FROM sites ORDER BY created_at DESC"))
    for d in rows:
        try:
            d["config"] = {**DEFAULT_CONFIG, **json.loads(d.get("config_json") or "{}")}
        except Exception:
            d["config"] = dict(DEFAULT_CONFIG)
    return rows


def update_site(sid: str, **fields: Any) -> None:
    if not fields:
        return
    if "config" in fields:
        fields["config_json"] = json.dumps(fields.pop("config"), ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        _conn.execute(f"UPDATE sites SET {cols} WHERE id=?", (*fields.values(), sid))
        _conn.commit()


def delete_site(sid: str) -> None:
    with _lock:
        for t in ("pages", "chunks", "conversations", "leads"):
            _conn.execute(f"DELETE FROM {t} WHERE site_id=?", (sid,))
        _conn.execute("DELETE FROM sites WHERE id=?", (sid,))
        _conn.commit()


# ---------- pages / chunks ----------

def replace_pages(sid: str, pages: list[dict], chunks_per_page: list[list[str]]) -> None:
    with _lock:
        _conn.execute("DELETE FROM pages WHERE site_id=?", (sid,))
        _conn.execute("DELETE FROM chunks WHERE site_id=?", (sid,))
        for p, chs in zip(pages, chunks_per_page):
            cur = _conn.execute(
                "INSERT INTO pages(site_id,url,title,text) VALUES(?,?,?,?)",
                (sid, p["url"], p["title"], p["text"]),
            )
            pid = cur.lastrowid
            _conn.executemany(
                "INSERT INTO chunks(site_id,page_id,ord,text) VALUES(?,?,?,?)",
                [(sid, pid, i, c) for i, c in enumerate(chs)],
            )
        _conn.execute(
            "UPDATE sites SET pages_count=?, indexed_at=?, status='ready', status_detail='' WHERE id=?",
            (len(pages), time.time(), sid),
        )
        _conn.commit()


def get_chunks(sid: str) -> list[dict]:
    with _lock:
        return _rows(
            _conn.execute(
                "SELECT c.id, c.text, c.ord, p.url, p.title FROM chunks c JOIN pages p ON p.id=c.page_id WHERE c.site_id=? ORDER BY c.page_id, c.ord",
                (sid,),
            )
        )


def get_pages(sid: str) -> list[dict]:
    with _lock:
        return _rows(_conn.execute("SELECT id,url,title,length(text) AS size FROM pages WHERE site_id=? ORDER BY id", (sid,)))


def get_page(sid: str, url: str) -> Optional[dict]:
    with _lock:
        row = _conn.execute("SELECT * FROM pages WHERE site_id=? AND url=?", (sid, url)).fetchone()
    return dict(row) if row else None


# ---------- conversations ----------

def get_conversation(cid: str) -> Optional[dict]:
    with _lock:
        row = _conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["messages"] = json.loads(d.get("messages_json") or "[]")
    return d


def create_conversation(sid: str, visitor_meta: dict | None = None) -> dict:
    cid = new_id("c_")
    now = time.time()
    with _lock:
        _conn.execute(
            "INSERT INTO conversations(id,site_id,messages_json,visitor_meta,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (cid, sid, "[]", json.dumps(visitor_meta or {}, ensure_ascii=False), now, now),
        )
        _conn.commit()
    return get_conversation(cid)


def save_messages(cid: str, messages: list[dict]) -> None:
    with _lock:
        _conn.execute(
            "UPDATE conversations SET messages_json=?, updated_at=? WHERE id=?",
            (json.dumps(messages, ensure_ascii=False), time.time(), cid),
        )
        _conn.commit()


def list_conversations(sid: str, limit: int = 100) -> list[dict]:
    with _lock:
        rows = _rows(
            _conn.execute(
                "SELECT * FROM conversations WHERE site_id=? ORDER BY updated_at DESC LIMIT ?", (sid, limit)
            )
        )
    for d in rows:
        d["messages"] = json.loads(d.pop("messages_json") or "[]")
    return rows


# ---------- leads ----------

def create_lead(sid: str, cid: str, data: dict) -> dict:
    lid = new_id("l_")
    with _lock:
        _conn.execute(
            "INSERT INTO leads(id,site_id,conversation_id,name,phone,email,need,summary,temperature,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                lid, sid, cid,
                data.get("name"), data.get("phone"), data.get("email"),
                data.get("need"), data.get("summary"), data.get("temperature", "warm"),
                time.time(),
            ),
        )
        _conn.commit()
        row = _conn.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
    return dict(row)


def list_leads(sid: str, limit: int = 200) -> list[dict]:
    with _lock:
        return _rows(_conn.execute("SELECT * FROM leads WHERE site_id=? ORDER BY created_at DESC LIMIT ?", (sid, limit)))


def stats(sid: str) -> dict:
    with _lock:
        conv = _conn.execute("SELECT COUNT(*) FROM conversations WHERE site_id=?", (sid,)).fetchone()[0]
        leads = _conn.execute("SELECT COUNT(*) FROM leads WHERE site_id=?", (sid,)).fetchone()[0]
        hot = _conn.execute("SELECT COUNT(*) FROM leads WHERE site_id=? AND temperature='hot'", (sid,)).fetchone()[0]
        msgs = _conn.execute("SELECT messages_json FROM conversations WHERE site_id=?", (sid,)).fetchall()
    n_msgs = sum(len(json.loads(m[0] or "[]")) for m in msgs)
    return {"conversations": conv, "leads": leads, "hot_leads": hot, "messages": n_msgs,
            "conversion": round(leads / conv * 100, 1) if conv else 0.0}
