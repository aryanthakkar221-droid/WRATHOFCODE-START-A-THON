"""AI layer: structure shift notes, answer questions, extract meeting knowledge.

Uses OpenAI when OPENAI_API_KEY is set (structured JSON outputs, so replies always parse).
Without a key, or if a call fails, it falls back to simple offline rules, so the demo
never dies on stage.
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger("knowflow.ai")

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

_client = None


def client():
    global _client
    if _client is None and os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI

        _client = OpenAI(timeout=60.0, max_retries=2)
    return _client


def ai_enabled() -> bool:
    return client() is not None


def _call_json(system: str, user: str, schema: Dict[str, Any], name: str = "result") -> Optional[Dict[str, Any]]:
    c = client()
    if c is None:
        return None
    try:
        resp = c.chat.completions.create(
            model=MODEL,
            temperature=0.2,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
        )
        choice = resp.choices[0]
        if choice.finish_reason != "stop" or getattr(choice.message, "refusal", None):
            log.warning("OpenAI finish_reason=%s, using offline fallback", choice.finish_reason)
            return None
        return json.loads(choice.message.content)
    except Exception as e:  # noqa: BLE001 - the demo must keep working
        log.warning("OpenAI call failed (%s), using offline fallback", e)
        return None


def _nullable(t: str) -> Dict[str, Any]:
    return {"type": [t, "null"]}


# ---------------------------------------------------------------- structuring

STRUCTURE_SYSTEM = (
    "You are an assistant for a factory shift log (Schichtbuch). Turn this worker's spoken "
    "shift report into a knowledge card. Keep the worker's practical tips and reasons (the "
    "'why'). If something is not mentioned, use null. Do not invent facts. Write in clear, "
    "short sentences, in the same language the worker used. If the report contains no real "
    "work problem, fix or tip (e.g. it is empty, noise, or small talk), return null for "
    "problem, cause, solution and warning and an empty tags list. kind is 'mistake' if the report "
    "is mainly about something that went wrong and should not be repeated, otherwise 'fix'. "
    "tags are 1-4 short lowercase English keywords for the problem type (e.g. 'spindle', "
    "'noise'), always English so patterns match across languages. If the machine matches one "
    "of the known machines (also when said in another language, e.g. 'Verpackungslinie' = "
    "'Packaging line'), use that exact known name. If no machine is mentioned, machine is "
    "'unknown'. learned_from: if the worker says a "
    "colleague told or showed them the fix, that colleague's name exactly as in the list of "
    "experts if given, else null. nobody_could_help: true only if the problem was NOT solved "
    "and the worker says nobody could help or nobody knew."
)

CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "machine": _nullable("string"),
        "process": _nullable("string"),
        "kind": {"type": "string", "enum": ["fix", "mistake"]},
        "problem": _nullable("string"),
        "cause": _nullable("string"),
        "solution": _nullable("string"),
        "warning": _nullable("string"),
        "tags": {"type": "array", "items": {"type": "string"}},
        "learned_from": _nullable("string"),
        "nobody_could_help": {"type": "boolean"},
    },
    "required": ["machine", "process", "kind", "problem", "cause", "solution", "warning", "tags",
                 "learned_from", "nobody_could_help"],
    "additionalProperties": False,
}


def structure_entry(transcript: str, machine_hint: Optional[str] = None,
                    known_machines: Optional[List[str]] = None,
                    known_tags: Optional[List[str]] = None,
                    experts: Optional[List[str]] = None) -> Dict[str, Any]:
    user = transcript if not machine_hint else "Machine: %s\n\n%s" % (machine_hint, transcript)
    if experts:
        user = "Experts: %s\n\n%s" % (", ".join(experts), user)
    if known_machines and not machine_hint:
        user = "Known machines: %s\n\nShift report:\n%s" % (", ".join(known_machines), user)
    if known_tags:
        user = ("Tags already used (reuse the same tag for the same kind of problem, e.g. any "
                "jam is 'jam', so repeats can be spotted): %s\n\n%s" % (", ".join(known_tags), user))
    out = _call_json(STRUCTURE_SYSTEM, user, CARD_SCHEMA, "card")
    if out is None:
        out = _offline_structure(transcript)
        out["learned_from"] = find_learned_from(transcript, experts or [])
        out["nobody_could_help"] = bool(_NOBODY.search(transcript))
    if machine_hint:
        out["machine"] = machine_hint
    out["tags"] = [t.lower().strip() for t in (out.get("tags") or []) if t.strip()][:4]
    return out


_CAUSE_WORDS = ("because", "weil", "cause", "ursache", "due to", "da ")
_FIX_WORDS = ("fixed", "solved", "solution", "gelöst", "replace", "clean", "turn", "check", "let it", "tausch", "reinig")
_WARN_WORDS = ("watch out", "careful", "never", "achtung", "vorsicht", "nie ", "don't", "do not")
_STOP = set(("the a an and or to of in on at is was it for with my we i our this that be at how what why when where "
              "who which do does did can could should you your there then than make makes again der die das und "
              "ist was wie wenn ich mit bei auf").split())


def _offline_structure(text: str) -> Dict[str, Any]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    pick = lambda words: next((s for s in sentences if any(w in s.lower() for w in words)), None)  # noqa: E731
    warning = pick(_WARN_WORDS)
    cause = pick(_CAUSE_WORDS)
    solution = pick(_FIX_WORDS)
    problem = next((s for s in sentences if s not in (warning, cause, solution)), sentences[0] if sentences else None)
    words = [w for w in re.findall(r"[a-zäöüß]{4,}", (problem or "").lower()) if w not in _STOP]
    return {
        "machine": None,
        "process": None,
        "kind": "mistake" if re.search(r"mistake|fehler|went wrong|schiefgelaufen", text, re.I) else "fix",
        "problem": problem,
        "cause": cause,
        "solution": solution,
        "warning": warning,
        "tags": words[:3],
    }


_NOBODY = re.compile(r"nobody|no one|no-one|niemand|keiner (wusste|konnte)|could(n't| not) help|"
                     r"konnte (mir )?nicht helfen", re.I)
_TOLD = r"(told|said|showed|explained|helped|says|sagte|meinte|zeigte|gezeigt|erklärt|hat mir)"


def find_learned_from(text: str, experts: List[str]) -> Optional[str]:
    """Offline rule: an expert's name (or surname) near 'told me / showed me / sagte'."""
    for name in experts:
        short = re.sub(r"^(Herr|Frau)\s+", "", name)
        for n in {name, short}:
            pat = r"(%s)\W+(\w+\W+){0,3}%s|%s\W+(\w+\W+){0,3}(%s)" % (re.escape(n), _TOLD, _TOLD, re.escape(n))
            if re.search(pat, text, re.I):
                return name
    return None


