"""KnowFlow API: the AI Schichtbuch backend.

Run:  uvicorn app.main:app --reload   (from the backend/ folder)
Docs: http://localhost:8000/docs
"""
import io
import json
import logging
import os
import random
import re
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import segno
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from . import ai, alerts, db, seed, stt  # noqa: E402  (after .env so keys are visible)

logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(_app):
    db.init()
    if os.environ.get("KNOWFLOW_SEED", "1") == "1":
        seed.seed()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="KnowFlow API",
    description="Knowledge that doesn't retire. The AI Schichtbuch for manufacturing SMEs.",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PUBLIC_URL = os.environ.get("KNOWFLOW_PUBLIC_URL")  # where the frontend lives, for QR codes
ALLOW_RESET = os.environ.get("KNOWFLOW_ALLOW_RESET", "1") == "1"


# ------------------------------------------------------------------ helpers

def _experts(c) -> Dict[str, Dict[str, Any]]:
    return {r["name"]: dict(r) for r in c.execute("SELECT * FROM experts").fetchall()}


def _card_or_404(c, card_id: int) -> Dict[str, Any]:
    card = db.get_card(c, card_id)
    if not card:
        raise HTTPException(404, "Card %d not found" % card_id)
    return card


# What Whisper tends to "hear" in silence or noise. Never turn these into knowledge.
_SILENCE = re.compile(
    r"^(you|thank you\.?|thanks for watching!?|bye\.?|untertitel.*|vielen dank\.?|tschüss\.?|\W*)$", re.I)
_TOO_SHORT = ("I couldn't hear a shift report. Please try again a bit closer to the microphone "
              "and talk for at least a few seconds.")


def _check_heard(transcript: str) -> None:
    t = transcript.strip()
    if _SILENCE.match(t) or len(re.findall(r"\w+", t)) < 4:
        raise HTTPException(422, _TOO_SHORT)


async def _text_from(text: Optional[str], audio: Optional[UploadFile], language: Optional[str]):
    """Returns (transcript, audio_path). Audio is kept only until the card is approved."""
    if text and text.strip():
        if len(re.findall(r"\w+", text)) < 4:
            raise HTTPException(422, "Please write a little more: what happened, and what did you do?")
        return text.strip(), None
    if audio is None:
        raise HTTPException(422, "Send either `text` or an `audio` file.")
    raw = await audio.read()
    try:
        transcript = stt.transcribe(raw, audio.filename or "audio.webm", language)
    except stt.STTUnavailable as e:
        raise HTTPException(501, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, "Speech-to-text failed: %s" % e)
    _check_heard(transcript)
    ext = os.path.splitext(audio.filename or "")[1] or ".webm"
    path = os.path.join(db.AUDIO_DIR, uuid.uuid4().hex + ext)
    with open(path, "wb") as f:
        f.write(raw)
    return transcript, path


