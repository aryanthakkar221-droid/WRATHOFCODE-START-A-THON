"""KnowFlow API: the AI Schichtbuch backend.

Run:  uvicorn app.main:app --reload   (from the backend/ folder)
Docs: http://localhost:8000/docs
"""
import io
import sqlite3
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

from . import tokens, ai, alerts, db, seed, stt  # noqa: E402  (after .env so keys are visible)

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


@app.get("/wallet/{person}", tags=["tokens"])
def get_wallet(person: str) -> Dict[str, Any]:
    """Token balance, this month's progress to the 50 EUR cap, recent earnings."""
    with db.conn() as c:
        return tokens.wallet(c, person)


@app.post("/wallet/{person}/voucher", tags=["tokens"])
def canteen_voucher(person: str) -> Dict[str, Any]:
    """Demo canteen voucher (no real payment): a code plus a QR image."""
    import secrets
    import segno
    with db.conn() as c:
        w = tokens.wallet(c, person)
    code = "KF-" + secrets.token_hex(3).upper()
    buf = io.BytesIO()
    segno.make("KNOWFLOW-CANTEEN:%s:%s:%.2fEUR" % (code, person, w["balance_eur"])).save(
        buf, kind="svg", xmldecl=False, scale=6, border=2)
    return {"code": code, "person": person, "eur": w["balance_eur"], "tokens": w["balance"],
            "qr_svg": buf.getvalue().decode(), "demo": True}


@app.get("/shift-plan", tags=["meta"])
def shift_plan() -> List[Dict[str, Any]]:
    """Demo shift plan: who works at which machine (used when a recording names no machine)."""
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM shift_plan ORDER BY name").fetchall()]


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
        known = db.known_machine_names(c)
        planned = db.shift_machine(c, author)
        tag_rows = c.execute("SELECT tags FROM cards WHERE status='approved'" +
                             (" AND machine_id=?" if machine else ""),
                             ([db.slugify(machine)] if machine else [])).fetchall()
        experts = {r["name"]: r["years_experience"] for r in c.execute("SELECT * FROM experts").fetchall()}
    known_tags = sorted({t for r in tag_rows for t in json.loads(r[0])})
    author_years = experts.get(author)
    newer = author_years is None or author_years < db.EXPERT_YEARS
    senior = [n for n, y in experts.items() if y is not None and y >= db.EXPERT_YEARS and n != author]
    structured = ai.structure_entry(transcript, machine, known, known_tags, senior if newer else None)
    # Which machine? 1) chosen by hand  2) what the worker said  3) their shift plan
    if machine:
        machine_source = "chosen"
    else:
        said = db.match_machine(structured.get("machine"), known) or db.find_machine_in_text(transcript, known)
        machine_source = "said" if said else ("shift plan" if planned else "unknown")
        structured["machine"] = said or planned
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
    return {"transcript": transcript, "card": card, "tip_from": tip_from, "open_question_id": open_q,
            "machine_source": machine_source, "machines": known}


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
        reward = None
        if card["status"] != "approved":
            reward = tokens.award(c, card["author"], tokens.APPROVED,
                                  "Your card was approved: %s" % (card["problem"] or "")[:60], card_id)
        out = db.get_card(c, card_id)
        out["tokens"] = reward
        return out


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
    voter: Optional[str] = None  # who clicked; one vote per person per card


@app.post("/cards/{card_id}/feedback", tags=["cards"])
def card_feedback(card_id: int, body: Feedback) -> Dict[str, Any]:
    """'Did this work?' Proven fixes rise; fixes that fail get flagged as outdated.
    'Worked' from a colleague gives the author +10 tokens (not for your own card)."""
    with db.conn() as c:
        card = _card_or_404(c, card_id)
        reward = None
        if body.voter:
            try:
                c.execute("INSERT INTO feedback_votes (card_id, voter, worked, date) VALUES (?,?,?,?)",
                          (card_id, body.voter, int(body.worked), db.now_iso()))
            except sqlite3.IntegrityError:
                out = db.get_card(c, card_id)
                out["already_voted"] = True
                return out
            if body.worked and body.voter != card["author"]:
                reward = tokens.award(c, card["author"], tokens.WORKED,
                                      "Your fix worked for %s" % body.voter, card_id)
        if body.worked:
            c.execute("UPDATE cards SET worked_count=worked_count+1, last_confirmed=? WHERE id=?",
                      (db.now_iso(), card_id))
        else:
            c.execute("UPDATE cards SET failed_count=failed_count+1 WHERE id=?", (card_id,))
        out = db.get_card(c, card_id)
        out["tokens"] = reward
        return out


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
    if not ai.is_work_question(body.question):
        # small talk / general question: labelled general answer, no card search, never to the experts,
        # nothing stored
        return {"answered": False, "general": True, "question": body.question, "steps": [], "sources": [],
                "summary": ai.general_answer(body.question, body.asked_by), "warning": None}
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
# Consent: the organiser lists the participants; each participant answers for themselves
# (on their own device, or on the shared device after it is handed to them). Recording is
# only possible when everyone agreed; one "no" blocks the meeting. The log is kept.

