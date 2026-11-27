"""Token rewards: reward quality, not quantity.

+5 when your card is approved, +10 each time a colleague says your fix "Worked".
Nothing for recording, drafts or "Didn't work". No tokens for rating your own card.
One "Worked" per person per card. Max 30 tokens per person per day.
10 tokens = 1 EUR, at most 50 EUR a month (tax-free benefit limit), so 500 tokens a month.
Demo only: no real payment.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from . import db

APPROVED = 5
WORKED = 10
DAILY_CAP = 30
TOKENS_PER_EUR = 10
MONTHLY_CAP_EUR = 50
MONTHLY_CAP = MONTHLY_CAP_EUR * TOKENS_PER_EUR


def _sum_since(c: sqlite3.Connection, person: str, since: str) -> int:
    r = c.execute("SELECT COALESCE(SUM(amount),0) FROM token_events WHERE person=? AND date>=?",
                  (person, since)).fetchone()
    return int(r[0])


def _day_start() -> str:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _month_start() -> str:
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def award(c: sqlite3.Connection, person: Optional[str], amount: int, reason: str,
          card_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Give tokens within the daily and monthly caps. Returns the event, or None if capped."""
    if not person:
        return None
    left_today = DAILY_CAP - _sum_since(c, person, _day_start())
    left_month = MONTHLY_CAP - _sum_since(c, person, _month_start())
    amount = min(amount, left_today, left_month)
    if amount <= 0:
        return None
    date = db.now_iso()
    c.execute("INSERT INTO token_events (date, person, amount, reason, card_id) VALUES (?,?,?,?,?)",
              (date, person, amount, reason, card_id))
    return {"person": person, "amount": amount, "reason": reason, "date": date}


def daily_totals(c: sqlite3.Connection) -> Dict[str, Any]:
    """Tokens earned by everyone per day this month (for the Dashboard sparkline). No per-person data."""
    start = _month_start()
    rows = c.execute("SELECT substr(date,1,10) d, SUM(amount) s FROM token_events WHERE date>=? GROUP BY d",
                     (start,)).fetchall()
    by_day = {r["d"]: int(r["s"]) for r in rows}
    today = datetime.now(timezone.utc).date()
    first = today.replace(day=1)
    days = [{"date": (first + timedelta(days=i)).isoformat(),
             "tokens": by_day.get((first + timedelta(days=i)).isoformat(), 0)}
            for i in range((today - first).days + 1)]
    return {"month_total": sum(d["tokens"] for d in days), "days": days}


def wallet(c: sqlite3.Connection, person: str) -> Dict[str, Any]:
    bal = _sum_since(c, person, "")
    month = _sum_since(c, person, _month_start())
    events = [dict(r) for r in c.execute(
        "SELECT date, amount, reason, card_id FROM token_events WHERE person=? ORDER BY date DESC LIMIT 20",
        (person,)).fetchall()]
    return {
        "person": person, "balance": bal, "balance_eur": round(bal / TOKENS_PER_EUR, 2),
        "month_tokens": month, "month_eur": round(month / TOKENS_PER_EUR, 2),
        "monthly_cap_eur": MONTHLY_CAP_EUR, "daily_cap": DAILY_CAP,
        "tokens_per_eur": TOKENS_PER_EUR, "today": _sum_since(c, person, _day_start()),
        "events": events,
    }
