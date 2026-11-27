"""End-to-end test of the demo story. Runs offline (no API key needed): python -m pytest"""
import os
import tempfile

os.environ["KNOWFLOW_DATA_DIR"] = tempfile.mkdtemp()
os.environ["KNOWFLOW_DB"] = os.path.join(os.environ["KNOWFLOW_DATA_DIR"], "test.db")
os.environ["OPENAI_API_KEY"] = ""  # stay offline even if .env has a key (dotenv never overrides)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def setup_module(_):
    client.__enter__()  # runs startup (init + seed)
    client.post("/admin/reset-demo")


def test_health():
    h = client.get("/health").json()
    assert h["ok"] and h["cards"] == 16


def test_alerts_tell_the_demo_story():
    alerts = client.get("/alerts").json()
    msgs = " | ".join(a["message"] for a in alerts)
    assert any(a["type"] == "recurring_problem" and a["machine_id"] == "packaging-machine" and a["count"] == 3 for a in alerts), msgs
    assert any(a["type"] == "knowledge_risk" and a["expert"] == "Herr Müller" and a["machine_id"] == "packaging-machine" for a in alerts), msgs
    assert any(a["type"] == "knowledge_risk" and a["expert"] == "Herr Müller" and a["machine_id"] == "press-2" for a in alerts), msgs
    assert any(a["type"] == "disputed_fix" for a in alerts), msgs


def test_record_shift_then_approve_makes_it_4x():
    r = client.post("/entries", data={
        "author": "Herr Müller", "machine": "Packaging Machine",
        "text": "The film jammed again this Monday morning. Because the hall was cold over the weekend. "
                "Let it warm up ten minutes at low speed, then checked the roll tension. Never start at full speed cold."})
    assert r.status_code == 200, r.text
    card = r.json()["card"]
    assert card["status"] == "draft" and card["machine_id"] == "packaging-machine" and card["author_years_experience"] == 32
    client.patch("/cards/%d" % card["id"], json={"tags": ["jam", "monday"]})
    a = client.post("/cards/%d/approve" % card["id"], json={"expert": "Herr Müller"}).json()
    assert a["status"] == "approved" and a["approved_by"] == "Herr Müller" and not a["has_audio"]
    rec = [x for x in client.get("/alerts").json() if x["type"] == "recurring_problem" and x["machine_id"] == "packaging-machine"]
    assert rec[0]["count"] == 4


def test_new_employee_asks():
    r = client.post("/ask", json={"question": "The Packaging Machine jams on Monday morning, what do I do?", "asked_by": "Ahmed"}).json()
    assert r["answered"] and r["steps"]
    assert r["steps"][0]["source"]["author"] == "Herr Müller"
    assert "warm" in " ".join(s["text"] for s in r["steps"]).lower()


def test_unanswered_goes_to_open_questions():
    r = client.post("/ask", json={"question": "How do I calibrate the quantum flux capacitor?"}).json()
    assert not r["answered"] and r["open_question_id"]
    assert any(q["id"] == r["open_question_id"] for q in client.get("/open-questions").json())


def test_qr_machine_page_and_feedback():
    m = client.get("/machine/Packaging Machine").json()
    assert m["machine"] == "Packaging Machine" and m["cards"] and all(c["status"] == "approved" for c in m["cards"])
    cid = m["cards"][0]["id"]
    before = m["cards"][0]["worked_count"]
    after = client.post("/cards/%d/feedback" % cid, json={"worked": True}).json()
    assert after["worked_count"] == before + 1 and after["last_confirmed"]
    svg = client.get("/machine/packaging-machine/qr.svg")
    assert svg.status_code == 200 and svg.text.startswith("<svg")
    assert client.get("/machine/does-not-exist").status_code == 404


def test_meeting_needs_consent_and_creates_cards():
    body = {"title": "Line 2 weekly", "participants": "Frau Schneider, Ahmed",
            "text": "We decided to switch to the thicker film because the thin one keeps jamming. "
                    "Problem: the packaging line jams at roll change. Lesson learned: label the film type on the holder. "
                    "Ahmed will order the new rolls by Friday."}
    assert client.post("/meetings", data=body).status_code == 400
    r = client.post("/meetings", data=dict(body, consent="true"))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["decisions"] and j["cards"] and all(c["source"] == "meeting" for c in j["cards"])
    assert client.get("/meetings").json()[0]["title"] == "Line 2 weekly"


def test_audio_without_stt_key_gives_clear_error():
    r = client.post("/entries", data={"author": "Ahmed"}, files={"audio": ("a.webm", b"xx", "audio/webm")})
    assert r.status_code == 501 and "text" in r.json()["detail"]


def test_dashboard_endpoints():
    risk = client.get("/risk-map").json()
    press = next(r for r in risk if r["machine_id"] == "press-2")
    assert press["risk"] == "high" and press["experts_retiring_soon"] == ["Herr Müller"]
    s = client.get("/stats").json()
    assert s["cards_approved"] >= 15 and s["meetings"] >= 1
    assert client.get("/machines").json()


def test_too_short_or_empty_note_is_rejected_not_invented():
    r = client.post("/entries", data={"author": "Ahmed", "text": "you"})
    assert r.status_code == 422
    r = client.post("/entries/text", json={"author": "Ahmed", "text": "ok thanks"})
    assert r.status_code == 422


