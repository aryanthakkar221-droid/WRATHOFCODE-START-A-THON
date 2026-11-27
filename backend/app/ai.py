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


_SMALLTALK = re.compile(r"\b(how (was|is|are) (every(one|body)|you|your)|how'?s (it going|everyone|your)|hello|hi there|good (morning|night)|joke|weather|"
                        r"wie geht'?s|wie war (euer|dein|der) tag|witz|wetter|love|birthday|football|fussball)\b", re.I)
_WORK = re.compile(r"(machine|maschine|press|compressor|kompressor|packag|verpack|jam|stau|error|fehler|alarm|pressure|druck|"
                   r"oil|öl|leak|fix|repair|clean|change|how do i|wie (mache|kann)|e-?\d{2,}|motor|belt|sensor|label|film|part|teil)", re.I)


_GENERAL = re.compile(r"^\s*(hey|hi|hello|hallo|moin|servus|thanks|thank you|danke|good (morning|evening)|"
                      r"what (is|are|does)|was (ist|sind|bedeutet)|explain|erkl[äa]r|define|who is|wer ist)\b", re.I)


def is_work_question(question: str) -> bool:
    """True only for work FIX questions: a problem with a machine/process, or how to do a specific
    work task. Small talk and general questions (e.g. "what is film tension?") are False: they get
    a labelled general answer, are not searched in the cards and never go to the experts."""
    out = _call_json(
        "Classify a factory worker's chat message. is_work_question is true only for a WORK FIX question: "
        "a concrete problem with a machine or process (it jams, an error, parts are wrong, pressure drops) "
        "or how to do a specific work task on a machine. It is false for greetings, small talk, jokes, "
        "personal or off-topic messages, and for general knowledge questions such as definitions "
        "('what is film tension?').",
        question,
        {"type": "object", "additionalProperties": False, "required": ["is_work_question"],
         "properties": {"is_work_question": {"type": "boolean"}}}, "classify")
    if out is not None:
        return bool(out["is_work_question"])
    if _GENERAL.search(question) or _SMALLTALK.search(question):
        return False
    return bool(_WORK.search(question)) or len(question.split()) > 3


_FOLLOWUP = re.compile(r"^\s*(and|but|what if|what about|und|aber|was wenn|und wenn|that|this|it|das|es)\b|\b(that|this|it|das|es)\b.{0,25}\?\s*$", re.I)


def standalone_question(question: str, history: List[Dict[str, Any]]) -> str:
    """Rewrite a follow-up ("and what if that doesn't help?") into a standalone question using the
    chat history of this session. History is only used for this call and never stored."""
    users = [h["text"] for h in history if h.get("role") == "user"]
    if not users:
        return question
    convo = "\n".join("%s: %s" % (h.get("role"), h.get("text", "")[:400]) for h in history[-6:])
    out = _call_json(
        "Rewrite the user's last message into ONE standalone question that makes sense without the "
        "conversation. Replace every reference (it, that, this, the machine, es, das) with what it refers to "
        "in the earlier user questions, and keep the problem/machine named. Example: after 'The packaging "
        "machine jams, what do I do?', the message 'and what if that doesn't help?' becomes 'The packaging "
        "machine still jams after trying the fix, what else can I do?'. Keep the user's language. If the "
        "message is a new, unrelated topic (e.g. a greeting), return it unchanged.",
        "Conversation:\n%s\n\nLast message: %s" % (convo, question),
        {"type": "object", "additionalProperties": False, "required": ["question"],
         "properties": {"question": {"type": "string"}}}, "standalone")
    if out and out.get("question", "").strip():
        q = out["question"].strip()
        # safety net: still only "that/it" and no topic from before -> attach the previous question
        if _FOLLOWUP.search(q) and not set(re.findall(r"\w{4,}", users[-1].lower())) & set(re.findall(r"\w{4,}", q.lower())) - {"what", "does", "help", "that", "this"}:
            return "%s %s" % (users[-1], question)
        return q
    if _FOLLOWUP.search(question) or len(question.split()) <= 6:
        return "%s %s" % (users[-1], question)
    return question


def _suggestion_fallback(f: Dict[str, Any]) -> str:
    if f["kind"] == "open_questions":
        who = "colleague asked" if f["count"] == 1 else "colleagues asked"
        return "%d %s about the %s: \u201c%s\u201d Add a card?" % (f["count"], who, f["machine"], f["example"])
    if f["kind"] == "challenge_often_wrong":
        return ("New workers often get the \u201c%s\u201d challenge wrong (%d of %d answers). Add a clearer tip?"
                % (f["card"].rstrip("."), f["wrong"], f["total"]))
    return "The fix for \u201c%s\u201d failed %d times. Please review." % (f["card"].rstrip("."), f["failed"])


