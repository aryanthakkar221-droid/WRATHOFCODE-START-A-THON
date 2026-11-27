"""Demo data: 5 experts plus 15 shift entries, built around the pitch demo story.

- CNC 3 spindle noise is logged 3x this month, so "Recurring problem" fires. The live demo
  recording makes it 4x.
- Press 2 has only ever been solved by Herr Müller, who retires in 6 months, so the
  "Knowledge risk" alert fires.
- One draft from Ahmed waits for expert approval, for the approve demo.
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
ENTRIES = [
    (24, "Herr Müller", "CNC 3", "Milling", "fix",
     "Spindle makes a grinding noise at startup.",
     "Cold spindle bearings after the hall cooled down overnight.",
     "Let it warm up 10 minutes at low speed (about 20% rpm). If the noise stays, check the bearing temperature.",
     "Do not start a full-speed program on a cold spindle in winter.",
     ["spindle", "noise", "warm-up"], 5, 0, "approved"),
    (17, "Herr Müller", "CNC 3", "Milling", "fix",
     "Spindle noise again at startup.",
     "Same as before: cold bearings. The hall gets cold on Monday mornings.",
     "Warm-up routine fixed it again: 10 minutes at low speed before the first job.",
     None, ["spindle", "noise"], 3, 0, "approved"),
    (3, "Herr Müller", "CNC 3", "Milling", "fix",
     "Spindle noise on Monday morning, third time this month.",
     "Probably the weekend heating is switched off in hall B.",
     "Warm-up as usual. Told maintenance to keep the heating on at weekends.",
     "If it is noisy after warm-up, stop the machine: bearing damage is expensive.",
     ["spindle", "noise", "heating"], 1, 0, "approved"),
    (40, "Frau Schneider", "Packaging line", "Packaging", "fix",
     "Film jams about every 200 packs.",
     "Film roll tension set too high.",
     "Turn the tension knob back half a turn. Check that the roll is not dented.",
     "A dented roll jams no matter how you set the tension, so swap it.",
     ["film", "jam", "tension"], 6, 1, "approved"),
    (12, "Frau Schneider", "Packaging line", "Sealing", "fix",
     "Seal does not close properly.",
     "Seal bar is dirty.",
     "Clean the seal bar with alcohol while it is cold.",
     "Never use a metal scraper: it damages the non-stick coating.",
     ["seal", "cleaning"], 4, 0, "approved"),
    (55, "Herr Müller", "Press 2", "Stamping", "fix",
     "Parts out of tolerance after a die change.",
     "Die was not fully seated: chips on the seat.",
     "Clean the die seat with compressed air before clamping, then check the first part with the gauge.",
     "Never skip the first-part check after a die change.",
     ["die", "tolerance"], 3, 0, "approved"),
    (21, "Herr Müller", "Press 2", "Stamping", "fix",
     "Press stops with a hydraulic pressure fault.",
     "Hydraulic oil too cold at the start of the early shift.",
     "Run the press idle for 5 minutes so the oil warms up, then reset the fault.",
     "If the fault comes back when warm, call maintenance: it may be the pressure valve.",
     ["hydraulic", "pressure"], 2, 0, "approved"),
    (33, "Herr Müller", "Compressor", "Compressed air", "fix",
     "Air pressure drops at night.",
     "Probably a leak at the quick coupling near CNC 5.",
     "Listen for hissing at the coupling on a quiet weekend; replace the coupling seal.",
     None, ["pressure", "leak"], 1, 0, "approved"),
    (9, "Ahmed", "CNC 1", "Turning", "fix",
     "Alarm E-217: coolant flow too low.",
     "Coolant filter clogged.",
     "Replace the coolant filter. Herr Müller says to check it every Friday.",
     None, ["coolant", "alarm", "e-217"], 2, 0, "approved"),
    (14, "Herr Weber", "Welding robot", "Welding", "fix",
     "Weld seams porous on the first parts of the shift.",
     "Gas nozzle spatter build-up; gas flow disturbed.",
     "Clean the nozzle and spray anti-spatter before the shift; check gas flow is 12-15 l/min.",
     "Do not raise the gas flow above 18 l/min: it makes porosity worse.",
     ["welding", "porosity", "gas"], 3, 0, "approved"),
    (48, "Herr Weber", "Welding robot", "Welding", "mistake",
     "We reteached the robot path after a crash without checking the TCP.",
     "Tool centre point was bent by the crash, so every weld was offset by 2 mm.",
     "Always run the TCP calibration after any collision, before reteaching.",
     "A whole batch of 300 parts had to be scrapped.",
     ["robot", "tcp", "calibration"], 2, 0, "approved"),
    (6, "Lena Kowalski", "Packaging line", "Packaging", "fix",
     "Labels print smudged.",
     "Print head dirty after the night shift.",
     "Clean the print head with the cleaning pen at the start of each shift.",
     None, ["labels", "printer"], 1, 0, "approved"),
    (27, "Lena Kowalski", "CNC 1", "Turning", "fix",
     "Surface finish rough on stainless parts.",
     "Insert worn earlier than the tool counter says on stainless.",
     "Change the insert after 60% of the counter on stainless steel.",
     None, ["insert", "surface", "stainless"], 0, 2, "approved"),
    (2, "Frau Schneider", "Packaging line", "Packaging", "fix",
     "Film jam again after the roll change.",
     "New film supplier: thinner film.",
     "Tension knob back a full turn for the new film type.",
     "Write the film type on the roll holder so the next shift knows.",
     ["film", "jam", "tension"], 0, 0, "approved"),
    (1, "Ahmed", "CNC 1", "Turning", "fix",
     "Chuck does not open after the program ends.",
     "Low air pressure on the chuck line.",
     "Checked the pressure regulator, set it back to 6 bar, chuck works again.",
     "Do not open the chuck by hand with the spindle powered.",
     ["chuck", "pressure"], 0, 0, "draft"),
]

OPEN_QUESTIONS = [
    ("What do I do when the welding robot shows error 4021?", "welding-robot", "Ahmed"),
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