class ConsentRequest(BaseModel):
    title: str
    organiser: str
    participants: List[str]


class ConsentAnswer(BaseModel):
    name: str                      # the participant answering
    agree: bool
    by: str                        # who is signed in on the device that sent the answer
    shared_device: bool = False    # True only after the device was handed to `name`


def _consent_dict(r) -> Dict[str, Any]:
    d = dict(r)
    d["participants"] = json.loads(d["participants"])
    d["answers"] = json.loads(d["answers"])
    ans = d["answers"]
    if any(a.get("agreed") is False for a in ans.values()):
        d["status"] = "declined"
    elif all(ans.get(p, {}).get("agreed") for p in d["participants"]):
        d["status"] = "ready"
    else:
        d["status"] = "waiting"
    if d.get("meeting_id"):
        d["status"] = "used"
    d["declined_by"] = [n for n, a in ans.items() if a.get("agreed") is False]
    return d


def _consent_or_404(c, rid: int) -> Dict[str, Any]:
    r = c.execute("SELECT * FROM consent_requests WHERE id=?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "Consent request not found")
    return _consent_dict(r)


@app.post("/meetings/consent", tags=["meetings"])
def create_consent(body: ConsentRequest) -> Dict[str, Any]:
    """The organiser lists the participants. Nobody has agreed yet."""
    people = []
    for p in body.participants:
        p = p.strip()
        if p and p not in people:
            people.append(p)
    if not people:
        raise HTTPException(422, "Add at least one participant.")
    with db.conn() as c:
        cur = c.execute("INSERT INTO consent_requests (created, title, organiser, participants) VALUES (?,?,?,?)",
                        (db.now_iso(), body.title.strip() or "Team meeting", body.organiser,
                         json.dumps(people, ensure_ascii=False)))
        return _consent_or_404(c, cur.lastrowid)


@app.get("/meetings/consent", tags=["meetings"])
def pending_consents(pending_for: str) -> List[Dict[str, Any]]:
    """Open requests where this person still has to answer (for their own device)."""
    with db.conn() as c:
        rows = [_consent_dict(r) for r in c.execute(
            "SELECT * FROM consent_requests WHERE meeting_id IS NULL ORDER BY created DESC").fetchall()]
    return [r for r in rows if r["status"] == "waiting" and pending_for in r["participants"]
            and pending_for not in r["answers"]]


@app.get("/meetings/consent/{rid}", tags=["meetings"])
def get_consent(rid: int) -> Dict[str, Any]:
    with db.conn() as c:
        return _consent_or_404(c, rid)


@app.post("/meetings/consent/{rid}/answer", tags=["meetings"])
def answer_consent(rid: int, body: ConsentAnswer) -> Dict[str, Any]:
    """A participant agrees or says no, for themselves only."""
    with db.conn() as c:
        req = _consent_or_404(c, rid)
        if req["status"] == "used":
            raise HTTPException(409, "This meeting was already recorded.")
        if body.name not in req["participants"]:
            raise HTTPException(404, "%s is not a participant." % body.name)
        if body.by != body.name and not body.shared_device:
            raise HTTPException(403, "Only %s can answer for themselves." % body.name)
        prev = req["answers"].get(body.name)
        if prev and prev.get("agreed") is False:
            raise HTTPException(409, "%s already said no." % body.name)
        req["answers"][body.name] = {"agreed": body.agree, "at": db.now_iso(),
                                     "on": "shared device" if body.by != body.name else "own device"}
        c.execute("UPDATE consent_requests SET answers=? WHERE id=?",
                  (json.dumps(req["answers"], ensure_ascii=False), rid))
        return _consent_or_404(c, rid)


@app.post("/meetings", tags=["meetings"])
async def create_meeting(
    title: str = Form(...),
    participants: str = Form("", description="Ignored when consent_request is given (its participants are used)"),
    consent_request: Optional[int] = Form(None, description="Consent request where every participant agreed"),
    text: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    audio: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    """Record a meeting and keep the know-how: decisions with reasons, problems and
    solutions, lessons, action items. Lasting points become draft cards. Consent is
    required: recording without it is illegal in Germany."""
    if not consent_request:
        raise HTTPException(400, "Consent required: every participant must agree to the recording first.")
    with db.conn() as c:
        req = _consent_or_404(c, consent_request)
    if req["status"] != "ready":
        who = ", ".join(req["declined_by"]) or ", ".join(p for p in req["participants"] if p not in req["answers"])
        raise HTTPException(400, {"declined": "Not recorded: %s said no." % who,
                                  "used": "This consent was already used for a meeting.",
                                  }.get(req["status"], "Waiting for consent from: %s." % who))
    consent_log = [{"name": p, "agreed_at": req["answers"][p]["at"], "on": req["answers"][p]["on"]}
                   for p in req["participants"]]
    participants = ", ".join(req["participants"])
    transcript, audio_path = await _text_from(text, audio, language)
    _delete_audio(audio_path)  # meetings never keep audio; only extracted knowledge stays
    with db.conn() as c:
        known = db.known_machine_names(c)
    result = ai.extract_meeting(transcript, known)
    # machine per problem: what the AI found, else the meeting title, else the transcript
    fallback = db.find_machine_in_text(title, known) or db.find_machine_in_text(transcript, known)
    for p in result["problems"]:
        p["machine"] = db.match_machine(p.get("machine"), known) or fallback
    people = [p.strip() for p in participants.split(",") if p.strip()]
    author = people[0] if people else "Meeting"
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO meetings (date, title, participants, transcript, result, consent_log) VALUES (?,?,?,?,?,?)",
            (db.now_iso(), title, json.dumps(people, ensure_ascii=False), transcript,
             json.dumps(result, ensure_ascii=False), json.dumps(consent_log, ensure_ascii=False)),
        )
        meeting_id = cur.lastrowid
        c.execute("UPDATE consent_requests SET meeting_id=? WHERE id=?", (meeting_id, consent_request))
        new_ids = []
        for p in result["problems"]:
            new_ids.append(db.insert_card(c, {
                "author": author, "machine": p.get("machine"), "process": title, "kind": "fix",
                "problem": p["problem"], "solution": p.get("solution"), "tags": ["meeting"],
                "source": "meeting", "meeting_id": meeting_id, "transcript": None}))
        for d in result["decisions"]:
            new_ids.append(db.insert_card(c, {
                "author": author, "machine": fallback, "process": title, "kind": "decision", "problem": d["decision"],
                "cause": d.get("reason"), "tags": ["decision"], "source": "meeting",
                "meeting_id": meeting_id}))
        for lesson in result["lessons_learned"]:
            new_ids.append(db.insert_card(c, {
                "author": author, "machine": fallback, "process": title, "kind": "lesson", "problem": lesson,
                "tags": ["lesson"], "source": "meeting", "meeting_id": meeting_id}))
        cards = [db.get_card(c, i) for i in new_ids]
    return {"meeting_id": meeting_id, "title": title, "participants": people, "consent_log": consent_log,
            "transcript": transcript, **result, "cards": cards}


@app.get("/meetings", tags=["meetings"])
def list_meetings() -> List[Dict[str, Any]]:
    with db.conn() as c:
        rows = c.execute("SELECT id, date, title, participants, result, consent_log FROM meetings ORDER BY date DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["participants"] = json.loads(d["participants"])
        d["consent_log"] = json.loads(d["consent_log"] or "[]")
        d.update(json.loads(d.pop("result")))
        out.append(d)
    return out


# ------------------------------------------------------------------ frontend

# Served last, so API routes win. The app uses hash routes (#/m/cnc-3), so no clash.
_FRONTEND = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(_FRONTEND):
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")
