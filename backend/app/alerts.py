"""Pattern alerts and the knowledge-risk map. Plain rules, no AI needed."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

RECURRING_MIN = 3
RECURRING_DAYS = 30


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def compute_alerts(cards: List[Dict[str, Any]], experts: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    alerts = []  # type: List[Dict[str, Any]]
    since = datetime.now(timezone.utc) - timedelta(days=RECURRING_DAYS)

    # 1. Recurring problem: same machine + same tag >= 3 times in 30 days.
    by_key = defaultdict(list)  # type: Dict[Any, List[Dict[str, Any]]]
    for c in cards:
        if c.get("machine_id") and c["source"] == "shift" and _dt(c["date"]) >= since:
            for t in c["tags"]:
                by_key[(c["machine_id"], t)].append(c)
    covered = defaultdict(set)  # machine -> card ids already in a recurring alert
    for (mid, tag), group in sorted(by_key.items(), key=lambda kv: -len(kv[1])):
        ids = frozenset(c["id"] for c in group)
        # one alert per machine + problem: a tag group that is mostly the same cards
        # (e.g. 'cold' inside the 'jam' cards) is the same problem, not a new alert
        if len(group) < RECURRING_MIN or 2 * len(ids & covered[mid]) >= len(ids):
            continue
        covered[mid] |= ids
        alerts.append({
            "type": "recurring_problem",
            "severity": "high" if len(group) >= 4 else "medium",
            "machine": group[0]["machine"],
            "machine_id": mid,
            "tag": tag,
            "count": len(group),
            "card_ids": sorted(ids),
            "message": "%s: %s problem %d times in the last %d days" % (group[0]["machine"], tag, len(group), RECURRING_DAYS),
        })

    # 2. Knowledge risk: problems on a machine solved by only one person.
    for mid, group in _solved_by_machine(cards).items():
        authors = {c["author"] for c in group}
        if len(authors) == 1 and len(group) >= 2:
            who = next(iter(authors))
            ex = experts.get(who) or {}
            retiring = ex.get("retires_in_months")
            alerts.append({
                "type": "knowledge_risk",
                "severity": "high" if retiring is not None and retiring <= 12 else "medium",
                "machine": group[0]["machine"],
                "machine_id": mid,
                "expert": who,
                "retires_in_months": retiring,
                "card_ids": sorted(c["id"] for c in group),
                "message": "Only %s has solved problems on %s%s" % (
                    who, group[0]["machine"],
                    " (retires in %d months)" % retiring if retiring is not None else ""),
            })

    # 3. Outdated / disputed fixes.
    for c in cards:
        if c["status"] == "approved" and c["failed_count"] > c["worked_count"]:
            alerts.append({
                "type": "disputed_fix",
                "severity": "medium",
                "machine": c["machine"],
                "machine_id": c["machine_id"],
                "card_ids": [c["id"]],
                "message": "The fix for '%s' on %s often didn't work" % (c["problem"].rstrip("."), c["machine"]),
            })

    order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: order[a["severity"]])
    return alerts


def _solved_by_machine(cards: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out = defaultdict(list)  # type: Dict[str, List[Dict[str, Any]]]
    for c in cards:
        if c.get("machine_id") and c.get("solution") and c["kind"] in ("fix", "mistake"):
            out[c["machine_id"]].append(c)
    return out


def risk_map(cards: List[Dict[str, Any]], experts: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per machine: how many people know it, and how risky that is."""
    rows = []
    for mid, group in _solved_by_machine(cards).items():
        authors = sorted({c["author"] for c in group})
        retiring = [a for a in authors if (experts.get(a) or {}).get("retires_in_months") is not None
                    and experts[a]["retires_in_months"] <= 12]
        safe = [a for a in authors if a not in retiring]
        level = "high" if len(safe) == 0 else "medium" if len(safe) == 1 else "low"
        rows.append({
            "machine": group[0]["machine"],
            "machine_id": mid,
            "experts": authors,
            "experts_retiring_soon": retiring,
            "people_who_know_it": len(authors),
            "cards": len(group),
            "risk": level,
        })
    rows.sort(key=lambda r: ({"high": 0, "medium": 1, "low": 2}[r["risk"]], r["machine"]))
    return rows
