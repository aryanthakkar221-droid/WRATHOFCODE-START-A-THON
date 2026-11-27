<div align="center">

# KnowFlow

### Knowledge that doesn't retire.

**The AI Schichtbuch for manufacturing SMEs.**
Workers talk for 2 minutes at the end of each shift. The AI turns it into searchable know-how that new employees learn from, and that stays in the company when experienced people leave.

`Problem 03 · When the experts retire` · 8.8.8 Start-a-thon · THWS Campus 3 · 27 Nov 2026 · Team WRATHOFCODE

</div>

---

## The problem

Baby boomers are retiring, and with them go experienced technicians, foremen and engineers. What they know is written down nowhere: why CNC 3 makes that noise, the trick for the packaging jam, what to check first. When they leave, the company loses it. New people learn it the hard way, through downtime, mistakes and slow onboarding.

Most factories already keep a **Schichtbuch** (shift log), but it is a paper book or an Excel file that nobody reads again. And asking experts to "write documentation" fails: they don't have the time, and they don't like writing.

## Our solution

An AI-powered Schichtbuch. Every worker logs their shift **by voice in about 2 minutes**. The AI asks three questions, turns the answers into a clean knowledge card, and builds a searchable knowledge base over time, without anyone "documenting".

```mermaid
flowchart LR
    A["🎙️ Record<br/>2-min voice note<br/>at end of shift"] --> B["🧠 Structure<br/>AI writes a card:<br/>problem → cause →<br/>solution → warning"]
    B --> C["✅ Approve<br/>Expert checks it.<br/>Audio is deleted."]
    C --> D["🔎 Find & learn<br/>New employee asks<br/>in plain language<br/>or scans the QR code"]
    D --> E["👍 Did it work?<br/>Proven fixes rise,<br/>wrong ones get flagged"]
    E -.-> D
    D -- "no answer yet" --> F["❓ Closed loop<br/>Question goes to<br/>the right expert"]
    F -.-> A
    C --> G["📊 Spot patterns<br/>Recurring problems,<br/>single-expert risks"]
```

| Step | What happens |
|---|---|
| **1. Record** | At the end of the shift the worker taps one button and talks. The AI asks: *What was difficult today? What did you solve, and how? What should the next person watch out for?* |
| **2. Structure** | The AI turns the messy voice note into a card: problem → cause → solution → warning, tagged with machine, process, author and date. |
| **3. Find and learn** | A new employee asks in plain language ("packaging line jams, what do I do?") and gets the answer from real past entries, with who solved it and when. |
| **4. Spot patterns** | The dashboard flags repeats and risks: "Spindle noise at CNC 3: 4x this month", "Only Herr Müller has solved Press 2 problems, and he retires in 6 months." |

## Features

**Core (built in this prototype)**

- 🎙️ **Voice, not typing.** 2 minutes at the machine, in normal German. Whisper handles the speech-to-text.
- 🧠 **The AI does the documenting.** Messy talk in, clean structured card out. It never invents facts: missing fields stay empty.
- ✅ **Expert approves before it goes live.** The AI drafts the card, and the expert can edit, approve or reject it.
- 📱 **A QR code on every machine.** Scan it to see the experts' approved fixes for exactly that machine, proven ones first.
- 👍 **"Did this work?" button.** Cards show their confidence and "last confirmed" date. Fixes that fail more often than they work are flagged as outdated.
- ❓ **Closed loop.** A question nobody could answer becomes the next question the AI asks the expert who knows that machine.
- ⚠️ **Mistake library.** Cards can record "what went wrong", not only "what worked".
- 🗣️ **Meeting recording.** Recording starts only after everyone consents. It pulls out decisions with their reasons, problems and solutions, lessons, and action items with owners. Lasting points become cards.
- 🗺️ **Knowledge-risk map.** Shows which machines depend on one person who retires soon.
- 🌍 **Language bridge.** Ask in any language and get the answer in that language, built from German expert entries.

**Roadmap (in the pitch, not in the demo):** video capture for hands-on fixes · reading existing manuals first · event-triggered questions from machine/MES faults · contradiction check between experts · learning levels and quizzes for new hires · promoting good fixes to the official process, with version history.