def _delete_audio(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        os.remove(path)


# ------------------------------------------------------------------ meta

@app.get("/health", tags=["meta"])
def health() -> Dict[str, Any]:
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    return {"ok": True, "ai": ai.ai_enabled(), "model": ai.MODEL if ai.ai_enabled() else "offline-rules",
            "speech_to_text": stt.stt_enabled(), "cards": n}


@app.post("/admin/reset-demo", tags=["meta"])
def reset_demo() -> Dict[str, Any]:
    """Wipe everything and reload the demo data. Handy between pitch rehearsals."""
    if not ALLOW_RESET:
        raise HTTPException(403, "Reset is disabled")
    return {"seeded_cards": seed.seed(reset=True)}


@app.get("/experts", tags=["meta"])
def experts() -> List[Dict[str, Any]]:
    with db.conn() as c:
        return list(_experts(c).values())


# ------------------------------------------------------------------ shift entries

@app.post("/entries", tags=["entries"])
async def create_entry(
    author: str = Form(...),
    machine: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    language: Optional[str] = Form(None, description="e.g. 'de'; helps speech-to-text"),
    audio: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    """Log a shift: voice (audio file) or text. The AI turns it into a draft knowledge card
    that the expert approves before it goes live."""
    transcript, audio_path = await _text_from(text, audio, language)
    with db.conn() as c:
        known = [r[0] for r in c.execute("SELECT DISTINCT machine FROM cards WHERE machine IS NOT NULL").fetchall()]
        tag_rows = c.execute("SELECT tags FROM cards WHERE status='approved'" +
                             (" AND machine_id=?" if machine else ""),
                             ([db.slugify(machine)] if machine else [])).fetchall()
        experts = {r["name"]: r["years_experience"] for r in c.execute("SELECT * FROM experts").fetchall()}
    known_tags = sorted({t for r in tag_rows for t in json.loads(r[0])})
    author_years = experts.get(author)
    newer = author_years is None or author_years < db.EXPERT_YEARS
    senior = [n for n, y in experts.items() if y is not None and y >= db.EXPERT_YEARS and n != author]
    structured = ai.structure_entry(transcript, machine, known, known_tags, senior if newer else None)
    if not any(structured.get(k) for k in ("problem", "solution", "warning")):
        _delete_audio(audio_path)
        raise HTTPException(422, "I heard you, but found no problem, fix or tip in it. "
                                 "Try: what was difficult, what did you do, what should others watch out for?")
    # A newer worker passing on an expert's tip: the tip belongs to the expert, stays a
    # draft, and only that expert can approve it.
    tip_from = structured.get("learned_from") if newer else None
    if tip_from not in senior:
        tip_from = None
    with db.conn() as c:
        card_id = db.insert_card(c, {
            "author": tip_from or author,
            "shared_by": author if tip_from else None,
            "author_years_experience": db.expert_years(c, tip_from or author),
            "machine": structured.get("machine"),
            "process": structured.get("process"),
            "kind": structured.get("kind") or "fix",
            "problem": structured.get("problem"),
            "cause": structured.get("cause"),
            "solution": structured.get("solution"),
            "warning": structured.get("warning"),
            "tags": structured.get("tags"),
            "transcript": transcript,
            "audio_path": audio_path,
        })
        card = db.get_card(c, card_id)
        open_q = None
        if newer and structured.get("nobody_could_help") and not tip_from:
            cur = c.execute(
                "INSERT INTO open_questions (date, question, machine_id, asked_by) VALUES (?,?,?,?)",
                (db.now_iso(), card["problem"] or transcript[:200], card["machine_id"], author))
            open_q = cur.lastrowid
    return {"transcript": transcript, "card": card, "tip_from": tip_from, "open_question_id": open_q}


class EntryJSON(BaseModel):
    author: str
    text: str
    machine: Optional[str] = None


@app.post("/entries/text", tags=["entries"])
async def create_entry_json(body: EntryJSON) -> Dict[str, Any]:
    """Same as POST /entries, as JSON (for browser speech recognition or typed notes)."""
    return await create_entry(author=body.author, machine=body.machine, text=body.text, language=None, audio=None)


@app.get("/entries", tags=["entries"])
def list_entries(machine: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """All cards, newest first. Filter by machine id (e.g. `cnc-3`) and/or status (`draft`, `approved`)."""
    with db.conn() as c:
        return db.list_cards(c, db.slugify(machine) if machine else None, status)


@app.get("/cards/{card_id}", tags=["cards"])
def get_card(card_id: int) -> Dict[str, Any]:
    with db.conn() as c:
        return _card_or_404(c, card_id)


class CardEdit(BaseModel):
    machine: Optional[str] = None
    process: Optional[str] = None
    kind: Optional[str] = None
    problem: Optional[str] = None
    cause: Optional[str] = None
    solution: Optional[str] = None
    warning: Optional[str] = None
    tags: Optional[List[str]] = None


@app.patch("/cards/{card_id}", tags=["cards"])
def edit_card(card_id: int, body: CardEdit) -> Dict[str, Any]:
    """The expert corrects the AI draft before approving it."""
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(422, "Nothing to change")
    if "tags" in changes:
        changes["tags"] = json.dumps(changes["tags"], ensure_ascii=False)
    if "machine" in changes:
        changes["machine_id"] = db.slugify(changes["machine"])
    with db.conn() as c:
        _card_or_404(c, card_id)
        sets = ",".join("%s=?" % k for k in changes)
        c.execute("UPDATE cards SET %s WHERE id=?" % sets, list(changes.values()) + [card_id])
        return db.get_card(c, card_id)


class Approve(BaseModel):
    expert: str


@app.post("/cards/{card_id}/approve", tags=["cards"])
def approve_card(card_id: int, body: Approve) -> Dict[str, Any]:
    """Card goes live. Privacy by design: the audio recording is deleted now, only the
    approved text is kept."""
    with db.conn() as c:
        card = _card_or_404(c, card_id)
        if card.get("shared_by") and body.expert != card["author"]:
            raise HTTPException(403, "Only %s can approve this tip." % card["author"])
        r = c.execute("SELECT audio_path FROM cards WHERE id=?", (card_id,)).fetchone()
        _delete_audio(r["audio_path"])
        c.execute(
            "UPDATE cards SET status='approved', approved_by=?, approved_at=?, audio_path=NULL WHERE id=?",
            (body.expert, db.now_iso(), card_id),
        )
        return db.get_card(c, card_id)


@app.delete("/cards/{card_id}", tags=["cards"])
def reject_card(card_id: int) -> Dict[str, Any]:
    """Expert rejects a draft. Card and audio are deleted."""
    with db.conn() as c:
        _card_or_404(c, card_id)
        r = c.execute("SELECT audio_path FROM cards WHERE id=?", (card_id,)).fetchone()
        _delete_audio(r["audio_path"])
        c.execute("DELETE FROM cards WHERE id=?", (card_id,))
    return {"deleted": card_id}


class Feedback(BaseModel):
    worked: bool


@app.post("/cards/{card_id}/feedback", tags=["cards"])
def card_feedback(card_id: int, body: Feedback) -> Dict[str, Any]:
    """'Did this work?' Proven fixes rise; fixes that fail get flagged as outdated."""
    with db.conn() as c:
        _card_or_404(c, card_id)
        if body.worked:
            c.execute("UPDATE cards SET worked_count=worked_count+1, last_confirmed=? WHERE id=?",
                      (db.now_iso(), card_id))
        else:
            c.execute("UPDATE cards SET failed_count=failed_count+1 WHERE id=?", (card_id,))
        return db.get_card(c, card_id)


# ------------------------------------------------------------------ ask

class Ask(BaseModel):
    question: str
    machine: Optional[str] = None
    asked_by: Optional[str] = None


@app.post("/ask", tags=["ask"])
def ask(body: Ask) -> Dict[str, Any]:
    """A new employee asks in plain language (any language). The answer is built only from
    approved cards and names who solved it and when. Unanswered questions go to the
    experts (closed loop)."""
    mid = db.slugify(body.machine) if body.machine else None
    with db.conn() as c:
        cards = db.list_cards(c, mid, "approved")
        candidates = ai.rank_cards(body.question, cards)[:25] or cards[:25]
        result = ai.answer_question(body.question, candidates if ai.ai_enabled() else cards)
        by_id = {x["id"]: x for x in cards}
        for step in result["steps"]:
            src = by_id[step["card_id"]]
            step["source"] = {"author": src["author"], "date": src["date"],
                              "years_experience": src["author_years_experience"],
                              "confidence": src["confidence"], "machine": src["machine"]}
        source_ids = list(dict.fromkeys(s["card_id"] for s in result["steps"]))
        result["sources"] = [by_id[i] for i in source_ids]
        if not result["answered"]:
            cur = c.execute(
                "INSERT INTO open_questions (date, question, machine_id, asked_by) VALUES (?,?,?,?)",
                (db.now_iso(), body.question, mid, body.asked_by),
            )
            result["open_question_id"] = cur.lastrowid
    result["question"] = body.question
    return result


@app.get("/open-questions", tags=["ask"])
def open_questions(expert: Optional[str] = None) -> List[Dict[str, Any]]:
    """Questions nobody could answer yet. With `expert`, only questions about machines that
    expert knows: these become the AI's next question in their shift log."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM open_questions WHERE resolved_card_id IS NULL ORDER BY date DESC").fetchall()]
        if expert:
            known = {r["machine_id"] for r in c.execute(
                "SELECT DISTINCT machine_id FROM cards WHERE author=?", (expert,)).fetchall()}
            rows = [r for r in rows if r["machine_id"] is None or r["machine_id"] in known]
            rows.sort(key=lambda r: r["machine_id"] not in known)  # their machines first (stable: newest first)
    return rows


class Resolve(BaseModel):
    card_id: int


@app.post("/open-questions/{qid}/resolve", tags=["ask"])
def resolve_question(qid: int, body: Resolve) -> Dict[str, Any]:
    with db.conn() as c:
        _card_or_404(c, body.card_id)
        c.execute("UPDATE open_questions SET resolved_card_id=? WHERE id=?", (body.card_id, qid))
    return {"resolved": qid, "card_id": body.card_id}


# ------------------------------------------------------------------ machines & QR

@app.get("/machines", tags=["machines"])
def machines() -> List[Dict[str, Any]]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT machine_id, machine, COUNT(*) AS cards, COUNT(DISTINCT author) AS experts "
            "FROM cards WHERE status='approved' AND machine_id IS NOT NULL "
            "GROUP BY machine_id ORDER BY machine").fetchall()
    return [dict(r) for r in rows]


@app.get("/machine/{machine_id}", tags=["machines"])
def machine(machine_id: str, request: Request):
    """What the QR code on the machine opens: all approved cards for that machine, proven
    fixes first. A browser opening /machine/<id> directly is sent to the app page."""
    mid = db.slugify(machine_id)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/#/m/" + mid, status_code=302)
    with db.conn() as c:
        cards = db.list_cards(c, mid, "approved")
        if not cards:
            raise HTTPException(404, "No approved knowledge for machine '%s' yet" % machine_id)
        all_alerts = alerts.compute_alerts(db.list_cards(c), _experts(c))
    cards.sort(key=lambda x: (x["outdated"], -x["confidence"], x["date"]), reverse=False)
    return {
        "machine_id": mid,
        "machine": cards[0]["machine"],
        "experts": sorted({x["author"] for x in cards}),
        "alerts": [a for a in all_alerts if a.get("machine_id") == mid],
        "cards": cards,
    }


@app.get("/machine/{machine_id}/qr.svg", tags=["machines"])
def machine_qr(machine_id: str, request: Request) -> Response:
    """Printable QR code that opens the machine page in the app: {base}/#/m/{id}.
    Base is KNOWFLOW_PUBLIC_URL if set, otherwise this server (which also serves the app)."""
    mid = db.slugify(machine_id)
    base = (PUBLIC_URL or str(request.base_url)).rstrip("/")
    qr = segno.make(base + "/#/m/" + mid, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=8, dark="#1f2937", xmldecl=False)  # standalone SVG with xmlns
    return Response(buf.getvalue(), media_type="image/svg+xml")


# ------------------------------------------------------------------ dashboard

@app.get("/alerts", tags=["dashboard"])
def get_alerts() -> List[Dict[str, Any]]:
    """Recurring problems, single-expert knowledge risks, and disputed fixes."""
    with db.conn() as c:
        return alerts.compute_alerts(db.list_cards(c), _experts(c))


@app.get("/risk-map", tags=["dashboard"])
def get_risk_map() -> List[Dict[str, Any]]:
    """Per machine: how many people know it, and whether they retire soon."""
    with db.conn() as c:
        return alerts.risk_map(db.list_cards(c, status="approved"), _experts(c))


@app.get("/stats", tags=["dashboard"])
def stats() -> Dict[str, Any]:
    with db.conn() as c:
        one = lambda q: c.execute(q).fetchone()[0]  # noqa: E731
        return {
            "cards_approved": one("SELECT COUNT(*) FROM cards WHERE status='approved'"),
            "cards_draft": one("SELECT COUNT(*) FROM cards WHERE status='draft'"),
            "experts": one("SELECT COUNT(DISTINCT author) FROM cards"),
            "machines": one("SELECT COUNT(DISTINCT machine_id) FROM cards WHERE machine_id IS NOT NULL"),
            "confirmed_fixes": one("SELECT COALESCE(SUM(worked_count),0) FROM cards"),
            "open_questions": one("SELECT COUNT(*) FROM open_questions WHERE resolved_card_id IS NULL"),
            "meetings": one("SELECT COUNT(*) FROM meetings"),
        }


# ------------------------------------------------------------------ fix-it challenge

_challenge_cache = {}  # type: Dict[int, Dict[str, Any]]


class ChallengeReq(BaseModel):
    machine: Optional[str] = None
    card_id: Optional[int] = None
    exclude: List[int] = []
    fresh: bool = False


@app.post("/challenge", tags=["learn"])
def challenge(body: ChallengeReq) -> Dict[str, Any]:
    """Fix-it Challenge: turns an approved card into a short game (situation, 4 choices,
    what happens with each, the expert's explanation). Nothing about the player is stored."""
    with db.conn() as c:
        if body.card_id:
            card = _card_or_404(c, body.card_id)
        else:
            mid = db.slugify(body.machine) if body.machine else None
            cards = [x for x in db.list_cards(c, mid, "approved")
                     if x["solution"] and x["kind"] in ("fix", "mistake") and x["id"] not in body.exclude]
            if not cards:
                cards = [x for x in db.list_cards(c, mid, "approved") if x["solution"]]
            if not cards:
                raise HTTPException(404, "No approved tips to build a challenge from yet")
            # best proven fix first: the one most people said worked
            card = max(cards, key=lambda x: (x["worked_count"] - x["failed_count"], x["date"]))
    if card["status"] != "approved":
        raise HTTPException(400, "Only approved cards can become a challenge")
    game = None if body.fresh else _challenge_cache.get(card["id"])
    if game is None:
        game = ai.make_challenge(card)
        _challenge_cache[card["id"]] = game
    options = [dict(o) for o in game["options"]]
    random.shuffle(options)
    # The expert's own words come only from their approved card, never from the AI.
    tip = " ".join(x for x in [card["solution"], ("Watch out: " + card["warning"]) if card["warning"] else ""] if x)
    return {**game, "options": options, "card_id": card["id"], "machine": card["machine"],
            "machine_id": card["machine_id"], "expert": card["author"],
            "expert_years": card["author_years_experience"], "expert_tip": tip,
            "tip_date": card["date"], "approved_by": card["approved_by"]}


# ------------------------------------------------------------------ meetings

@app.post("/meetings", tags=["meetings"])
async def create_meeting(
    title: str = Form(...),
    participants: str = Form("", description="Comma-separated names"),
    consent: bool = Form(False, description="Everyone in the meeting agreed to the recording"),
    text: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    audio: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    """Record a meeting and keep the know-how: decisions with reasons, problems and
    solutions, lessons, action items. Lasting points become draft cards. Consent is
    required: recording without it is illegal in Germany."""
    if not consent:
        raise HTTPException(400, "Consent required: everyone in the meeting must agree to the recording.")
    transcript, audio_path = await _text_from(text, audio, language)
    _delete_audio(audio_path)  # meetings never keep audio; only extracted knowledge stays
    result = ai.extract_meeting(transcript)
    people = [p.strip() for p in participants.split(",") if p.strip()]
    author = people[0] if people else "Meeting"
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO meetings (date, title, participants, transcript, result) VALUES (?,?,?,?,?)",
            (db.now_iso(), title, json.dumps(people, ensure_ascii=False), transcript,
             json.dumps(result, ensure_ascii=False)),
        )
        meeting_id = cur.lastrowid
        new_ids = []
        for p in result["problems"]:
            new_ids.append(db.insert_card(c, {
                "author": author, "machine": p.get("machine"), "process": title, "kind": "fix",
                "problem": p["problem"], "solution": p.get("solution"), "tags": ["meeting"],
                "source": "meeting", "meeting_id": meeting_id, "transcript": None}))
        for d in result["decisions"]:
            new_ids.append(db.insert_card(c, {
                "author": author, "process": title, "kind": "decision", "problem": d["decision"],
                "cause": d.get("reason"), "tags": ["decision"], "source": "meeting",
                "meeting_id": meeting_id}))
        for lesson in result["lessons_learned"]:
            new_ids.append(db.insert_card(c, {
                "author": author, "process": title, "kind": "lesson", "problem": lesson,
                "tags": ["lesson"], "source": "meeting", "meeting_id": meeting_id}))
        cards = [db.get_card(c, i) for i in new_ids]
    return {"meeting_id": meeting_id, "title": title, "participants": people,
            "transcript": transcript, **result, "cards": cards}


@app.get("/meetings", tags=["meetings"])
def list_meetings() -> List[Dict[str, Any]]:
    with db.conn() as c:
        rows = c.execute("SELECT id, date, title, participants, result FROM meetings ORDER BY date DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["participants"] = json.loads(d["participants"])
        d.update(json.loads(d.pop("result")))
        out.append(d)
    return out


# ------------------------------------------------------------------ frontend

# Served last, so API routes win. The app uses hash routes (#/m/cnc-3), so no clash.
_FRONTEND = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(_FRONTEND):
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")
