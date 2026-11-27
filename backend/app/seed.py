"""Demo data: 5 experts, 15 approved cards + 1 draft on 4 machines, in simple words.

Main story, Packaging Machine: it jams every Monday morning. Only Herr Müller (32 years,
retires in 6 months) knows the fix: warm it up 10 minutes at low speed (the film gets
stiff when the hall is cold over the weekend), then check the film roll tension.
- 3 "jam" entries this month -> "Keeps happening" alert. The live demo recording makes it 4.
- Packaging Machine and Press 2 only ever fixed by Herr Müller -> "Only one person knows".
- One fix on Machine 1 often didn't work -> warning flag.
- One draft from Ahmed waits for approval; one open question waits for Herr Weber.
Dates are relative to today, so "this month" stays true whenever you run it.
"""
from datetime import datetime, timedelta, timezone

from . import db

EXPERTS = [
    ("Herr Müller", 32, 6),
    ("Frau Schneider", 18, None),
    ("Herr Weber", 25, 10),
    ("Ahmed", 1, None),
    ("Lena Kowalski", 7, None),
]


def _ago(days: int, hour: int = 14) -> str:
    d = datetime.now(timezone.utc) - timedelta(days=days)
    return d.replace(hour=hour, minute=30, second=0, microsecond=0).isoformat()


# (days_ago, author, machine, process, kind, problem, cause, solution, warning, tags, worked, failed, status)
PM = "Packaging Machine"
ENTRIES = [
    # ---- Packaging Machine: the main story (only Herr Müller)
    (24, "Herr Müller", PM, "Packing", "fix",
     "Film jams at the start on Monday morning.",
     "The hall is cold over the weekend, so the film gets stiff.",
     "Let it warm up for 10 minutes at low speed. Then check the film roll tension.",
     "Don't start at full speed when the machine is cold.",
     ["jam", "monday", "cold"], 5, 0, "approved"),
    (17, "Herr Müller", PM, "Packing", "fix",
     "Jammed again on Monday morning.",
     "Cold hall again. The film roll was also wound too tight.",
     "10 minutes warm-up at low speed fixed it. Loosened the roll tension half a turn.",
     None, ["jam", "monday"], 3, 0, "approved"),
    (3, "Herr Müller", PM, "Packing", "fix",
     "Jams every Monday. Third time this month.",
     "The heating is switched off at the weekend.",
     "Warm-up as usual. Asked the caretaker to keep the heating on over the weekend.",
     "If it still jams after the warm-up, stop and check the film roll for dents.",
     ["jam", "cold", "heating"], 1, 0, "approved"),
    (40, "Herr Müller", PM, "Sealing", "fix",
     "Bags don't close properly.",
     "The sealing bar is dirty.",
     "Clean the sealing bar with alcohol while it is cold.",
     "Never use a metal scraper. It ruins the coating.",
     ["seal", "cleaning"], 6, 1, "approved"),
    (6, "Herr Müller", PM, "Labels", "fix",
     "Labels print blurry.",
     "The print head is dirty after the night shift.",
     "Clean the print head with the cleaning pen at the start of each shift.",
     None, ["labels", "printer"], 2, 0, "approved"),

    # ---- Machine 1 (many people know it)
    (9, "Ahmed", "Machine 1", "Cutting", "fix",
     "Machine stops with alarm E-217.",
     "The coolant filter is blocked.",
     "Change the coolant filter. Herr Müller says: check it every Friday.",
     None, ["alarm", "filter"], 2, 0, "approved"),
    (27, "Lena Kowalski", "Machine 1", "Cutting", "fix",
     "Steel parts come out rough.",
     "The cutting tool wears out sooner than the counter says.",
     "Change the cutting tool earlier when cutting steel.",
     None, ["tool", "surface"], 0, 2, "approved"),
    (19, "Frau Schneider", "Machine 1", "Cutting", "fix",
     "Metal chips wrap around the part and scratch it.",
     "Wrong cutting tool for aluminium.",
     "Use the tool with the chip groove and feed a little faster.",
     "Never pull chips away by hand while the machine turns.",
     ["chips", "surface"], 4, 0, "approved"),
    (33, "Frau Schneider", "Machine 1", "Cutting", "mistake",
     "We started the program without checking the clamp.",
     "The part was not clamped tight and came loose.",
     "Always pull on the part once before you press start.",
     "A loose part can fly out. Keep the door closed.",
     ["clamp", "safety"], 3, 0, "approved"),

    # ---- Press 2 (only Herr Müller)
    (55, "Herr Müller", "Press 2", "Pressing", "fix",
     "Parts come out the wrong size after a tool change.",
     "Small metal chips under the tool, so it doesn't sit flat.",
     "Blow the tool seat clean with air before clamping. Check the first part with the gauge.",
     "Always check the first part after a tool change.",
     ["size", "tool-change"], 3, 0, "approved"),
    (21, "Herr Müller", "Press 2", "Pressing", "fix",
     "Press stops with a pressure fault in the morning.",
     "The oil is too cold at the start of the early shift.",
     "Let the press run empty for 5 minutes so the oil warms up, then reset the fault.",
     "If the fault comes back when warm, call maintenance.",
     ["pressure", "cold"], 2, 0, "approved"),
    (8, "Herr Müller", "Press 2", "Pressing", "mistake",
     "We restarted after a jam without checking the metal strip.",
     "The strip was one step off, so 40 parts came out wrong.",
     "After a jam, move one stroke by hand and check the strip before switching to automatic.",
     "Wrong parts look fine at first glance. Check them with the gauge.",
     ["jam", "strip"], 2, 0, "approved"),

    # ---- Compressor (Herr Weber and Herr Müller, both retiring soon)
    (33, "Herr Müller", "Compressor", "Compressed air", "fix",
     "Air pressure drops at night.",
     "A leak at a hose connection near Machine 1.",
     "Listen for hissing on a quiet weekend and replace the seal at the connection.",
     None, ["pressure", "leak"], 1, 0, "approved"),
    (14, "Herr Weber", "Compressor", "Compressed air", "fix",
     "Compressor gets hot and switches off.",
     "The air filter is full of dust.",
     "Clean the air filter every month and replace it every 6 months.",
     "Let it cool down before you open the cover.",
     ["heat", "filter"], 3, 0, "approved"),
    (48, "Herr Weber", "Compressor", "Compressed air", "mistake",
     "We drained the water tank while it was still under pressure.",
     "The valve was opened too fast, so water and air sprayed out.",
     "Switch off and wait until the pressure shows zero before draining.",
     "Wear safety glasses when draining.",
     ["water", "safety"], 2, 0, "approved"),

    # ---- Draft waiting for approval (for the approve demo)
    (1, "Ahmed", "Machine 1", "Cutting", "fix",
     "The door does not open after the program ends.",
     "Air pressure is too low.",
     "Checked the air pressure, set it back to 6 bar, door works again.",
     "Do not force the door open by hand.",
     ["door", "pressure"], 0, 0, "draft"),
]