# ---------------------------------------------------------------- answering

ASK_SYSTEM = (
    "You help a new factory employee. Answer the question using only the knowledge cards "
    "provided. Give the steps in order, mention the warning, and for each step cite the card "
    "id it came from. Prefer cards with higher confidence and skip cards marked outdated "
    "unless nothing else fits. If the cards don't answer it, set answered=false, leave steps "
    "empty and in suggest_ask name the author who knows that machine best (or null). "
    "Answer in the language of the question."
)

ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "answered": {"type": "boolean"},
        "summary": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "card_id": {"type": "integer"}},
                "required": ["text", "card_id"],
                "additionalProperties": False,
            },
        },
        "warning": _nullable("string"),
        "suggest_ask": _nullable("string"),
    },
    "required": ["answered", "summary", "steps", "warning", "suggest_ask"],
    "additionalProperties": False,
}


def _card_brief(c: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("id", "date", "author", "author_years_experience", "machine", "kind", "problem",
            "cause", "solution", "warning", "tags", "confidence", "outdated")
    return {k: c.get(k) for k in keys}


def answer_question(question: str, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    if cards:
        payload = json.dumps([_card_brief(c) for c in cards], ensure_ascii=False)
        out = _call_json(ASK_SYSTEM, "Knowledge cards:\n%s\n\nQuestion: %s" % (payload, question), ASK_SCHEMA, "answer")
        if out is not None:
            valid = {c["id"] for c in cards}
            out["steps"] = [s for s in out["steps"] if s["card_id"] in valid]
            if not out["steps"]:
                out["answered"] = False
            if not out["answered"] and not out["summary"].strip():
                out["summary"] = NO_ANSWER
            return out
    return _offline_answer(question, cards)


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9äöüß\-]{3,}", s.lower()) if w not in _STOP}


def rank_cards(question: str, cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    q = _tokens(question)
    qnorm = re.sub(r"\s+", "", question.lower())
    scored = []
    for c in cards:
        hay = " ".join(str(c.get(k) or "") for k in ("machine", "process", "problem", "cause", "solution", "warning"))
        hay += " " + " ".join(c.get("tags") or [])
        score = float(len(q & _tokens(hay)))
        if c.get("machine") and re.sub(r"\s+", "", c["machine"].lower()) in qnorm:
            score += 3
        if score < 1:  # needs a real match; confidence only breaks ties
            continue
        score += c["confidence"] - (1 if c["outdated"] else 0)
        if score >= 1.5:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored]


NO_ANSWER = "No entry answers this yet. It was added to the open questions for the experts."


def _offline_answer(question: str, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    hits = [c for c in rank_cards(question, cards) if c.get("solution")][:3]
    if not hits:
        authors = [c["author"] for c in cards]
        return {
            "answered": False,
            "summary": NO_ANSWER,
            "steps": [],
            "warning": None,
            "suggest_ask": max(set(authors), key=authors.count) if authors else None,
        }
    return {
        "answered": True,
        "summary": hits[0]["problem"] or "Here is what the team has logged.",
        "steps": [{"text": c["solution"], "card_id": c["id"]} for c in hits],
        "warning": next((c["warning"] for c in hits if c.get("warning")), None),
        "suggest_ask": None,
    }


# ---------------------------------------------------------------- meetings

MEETING_SYSTEM = (
    "From this meeting transcript, extract decisions (decision + reason), problems (problem + "
    "solution, with the machine if one is named), lessons_learned, and action_items (task, "
    "owner, due). Only include points useful later to someone who was not in the meeting. "
    "Skip small talk. Do not invent facts; use null for unknown owner/due/machine."
)

MEETING_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"decision": {"type": "string"}, "reason": _nullable("string")},
                "required": ["decision", "reason"],
                "additionalProperties": False,
            },
        },
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "problem": {"type": "string"},
                    "solution": _nullable("string"),
                    "machine": _nullable("string"),
                },
                "required": ["problem", "solution", "machine"],
                "additionalProperties": False,
            },
        },
        "lessons_learned": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"task": {"type": "string"}, "owner": _nullable("string"), "due": _nullable("string")},
                "required": ["task", "owner", "due"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "decisions", "problems", "lessons_learned", "action_items"],
    "additionalProperties": False,
}