def test_silence_transcripts_are_caught():
    from fastapi import HTTPException

    from app.main import _check_heard
    for t in ["you", "Thank you.", "Untertitel der Amara.org-Community", "  ", "Bye."]:
        try:
            _check_heard(t)
            raise AssertionError("should reject: %r" % t)
        except HTTPException as e:
            assert e.status_code == 422
    _check_heard("Spindle noise at startup, let it warm up first.")


def test_open_questions_for_expert_puts_their_machines_first():
    client.post("/ask", json={"question": "How do I refill the coffee machine in hall Z?"})
    qs = client.get("/open-questions", params={"expert": "Herr Weber"}).json()
    assert qs[0]["machine_id"] == "compressor"


def test_demo_seed_has_enough_approved_cards_on_the_four_machines():
    cards = client.get("/entries", params={"status": "approved"}).json()
    assert len(cards) >= 15
    assert {c["machine_id"] for c in cards} == {"packaging-machine", "machine-1", "press-2", "compressor"}
    jams = [c for c in cards if c["machine_id"] == "packaging-machine" and "jam" in c["tags"]]
    assert len(jams) >= 3 and all(c["author"] == "Herr Müller" and c["author_years_experience"] == 32 for c in jams)


def test_machine_link_in_browser_opens_app_page():
    r = client.get("/machine/Packaging Machine", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/#/m/packaging-machine"
    assert client.get("/machine/packaging-machine").json()["machine"] == "Packaging Machine"


def test_fix_it_challenge_offline():
    g = client.post("/challenge", json={"machine": "Packaging Machine"}).json()
    assert g["machine"] == "Packaging Machine" and g["expert"] == "Herr Müller"
    assert len(g["options"]) == 4 and sum(o["correct"] for o in g["options"]) == 1
    right = next(o for o in g["options"] if o["correct"])
    assert "warm" in right["text"].lower()
    assert g["situation"] and g["explanation"] and g["badge"]
    assert "warm up for 10 minutes" in g["expert_tip"].lower()  # his real words, not AI text
    other = client.post("/challenge", json={"exclude": [g["card_id"]]}).json()
    assert other["card_id"] != g["card_id"]
    draft = client.get("/entries", params={"status": "draft"}).json()[0]
    assert client.post("/challenge", json={"card_id": draft["id"]}).status_code == 400


def test_newer_worker_passing_on_expert_tip_becomes_expert_draft():
    r = client.post("/entries", data={
        "author": "Ahmed", "machine": "Machine 1",
        "text": "The door would not open after the program. Herr Müller told me to set the air "
                "pressure back to 6 bar. I did that and it worked."}).json()
    card = r["card"]
    assert r["tip_from"] == "Herr Müller"
    assert card["author"] == "Herr Müller" and card["shared_by"] == "Ahmed" and card["status"] == "draft"
    assert card["author_years_experience"] == 32
    # only the expert can approve it
    assert client.post("/cards/%d/approve" % card["id"], json={"expert": "Ahmed"}).status_code == 403
    ok = client.post("/cards/%d/approve" % card["id"], json={"expert": "Herr Müller"}).json()
    assert ok["status"] == "approved" and ok["approved_by"] == "Herr Müller"


def test_newer_worker_nobody_could_help_goes_to_open_questions():
    r = client.post("/entries", data={
        "author": "Ahmed", "machine": "Compressor",
        "text": "The compressor made a clicking sound all shift. I tried restarting it but it did "
                "not work. Nobody could help me."}).json()
    assert r["open_question_id"] and r["tip_from"] is None
    qs = client.get("/open-questions").json()
    assert any(q["id"] == r["open_question_id"] and q["asked_by"] == "Ahmed" for q in qs)


def test_expert_entry_is_unchanged():
    r = client.post("/entries", data={
        "author": "Frau Schneider", "machine": "Machine 1",
        "text": "Chips wrapped around the part again. Herr Müller told me last year to feed faster. "
                "I fed faster and it was fine."}).json()
    assert r["card"]["author"] == "Frau Schneider" and r["card"]["shared_by"] is None
    assert r["tip_from"] is None and r["open_question_id"] is None


def test_machine_detected_from_words_or_shift_plan():
    # names the machine (German alias) -> detected from what was said
    r = client.post("/entries", data={"author": "Frau Schneider",
        "text": "Der Kompressor wurde heiß und hat abgeschaltet. Luftfilter sauber gemacht, dann lief er wieder."}).json()
    assert r["card"]["machine"] == "Compressor" and r["machine_source"] == "said"
    # English, names the machine
    r = client.post("/entries", data={"author": "Lena Kowalski",
        "text": "Press 2 stopped with a pressure fault this morning. Let it run empty five minutes, then reset."}).json()
    assert r["card"]["machine"] == "Press 2" and r["machine_source"] == "said"
    # no machine named -> shift plan (Herr Müller works at the Packaging Machine)
    r = client.post("/entries", data={"author": "Herr Müller",
        "text": "The film jammed at the start. The hall was cold. Warm-up ten minutes at low speed fixed it."}).json()
    assert r["card"]["machine"] == "Packaging Machine" and r["machine_source"] == "shift plan"
    assert r["card"]["machine_id"] == "packaging-machine"
    assert "Packaging Machine" in r["machines"]


def test_meeting_machine_from_title():
    r = client.post("/meetings", data={"title": "Press 2 weekly", "participants": "Herr Müller", "consent": "true",
        "text": "Problem: parts came out too big after the tool change. We decided to always check the first part."}).json()
    assert r["cards"] and all(c["machine"] == "Press 2" for c in r["cards"])