### Privacy by design

> **This is for sharing knowledge, never for judging employees.** Entries are about the work, not the person. There is no performance scoring. In Germany, monitoring performance would also need the works council's agreement.

- Shift audio is **deleted once the card is approved**. Meeting audio is never stored. Only the approved text is kept.
- Meetings cannot be recorded without `consent=true` from everyone (the API returns `400` without it). Recording a conversation without consent is illegal in Germany.
- Every card carries the expert's name. **Credit, not control:** it is their legacy.

## Demo story (2 minutes)

1. **"Herr Müller"** (32 years, retires in 6 months) records a shift note by voice, and a clean card appears. He approves it.
2. **Dashboard:** "Spindle noise at CNC 3: 4x this month" and "Only Herr Müller has solved problems on Press 2 (retires in 6 months)".
3. **"Ahmed"** (1 year in) asks: *"CNC 3 makes a noise when starting, what do I do?"* He gets Müller's steps, each with its source.
4. **The jury scans the QR code** on a printed "CNC 3" sign with their own phones and sees Müller's approved fix.
5. *Optional:* a 30-second team meeting turns into decisions and lessons.
6. **Close:** *"Herr Müller retires in 6 months. His answers stay."*

The seed data is built around this story. Run `POST /admin/reset-demo` between rehearsals to reset it.

---

## Tech

| Part | Choice | Why |
|---|---|---|
| Backend | **Python · FastAPI** | A few clear endpoints, with auto-generated docs at `/docs` |
| Brain | **OpenAI** chat model with strict JSON-schema outputs | Structuring, answers and meeting extraction always return valid JSON |
| Speech-to-text | **OpenAI Whisper** | Handles German shop-floor speech well |
| Storage | **SQLite** | One file, zero setup, enough for a pilot |
| Search | All approved cards plus the question go into one prompt | With 15–500 cards that fits in one prompt, so no vector DB is needed |
| QR codes | `segno` → SVG | Printable, scales cleanly |
| Frontend | **Plain HTML/CSS/JS**, mobile-first, served by the backend | No build step, one URL for app + API, works on any phone |
| Voice in the browser | `MediaRecorder` API | Records audio in the browser, no app install |

**Offline fallback:** if there's no API key, or the AI provider fails mid-pitch, the backend falls back to simple offline rules for structuring and keyword search. The demo never dies on stage.

```
WRATHOFCODE-START-A-THON/
├── backend/
│   ├── app/
│   │   ├── main.py      # FastAPI routes
│   │   ├── ai.py        # OpenAI brain + offline fallback (structure, ask, meetings)
│   │   ├── stt.py       # Whisper speech-to-text
│   │   ├── alerts.py    # pattern alerts + knowledge-risk map (plain rules)
│   │   ├── db.py        # SQLite schema + helpers
│   │   └── seed.py      # demo experts + 15 shift entries
│   ├── tests/test_api.py  # end-to-end test of the whole demo story
│   ├── requirements.txt
│   └── .env.example
└── frontend/            # the app (served at /)
    ├── index.html
    ├── styles.css
    └── app.js           # screens: Record · Ask · Meetings · Dashboard · QR machine page · printable sign
```

### Run it locally

```bash
git clone https://github.com/aryanthakkar221-droid/WRATHOFCODE-START-A-THON.git
cd WRATHOFCODE-START-A-THON/backend

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # add OPENAI_API_KEY (optional: it also runs offline)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** for the app and **http://localhost:8000/docs** for the API. Demo data loads on first start.

> **Microphone on phones needs HTTPS.** Recording works on `localhost` and on any HTTPS deployment. On plain `http://<LAN-IP>` phones fall back to "Type instead"; everything else (Ask, QR pages, Dashboard) works.

### The app