def extract_meeting(transcript: str, known_machines: Optional[List[str]] = None) -> Dict[str, Any]:
    user = transcript
    if known_machines:
        user = ("Known machines (for 'machine' use one of these exact names, also when said in "
                "another language, or null): %s\n\nTranscript:\n%s" % (", ".join(known_machines), transcript))
    out = _call_json(MEETING_SYSTEM, user, MEETING_SCHEMA, "meeting")
    if out is not None:
        return out
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", transcript) if s.strip()]
    has = lambda s, *w: any(x in s.lower() for x in w)  # noqa: E731
    return {
        "summary": sents[0] if sents else "",
        "decisions": [{"decision": s, "reason": None} for s in sents if has(s, "decided", "we will", "beschlossen", "entschieden")],
        "problems": [{"problem": s, "solution": None, "machine": None} for s in sents if has(s, "problem", "issue", "broke", "jam")],
        "lessons_learned": [s for s in sents if has(s, "lesson", "learned", "next time", "gelernt", "nächstes mal")],
        "action_items": [{"task": s, "owner": None, "due": None} for s in sents if has(s, "will ", "todo", "to do", "bis ")],
    }


# ---------------------------------------------------------------- fix-it challenge

CHALLENGE_SYSTEM = (
    "You turn one expert knowledge card from a factory into a short training game for a new "
    "employee. Use simple, everyday words, no technical jargon. Write: a short situation "
    "(2-3 sentences, present tense, 'you' form, at the machine), a question, and exactly 4 "
    "possible next steps. Exactly one is right: it must follow the expert's solution. The 3 "
    "wrong ones must sound tempting (things a beginner would really try). For every option "
    "write what happens next (1 sentence). Then 'explanation': why the right step works, in "
    "neutral third person (1-2 sentences). Never write as the expert, never use 'I', and "
    "never invent quotes. Then a 2-4 word badge name. "
    "Only use facts from the card. Write in English."
)

CHALLENGE_SCHEMA = {
    "type": "object",
    "properties": {
        "situation": {"type": "string"},
        "question": {"type": "string"},
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "correct": {"type": "boolean"},
                    "consequence": {"type": "string"},
                },
                "required": ["text", "correct", "consequence"],
                "additionalProperties": False,
            },
        },
        "explanation": {"type": "string"},
        "badge": {"type": "string"},
    },
    "required": ["situation", "question", "options", "explanation", "badge"],
    "additionalProperties": False,
}


def make_challenge(card: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps(_card_brief(card), ensure_ascii=False)
    out = _call_json(CHALLENGE_SYSTEM, "Knowledge card:\n" + payload, CHALLENGE_SCHEMA, "challenge")
    if out is not None:
        right = [o for o in out["options"] if o["correct"]]
        if len(right) == 1 and 3 <= len(out["options"]) <= 4:
            return out
        log.warning("Challenge had %d right answers, using offline version", len(right))
    return _offline_challenge(card)


def _offline_challenge(card: Dict[str, Any]) -> Dict[str, Any]:
    machine = card.get("machine") or "the machine"
    return {
        "situation": "You are at the %s. %s" % (machine, card.get("problem") or "Something is wrong."),
        "question": "What do you do first?",
        "options": [
            {"text": card.get("solution") or "Ask an expert.", "correct": True,
             "consequence": "It works, and the machine runs normally again."},
            {"text": "Restart it at full speed and hope it goes away.", "correct": False,
             "consequence": card.get("warning") or "The same problem comes back, and it may get worse."},
            {"text": "Keep pressing reset until it runs.", "correct": False,
             "consequence": "It stops again a few minutes later. The cause is still there."},
            {"text": "Leave it and wait for the next shift.", "correct": False,
             "consequence": "Production stands still, and the next shift has the same problem."},
        ],
        "explanation": " ".join(x for x in [
            ("The cause: %s" % card["cause"]) if card.get("cause") else "",
            card.get("solution") or "",
            ("Watch out: %s" % card["warning"]) if card.get("warning") else "",
        ] if x),
        "badge": "%s helper" % machine,
    }
