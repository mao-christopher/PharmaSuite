"""Proactive stock suggestions: things an employee may want to do before they become alerts.

Derived from the current inventory on every read rather than stored, so they can never
be raised twice. They change nothing; alerts (expired, out of stock) still come from the
inventory rules. All thresholds below are unvalidated demo defaults.
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from pharma.db.models import Catalog, Medication

EXPIRY_WARNING_DAYS = 30  # warn about batches expiring within this many days
EXPIRY_URGENT_DAYS = 7
DEFAULT_REORDER_FRACTION = 0.2  # default reorder point: 20% of the opening stock
USAGE_WINDOW_DAYS = 14  # prescription usage is averaged over this many days
LEAD_TIME_DAYS = 7  # suggest ordering when stock covers fewer days than this


def reorder_point(med: Medication, catalog: Catalog) -> int:
    if med.reorder_point is not None:
        return med.reorder_point
    opening = sum(r.bottle_count * r.tablets_per_bottle for r in catalog.receipts if r.medication_key == med.medication_key)
    return round(opening * DEFAULT_REORDER_FRACTION)


def daily_usage(engine, medication_key: str, today: date) -> float:
    """Average units dispensed per day by prescriptions over the usage window."""
    since = today - timedelta(days=USAGE_WINDOW_DAYS)
    used = 0
    for tx in engine.transactions.values():
        if tx.medication_key != medication_key or not tx.deducted or not tx.deducted_at:
            continue
        try:
            when = datetime.fromisoformat(tx.deducted_at).date()
        except ValueError:
            continue
        if since < when <= today:
            used += tx.quantity
    return used / USAGE_WINDOW_DAYS


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def stock_suggestions(engine, catalog: Catalog, today_iso: str, dismissed: Iterable[str] = ()) -> List[Dict[str, Any]]:
    today = date.fromisoformat(today_iso)
    dismissed = set(dismissed)
    out: List[Dict[str, Any]] = []

    def add(sid: str, kind: str, severity: str, med: Medication, title: str, message: str,
            action: str, receipt_id: Optional[str] = None, **numbers: Any) -> None:
        if sid not in dismissed:
            out.append({"id": sid, "kind": kind, "severity": severity, "medication_key": med.medication_key,
                        "title": title, "message": message, "action": action, "receipt_id": receipt_id, **numbers})

    for med in catalog.medications:
        inv = engine.inventory.get(med.medication_key)
        if inv is None or inv.total_bottles == 0:
            continue  # out of stock is an alert already
        unit = med.unit
        batches = sorted((r for r in engine.receipts.values() if r.medication_key == med.medication_key),
                         key=lambda r: r.received_at)
        # A restock gives a new latest batch, which re-arms suggestions dismissed before it.
        latest = batches[-1].receipt_id if batches else "none"
        point = reorder_point(med, catalog)

        if inv.total_bottles == 1:
            add(f"last_bottle:{med.medication_key}:{latest}", "last_bottle", "warning", med, "Last bottle",
                f"Only one bottle is left ({inv.pooled_tablets} {unit}). Order more before it runs out.",
                "receive", tablets=inv.pooled_tablets, reorder_point=point)
        elif point > 0 and inv.pooled_tablets <= point:
            add(f"low_stock:{med.medication_key}:{latest}", "low_stock", "warning", med, "Running low",
                f"{inv.pooled_tablets} {unit} left, at or below the reorder point of {point}. Order more.",
                "receive", tablets=inv.pooled_tablets, reorder_point=point)

        rate = daily_usage(engine, med.medication_key, today)
        if rate > 0 and inv.pooled_tablets > 0:
            days_left = inv.pooled_tablets / rate
            if days_left < LEAD_TIME_DAYS:
                add(f"runout:{med.medication_key}:{latest}", "runout", "warning", med, "Forecast to run out",
                    f"At the last {USAGE_WINDOW_DAYS} days' pace (about {rate:.0f} {unit} a day), stock lasts "
                    f"about {max(1, round(days_left))} more day{'s' if round(days_left) != 1 else ''}. Order now.",
                    "receive", days_left=round(days_left, 1), daily_usage=round(rate, 1))

        for r in batches:
            if r.remaining_bottles == 0:
                continue
            expiry = date.fromisoformat(r.expiry_date)
            days = (expiry - today).days
            if days < 0 or days > EXPIRY_WARNING_DAYS:
                continue  # already expired is an alert; far-off expiry needs nothing
            # Bottles still usable once this batch and everything expiring before it are gone.
            left_after = inv.total_bottles - sum(
                b.remaining_bottles for b in batches if b.remaining_bottles and b.expiry_date <= r.expiry_date
            )
            urgent = days <= EXPIRY_URGENT_DAYS or left_after <= 0
            when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
            message = f"{_plural(r.remaining_bottles, 'bottle')} of batch {r.receipt_id} expire {when} ({r.expiry_date}). Use them first."
            if left_after <= 0:
                message += " They're the last in stock, so order more now."
            elif left_after == 1:
                message += " Only one other bottle would be left, so consider ordering more."
            add(f"expiring:{r.receipt_id}:{'week' if days <= EXPIRY_URGENT_DAYS else 'month'}", "expiring_soon",
                "warning" if urgent else "info", med, "Expiring soon", message,
                "receive" if left_after <= 1 else "view_batch", receipt_id=r.receipt_id,
                days_left=days, bottles=r.remaining_bottles, bottles_left_after=max(0, left_after))

    out.sort(key=lambda s: (s["severity"] != "warning", s["kind"], s["medication_key"]))
    return out