| Screen | What it does |
|---|---|
| 🎙️ **Record** | Big mic button; the 3 questions shown one by one; pulsing red dot + timer → "AI is writing your card…" → the card slides in → **Approve / Edit / Discard**. Shows a colleague's open question if you're the right expert. "Type instead" fallback. |
| 🔎 **Ask** | Ask in any language → numbered steps, each with a source badge ("from Herr Müller, 3 Nov · 32 yrs"), the warning, and the source cards with **Did this work? 👍 👎**. Links like `#/ask/<question>` run a question directly. |
| 👥 **Meetings** | Consent checkbox unlocks recording → red recording bar with timer → decisions, problems, lessons, action items → **Add to knowledge base**. |
| 📊 **Dashboard** | Stats, alerts, knowledge-risk map (machines coloured by risk), drafts waiting for approval, open questions, QR codes per machine, demo reset. |
| ⚙️ **Machine page** `#/m/<id>` | What the QR code opens on a phone: the machine's alerts and approved fixes, proven first. |
| 🖨️ **Sign** `#/sign/<id>` | Printable A4 sign with the machine name and QR code. |

Tap the name in the top-right to switch person (e.g. Herr Müller → Ahmed) during the demo.

Run the tests (offline, no key needed):

```bash
python -m pytest -q
```

### Configuration (`backend/.env`)

| Variable | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | – | Turns on the AI brain and speech-to-text |
| `OPENAI_MODEL` | `gpt-4o-mini` | Chat model for structuring, answers and meetings |
| `OPENAI_STT_MODEL` | `whisper-1` | Speech-to-text model |
| `KNOWFLOW_PUBLIC_URL` | this server | Public URL printed QR codes open (`<url>/#/m/<id>`) |
| `KNOWFLOW_SEED` | `1` | Load demo data on first start |
| `KNOWFLOW_ALLOW_RESET` | `1` | Allow `POST /admin/reset-demo` (set to `0` in production) |

### API

| Method | Endpoint | Input | Output |
|---|---|---|---|
| `POST` | `/entries` | form: `author`, `machine?`, `text` **or** `audio` file, `language?` | transcript + draft card |
| `POST` | `/entries/text` | JSON `{author, text, machine?}` | same, for browser speech recognition |
| `GET` | `/entries` | `?machine=cnc-3&status=approved` | list of cards |
| `GET` · `PATCH` · `DELETE` | `/cards/{id}` | `PATCH`: fields to correct | one card / edited card / deleted draft |
| `POST` | `/cards/{id}/approve` | `{expert}` | card goes live; audio deleted |
| `POST` | `/cards/{id}/feedback` | `{worked: true\|false}` | updated confidence + last confirmed date |
| `POST` | `/ask` | `{question, machine?, asked_by?}` | `answered`, `summary`, `steps[]` each with `source` (author, date, years, confidence), `warning`, `sources[]` |
| `GET` | `/open-questions` | `?expert=Herr Müller` | unanswered questions (closed loop) |
| `POST` | `/open-questions/{id}/resolve` | `{card_id}` | marks a question answered |
| `GET` | `/machines` | – | machines with card and expert counts |
| `GET` | `/machine/{id}` | from the QR scan | approved cards for the machine, proven first, plus its alerts |
| `GET` | `/machine/{id}/qr.svg` | – | printable QR code |
| `GET` | `/alerts` | – | recurring problems, knowledge risks, disputed fixes |
| `GET` | `/risk-map` | – | per machine: who knows it, who retires soon, risk level |
| `GET` | `/stats` | – | headline numbers for the dashboard |
| `POST` | `/meetings` | form: `title`, `participants`, `consent=true`, `text` **or** `audio` | decisions, problems, lessons, action items + new draft cards |
| `GET` | `/meetings` | – | past meetings |
| `GET` | `/health` | – | whether the AI and speech-to-text are on |
| `POST` | `/admin/reset-demo` | – | reloads the demo data |

<details>
<summary><b>Card data model</b></summary>

```jsonc
{
  "id": 3,
  "date": "2026-11-24T14:30:00+00:00",
  "author": "Herr Müller",
  "author_years_experience": 32,
  "machine": "CNC 3",            "machine_id": "cnc-3",
  "process": "Milling",
  "kind": "fix",                 // fix | mistake | decision | lesson
  "problem": "Spindle noise on Monday morning, third time this month.",
  "cause": "Probably the weekend heating is switched off in hall B.",
  "solution": "Warm-up as usual. Told maintenance to keep the heating on at weekends.",
  "warning": "If it is noisy after warm-up, stop the machine: bearing damage is expensive.",
  "tags": ["spindle", "noise", "heating"],
  "source": "shift",             // shift | meeting
  "status": "approved",          // draft | approved
  "approved_by": "Herr Müller",  "approved_at": "…",
  "worked_count": 1, "failed_count": 0, "last_confirmed": "…",
  "confidence": 0.67,            // (worked+1)/(worked+failed+2)
  "outdated": false,             // failed > worked, or not confirmed for a year
  "transcript": "…"
}
```
</details>