def review_suggestions(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Up to 3 short 'How you can help' suggestions for an expert, written by the AI from
    knowledge-gap facts (counts only). Never names or judges individual employees."""
    if not facts:
        return []
    clean = [{k: v for k, v in f.items() if k not in ("action", "card_id", "question")} for f in facts]
    out = _call_json(
        "You write short suggestions (max 20 words each) for a senior factory expert about where their "
        "knowledge is missing. Exactly one suggestion per fact, same order. Each suggestion MUST contain the "
        "number from the fact (count, wrong or failed) and the machine or card name. Never mention, guess "
        "or judge any individual person. Style examples:\n"
        "open_questions -> '3 colleagues asked about the Compressor oil light. Add a card?'\n"
        "challenge_often_wrong -> 'New workers often miss the Packaging Machine film-jam challenge (5 of 7 "
        "answers wrong). Add a clearer tip?'\n"
        "fix_failed -> 'The fix for \\'Steel parts come out rough\\' failed twice. Please review.'",
        json.dumps(clean, ensure_ascii=False),
        {"type": "object", "additionalProperties": False, "required": ["suggestions"],
         "properties": {"suggestions": {"type": "array", "items": {"type": "string"}}}}, "suggestions")
    texts = (out or {}).get("suggestions") or []
    res = []
    for i, f in enumerate(facts[:3]):
        t = texts[i].strip() if i < len(texts) and texts[i].strip() else _suggestion_fallback(f)
        res.append({"text": t, "kind": f["kind"], "action": f["action"],
                    "question": f.get("question"), "card_id": f.get("card_id")})
    return res


_APP_Q = re.compile(r"approv|review|draft|token|wallet|balance|earn|question|task|to ?do|for me|freigabe|offen|aufgabe", re.I)


def _context_answer(question: str, ctx: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Offline: answer app questions only from the real context."""
    q = question.lower()
    if re.search(r"approv|review|draft|freigabe", q):
        d = ctx.get("drafts_for_you_to_approve") or []
        if not ctx.get("is_expert"):
            own = ctx.get("your_drafts_waiting_for_an_expert") or []
            return {"answer": "Only experts approve cards. %s" % (
                "You have %d card waiting for an expert: '%s'." % (len(own), own[0]["title"]) if own else "You have no cards waiting."),
                "link": "dashboard" if own else "none"}
        if not d:
            return {"answer": "Nothing is waiting for your approval right now.", "link": "none"}
        return {"answer": "You have %d card%s waiting for approval: %s." % (len(d), "" if len(d) == 1 else "s",
                ", ".join("'%s' (%s)" % (x["title"], x["machine"]) for x in d[:3])), "link": "dashboard"}
    if re.search(r"token|wallet|balance|earn", q):
        t = ctx.get("tokens") or {}
        last = t.get("last_earning")
        return {"answer": "You have %s tokens.%s" % (t.get("balance", 0), (" Last: +%d, %s." % (last["amount"], last["reason"])) if last else ""),
                "link": "wallet"}
    if re.search(r"question|offen", q):
        oq = ctx.get("open_questions_on_your_machines") or []
        return {"answer": ("%d open question%s on your machines, e.g. '%s'." % (len(oq), "" if len(oq) == 1 else "s", oq[0]["text"])) if oq
                else "No open questions on your machines.", "link": "dashboard" if oq else "none"}
    return None


def general_answer(question: str, person: Optional[str], context: Optional[Dict[str, Any]] = None,
                   first: bool = True) -> Dict[str, str]:
    """Friendly, short general chat answer. Never repair steps, never guesses about the app:
    approvals, questions, tokens and tasks come only from `context` (read from the database).
    Greets by name only in the first reply of a chat session. Nothing is stored."""
    name = (person or "").strip()
    ctx = context or {}
    out = _call_json(
        "You are KnowFlow's friendly assistant in a factory app. Answer briefly (1-3 short sentences), like a "
        "normal chat assistant. "
        + ("Greet the person by name once. " if first and name else
           "Do NOT greet and do NOT use the person's name; answer directly. ")
        + "You may explain general concepts in plain words. NEVER give machine repair, troubleshooting or "
        "step-by-step fix instructions: for those, say to ask the concrete problem so KnowFlow can answer from "
        "the experts' approved cards. Never pretend to be one of the experts.\n"
        "APP FACTS: answer anything about approvals, drafts, open questions, tokens or the person's tasks ONLY "
        "from the CONTEXT JSON. Mention items with counts and titles, e.g. \"You have 1 card waiting for "
        "approval: 'The door does not open after the program ends' (Machine 1).\" If the context does not "
        "contain the answer, say exactly: \"I can't see that. Check the Dashboard.\" Never claim that "
        "everything is fine or running smoothly. Set link to 'dashboard' for approvals/questions/tasks with "
        "items, 'wallet' for tokens, else 'none'.",
        "CONTEXT: %s\nPerson: %s\nMessage: %s" % (json.dumps(ctx, ensure_ascii=False), name or "unknown", question),
        {"type": "object", "additionalProperties": False, "required": ["answer", "link"],
         "properties": {"answer": {"type": "string"}, "link": {"type": "string", "enum": ["dashboard", "wallet", "none"]}}},
        "general")
    if out and out.get("answer", "").strip():
        return {"answer": out["answer"].strip(), "link": out.get("link", "none")}
    if _APP_Q.search(question):
        got = _context_answer(question, ctx)
        if got:
            return got
        return {"answer": "I can't see that. Check the Dashboard.", "link": "dashboard"}
    hi = ("Hi %s! " % name) if (first and name) else ""
    if re.match(r"^\s*(hey|hi|hello|hallo|moin|servus|good)", question, re.I):
        return {"answer": (hi or "Hi! ") + "How can I help? Ask me about a machine problem and I'll answer from your experts' approved cards.", "link": "none"}
    return {"answer": hi + "That's a general question. For a concrete machine problem, describe what happens and I'll answer from the approved cards.", "link": "none"}


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
