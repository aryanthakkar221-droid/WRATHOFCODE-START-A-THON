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
    assert h["ok"] and h["cards"] == 15


def test_alerts_tell_the_demo_story():
    alerts = client.get("/alerts").json()
    msgs = " | ".join(a["message"] for a in alerts)
    assert any(a["type"] == "recurring_problem" and a["machine_id"] == "cnc-3" and a["count"] == 3 for a in alerts), msgs
    assert any(a["type"] == "knowledge_risk" and a["expert"] == "Herr Müller" and a["machine_id"] == "press-2" for a in alerts), msgs
    assert any(a["type"] == "disputed_fix" for a in alerts), msgs


def test_record_shift_then_approve_makes_it_4x():
    r = client.post("/entries", data={
        "author": "Herr Müller", "machine": "CNC 3",
        "text": "Spindle noise at startup again this morning. Because the hall was cold. "
                "Let it warm up ten minutes at low speed, then it was fine. Never run full speed cold."})
    assert r.status_code == 200, r.text
    card = r.json()["card"]
    assert card["status"] == "draft" and card["machine_id"] == "cnc-3" and card["author_years_experience"] == 32
    client.patch("/cards/%d" % card["id"], json={"tags": ["spindle", "noise"]})
    a = client.post("/cards/%d/approve" % card["id"], json={"expert": "Herr Müller"}).json()
    assert a["status"] == "approved" and a["approved_by"] == "Herr Müller" and not a["has_audio"]
    rec = [x for x in client.get("/alerts").json() if x["type"] == "recurring_problem" and x["machine_id"] == "cnc-3"]
    assert rec[0]["count"] == 4


def test_new_employee_asks():
    r = client.post("/ask", json={"question": "CNC 3 makes a noise when starting, what do I do?", "asked_by": "Ahmed"}).json()
    assert r["answered"] and r["steps"]
    assert r["steps"][0]["source"]["author"] == "Herr Müller"
    assert "warm" in " ".join(s["text"] for s in r["steps"]).lower()


def test_unanswered_goes_to_open_questions():
    r = client.post("/ask", json={"question": "How do I calibrate the quantum flux capacitor?"}).json()
    assert not r["answered"] and r["open_question_id"]
    assert any(q["id"] == r["open_question_id"] for q in client.get("/open-questions").json())


def test_qr_machine_page_and_feedback():
    m = client.get("/machine/CNC 3").json()
    assert m["machine"] == "CNC 3" and m["cards"] and all(c["status"] == "approved" for c in m["cards"])
    cid = m["cards"][0]["id"]
    before = m["cards"][0]["worked_count"]
    after = client.post("/cards/%d/feedback" % cid, json={"worked": True}).json()
    assert after["worked_count"] == before + 1 and after["last_confirmed"]
    svg = client.get("/machine/cnc-3/qr.svg")
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