OPEN_QUESTIONS = [
    ("The compressor shows a red oil light. What do I do?", "compressor", "Ahmed"),
]


def seed(reset: bool = False) -> int:
    db.init()
    with db.conn() as c:
        if reset:
            c.execute("DELETE FROM cards")
            c.execute("DELETE FROM meetings")
            c.execute("DELETE FROM open_questions")
            c.execute("DELETE FROM experts")
        elif c.execute("SELECT COUNT(*) FROM cards").fetchone()[0]:
            return 0
        c.executemany("INSERT OR REPLACE INTO experts VALUES (?,?,?)", EXPERTS)
        years = {n: y for n, y, _ in EXPERTS}
        for (days, author, machine, process, kind, problem, cause, solution, warning, tags,
             worked, failed, status) in ENTRIES:
            date = _ago(days)
            approved = status == "approved"
            db.insert_card(c, {
                "date": date, "author": author, "author_years_experience": years[author],
                "machine": machine, "process": process, "kind": kind, "problem": problem,
                "cause": cause, "solution": solution, "warning": warning, "tags": tags,
                "transcript": None, "status": status,
                "approved_by": author if approved else None,
                "approved_at": _ago(max(days - 1, 0)) if approved else None,
                "worked_count": worked, "failed_count": failed,
                "last_confirmed": _ago(max(days - 2, 0)) if worked else None,
            })
        for q, mid, who in OPEN_QUESTIONS:
            c.execute("INSERT INTO open_questions (date, question, machine_id, asked_by) VALUES (?,?,?,?)",
                      (_ago(4), q, mid, who))
        return len(ENTRIES)


if __name__ == "__main__":
    print("Seeded %d cards" % seed(reset=True))