<details>
<summary><b>Pattern alert rules (no AI needed)</b></summary>

- **Recurring problem:** same machine + same tag **3 or more times in 30 days**. Severity is high at 4 or more.
- **Knowledge risk:** a machine's problems have been solved by **only one person**. Severity is high if that person retires within 12 months.
- **Disputed fix:** an approved fix that failed more often than it worked.
</details>

<details>
<summary><b>AI prompts</b></summary>

- **Structuring a shift entry:** *"Turn this worker's spoken shift report into a knowledge card … Keep the worker's practical tips and reasons (the 'why'). If something is not mentioned, use null. Do not invent facts."*
- **Answering a new employee:** *"Answer using only the knowledge cards provided. Give the steps in order, mention the warning, cite the card for each step … If the cards don't answer it, say so and suggest who to ask."*
- **Meetings:** *"Extract decisions (with reason), problems (with solution), lessons learned and action items (task, owner, due). Only include points useful later to someone who was not in the meeting. Do not invent facts."*

All three use strict JSON-schema outputs, so the frontend always gets the same shape. See [`backend/app/ai.py`](backend/app/ai.py).
</details>

---

## Business

**Who pays:** the owner, HR or plant manager at a manufacturing SME. **Workers use it for free.**

| Plan | Price | For |
|---|---|---|
| Pilot | Free for 3 months | The first 2–3 companies, in exchange for feedback and a reference |
| **Standard (cloud)** | **€15 per worker per month** | Small and mid-sized manufacturers |
| Handover package | One-time fee per retiring expert (€1,500–3,000, to validate) | The easy first sale when someone announces retirement; converts into the subscription |
| On-premise | Setup fee + yearly licence | Companies that keep their data in-house |

**Example:** 50 production workers × €15 = **€750/month = €9,000/year** per customer.

> *"€9,000 a year is less than one bad week after your expert is gone."*

**Why it becomes a company:** every manufacturer has this problem, and it grows each year as more people retire. It is sticky: after a year of entries the knowledge base is too valuable to leave. And it expands: maintenance, quality and logistics, then other countries and industries.

**Go-to-market:** pilots with manufacturers of 50–500 employees in **Mainfranken** (Würzburg, Schweinfurt), through THWS industry partners and IHK Würzburg-Schweinfurt. 2–3 pilot companies with 30–50 workers each give us our **first 100 users**.

| Alternative | Weakness | KnowFlow |
|---|---|---|
| Paper/Excel Schichtbuch | Nobody reads it again, not searchable | Searchable, answers questions |
| Digital shift-log tools | Forms and typing; they store notes but don't create knowledge | Voice + AI structuring + patterns |
| Wikis / document management | Experts don't write documentation | Experts just talk for 2 minutes |
| Generic AI chat over PDFs | The knowledge isn't in PDFs | Captures knowledge that was never written down |

---

## Team WRATHOFCODE

| | Role | Owns |
|---|---|---|
| **Rishabh** | Backend + frontend | API, speech-to-text, AI structuring, search and answers, pattern alerts, sample data, the web app |
| **Aditi** | Frontend | Record, card, ask, QR machine page, dashboard, meetings; demo flow; backup demo video |
| **Aryan** | Business | Problem slide, business model, pricing, costs, competition, deck, pitch |
| **Bushra** | Marketing | First customers, first 100 users, go-to-market slides, pitch, jury Q&A |

### Workflow

- Pull before you start: `git pull`
- Work on a branch: `git checkout -b your-name/feature`
- Push and open a pull request into `main`
- Never commit `.env` or API keys

<div align="center">

**Experts retire. Their knowledge doesn't have to.**

</div>
