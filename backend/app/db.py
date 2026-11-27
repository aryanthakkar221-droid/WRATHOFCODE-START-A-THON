"""SQLite storage. One file, no server, enough for a demo and a first pilot."""
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

DATA_DIR = os.environ.get("KNOWFLOW_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
DB_PATH = os.environ.get("KNOWFLOW_DB", os.path.join(DATA_DIR, "knowflow.db"))
AUDIO_DIR = os.path.join(DATA_DIR, "audio")

SCHEMA = """
CREATE TABLE IF NOT EXISTS experts (
    name TEXT PRIMARY KEY,
    years_experience INTEGER,
    retires_in_months INTEGER,
    status TEXT,                               -- e.g. "retired 2025" (display only)
    available TEXT                             -- e.g. "Thursdays" (display only)
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    author TEXT NOT NULL,
    author_years_experience INTEGER,
    machine TEXT,
    machine_id TEXT,
    process TEXT,
    kind TEXT NOT NULL DEFAULT 'fix',          -- fix | mistake | lesson | decision
    problem TEXT,
    cause TEXT,
    solution TEXT,
    warning TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    transcript TEXT,
    source TEXT NOT NULL DEFAULT 'shift',      -- shift | meeting
    meeting_id INTEGER,
    status TEXT NOT NULL DEFAULT 'draft',      -- draft | approved
    approved_by TEXT,
    approved_at TEXT,
    worked_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    last_confirmed TEXT,
    audio_path TEXT,
    shared_by TEXT                             -- newer worker who passed on an expert's tip
);
CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    title TEXT NOT NULL,
    participants TEXT NOT NULL DEFAULT '[]',
    transcript TEXT,
    result TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS shift_plan (
    name TEXT PRIMARY KEY,                     -- person
    machine TEXT NOT NULL,                     -- where they work this shift
    shift TEXT
);
CREATE TABLE IF NOT EXISTS open_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    question TEXT NOT NULL,
    machine_id TEXT,
    asked_by TEXT,
    resolved_card_id INTEGER
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    s = name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or None


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    os.makedirs(AUDIO_DIR, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(cards)").fetchall()}
        if "shared_by" not in cols:  # database created before this column existed
            c.execute("ALTER TABLE cards ADD COLUMN shared_by TEXT")
        ecols = {r[1] for r in c.execute("PRAGMA table_info(experts)").fetchall()}
        for col in ("status", "available"):
            if col not in ecols:
                c.execute("ALTER TABLE experts ADD COLUMN %s TEXT" % col)


def card_row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["tags"] = json.loads(d.get("tags") or "[]")
    w, f = d["worked_count"], d["failed_count"]
    d["confidence"] = round((w + 1) / (w + f + 2), 2)  # Laplace: 0.5 with no votes
    d["outdated"] = _is_outdated(d)
    d["has_audio"] = bool(d.pop("audio_path", None))
    return d


def _is_outdated(d: Dict[str, Any]) -> bool:
    if d["failed_count"] > d["worked_count"]:
        return True
    ref = d.get("last_confirmed") or d.get("approved_at") or d.get("date")
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(ref)
        return age.days > 365
    except (TypeError, ValueError):
        return False


def insert_card(c: sqlite3.Connection, card: Dict[str, Any]) -> int:
    fields = [
        "date", "author", "author_years_experience", "machine", "machine_id", "process", "kind",
        "problem", "cause", "solution", "warning", "tags", "transcript", "source", "meeting_id",
        "status", "approved_by", "approved_at", "worked_count", "failed_count", "last_confirmed",
        "audio_path", "shared_by",
    ]
    row = dict(card)
    row.setdefault("date", now_iso())
    row.setdefault("kind", "fix")
    row.setdefault("source", "shift")
    row.setdefault("status", "draft")
    row.setdefault("worked_count", 0)
    row.setdefault("failed_count", 0)
    row["machine_id"] = row.get("machine_id") or slugify(row.get("machine"))
    row["tags"] = json.dumps(row.get("tags") or [], ensure_ascii=False)
    vals = [row.get(f) for f in fields]
    cur = c.execute(
        "INSERT INTO cards (%s) VALUES (%s)" % (",".join(fields), ",".join("?" * len(fields))), vals
    )
    return int(cur.lastrowid)


def get_card(c: sqlite3.Connection, card_id: int) -> Optional[Dict[str, Any]]:
    r = c.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    return card_row_to_dict(r) if r else None


def list_cards(
    c: sqlite3.Connection,
    machine_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    q, args = "SELECT * FROM cards WHERE 1=1", []  # type: str, List[Any]
    if machine_id:
        q += " AND machine_id=?"
        args.append(machine_id)
    if status:
        q += " AND status=?"
        args.append(status)
    q += " ORDER BY date DESC, id DESC"
    return [card_row_to_dict(r) for r in c.execute(q, args).fetchall()]


EXPERT_YEARS = 5  # 5+ years = expert questions; below = questions for newer workers


def expert_years(c: sqlite3.Connection, name: str) -> Optional[int]:
    r = c.execute("SELECT years_experience FROM experts WHERE name=?", (name,)).fetchone()
    return r["years_experience"] if r else None


# Spoken names for each machine (German and English), so "Verpackungsmaschine" or
# "the press" still lands on the right machine page.
MACHINE_ALIASES = {
    "packaging-machine": ["packaging machine", "packaging", "verpackungsmaschine", "verpackung", "packmaschine"],
    "machine-1": ["machine 1", "machine one", "maschine 1", "maschine eins"],
    "press-2": ["press 2", "press two", "presse 2", "presse zwei", "the press", "die presse"],
    "compressor": ["compressor", "kompressor"],
}


def match_machine(name: Optional[str], known: List[str]) -> Optional[str]:
    """Map a name the AI or the worker used to one of the known machines, or None."""
    if not name:
        return None
    low = name.strip().lower()
    if low in ("unknown", "none", "null", "n/a", "unbekannt"):
        return None
    by_slug = {slugify(k): k for k in known}
    if slugify(name) in by_slug:
        return by_slug[slugify(name)]
    for slug, aliases in MACHINE_ALIASES.items():
        if slug in by_slug and any(a in low for a in aliases):
            return by_slug[slug]
    return None


def find_machine_in_text(text: str, known: List[str]) -> Optional[str]:
    low = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    by_slug = {slugify(k): k for k in known}
    for k in known:  # exact names first
        if " " + k.lower() in low:
            return k
    for slug, aliases in MACHINE_ALIASES.items():
        if slug in by_slug and any(a in low for a in aliases):
            return by_slug[slug]
    return None


def shift_machine(c: sqlite3.Connection, name: str) -> Optional[str]:
    r = c.execute("SELECT machine FROM shift_plan WHERE name=?", (name,)).fetchone()
    return r["machine"] if r else None


def known_machine_names(c: sqlite3.Connection) -> List[str]:
    rows = c.execute("SELECT DISTINCT machine FROM cards WHERE machine IS NOT NULL "
                     "UNION SELECT machine FROM shift_plan").fetchall()
    return sorted({r[0] for r in rows})
