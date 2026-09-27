"""Shelf layouts planned from imported shipments.

The layout fills shelving the room already has. Tagged shelf boxes are grouped into
units the way the rebuild groups them (stacked rows with overlapping footprints), and
each unit's rows are split into equal slots at least MIN_SLOT_M wide. A plan gives one
slot to every medication that needs a shelf in this room: those already shelved here
and every accepted line of a shipment that hasn't been stocked yet.

Mix-up risks come first. Name rules flag one drug in two strengths or forms, pairs on
published confused-name lists, sound-alike and look-alike names, and class siblings
that share a name stem. The Llama model adds pairs the rules miss. Every risk pair must
land on different shelves (another row or unit) and never in touching slots. The
thresholds are unvalidated heuristics: they cast a wide net, and a flagged pair only
costs shelf placement, never stock.

Plans come from Meta's Llama API when a token is configured, otherwise (or when its
answer fails validation, or separates fewer risk pairs than the built-in plan would)
from a deterministic built-in planner. Both keep shelved medications where they are
unless separation needs a move, and put the busiest stock at waist or eye height near
the counter. A plan is a proposal: nothing changes until an employee applies it, a plan
that leaves risk pairs together needs an explicit acknowledgement, and applying never
changes stock counts.

Applying replaces the room's shelf boxes with one slot-sized box per medication. The
full-width rows it planned in are saved beside the room (`shelving.json`), so empty
slots stay available to the next plan.
"""

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from pharma.db.models import Catalog, Medication, Region3D, Room, RoomBox, medication_key_for, shelf_region_id
from pharma.services import meta_llama
from pharma.services.room import room_dir
from pharma.services.room_rebuild import _footprint_corners, _group_shelves, _to_world

SHELVING_FILE = "shelving.json"
MIN_SLOT_M = 0.25
SLOT_GAP_M = 0.02
ROW_MERGE_M = 0.05  # vertical overlap above which two tagged boxes share a row
# Unvalidated name-similarity thresholds: a match is treated as a mix-up risk.
SIMILAR_NAME_RATIO = 0.6  # spelling
SOUND_ALIKE_RATIO = 0.75  # phonetic key
SHARED_PREFIX = 4  # letters two names of 6+ letters share at the start ("tall man" pairs)
# Widely published confused-name pairs (a subset of lists such as ISMP's), by first word.
KNOWN_MIXUPS = {frozenset(p) for p in [
    ("HYDROXYZINE", "HYDRALAZINE"), ("HYDROCHLOROTHIAZIDE", "HYDROXYCHLOROQUINE"), ("HYDROCODONE", "OXYCODONE"),
    ("HYDROMORPHONE", "MORPHINE"), ("OXYCODONE", "OXYMORPHONE"), ("METFORMIN", "METRONIDAZOLE"),
    ("TRAMADOL", "TRAZODONE"), ("LAMOTRIGINE", "LAMIVUDINE"), ("LAMOTRIGINE", "LEVOTHYROXINE"),
    ("LAMOTRIGINE", "LABETALOL"), ("LEVETIRACETAM", "LEVOFLOXACIN"), ("CLONIDINE", "CLONAZEPAM"),
    ("CLONIDINE", "KLONOPIN"), ("CLONAZEPAM", "LORAZEPAM"), ("ALPRAZOLAM", "LORAZEPAM"),
    ("LOSARTAN", "VALSARTAN"), ("PREDNISONE", "PREDNISOLONE"), ("GLIPIZIDE", "GLYBURIDE"),
    ("GLIMEPIRIDE", "GLYBURIDE"), ("GLIPIZIDE", "GLIMEPIRIDE"), ("CARBAMAZEPINE", "OXCARBAZEPINE"),
    ("BUPROPION", "BUSPIRONE"), ("FAMOTIDINE", "FLUOXETINE"), ("PAROXETINE", "FLUOXETINE"),
    ("DULOXETINE", "FLUOXETINE"), ("SERTRALINE", "CETIRIZINE"), ("RISPERIDONE", "ROPINIROLE"),
    ("QUETIAPINE", "OLANZAPINE"), ("AMLODIPINE", "AMILORIDE"), ("NICARDIPINE", "NIFEDIPINE"),
    ("METHOTREXATE", "METOLAZONE"), ("METOPROLOL", "MISOPROSTOL"), ("SITAGLIPTIN", "SUMATRIPTAN"),
    ("SUMATRIPTAN", "ZOLMITRIPTAN"), ("TIZANIDINE", "TIAGABINE"), ("TOPIRAMATE", "TORSEMIDE"),
    ("VALACYCLOVIR", "VALGANCICLOVIR"), ("GUAIFENESIN", "GUANFACINE"), ("CHLORPROMAZINE", "CHLORPROPAMIDE"),
    ("CYCLOBENZAPRINE", "CYPROHEPTADINE"), ("CELEBREX", "CELEXA"), ("CELEBREX", "CEREBYX"),
    ("ZYRTEC", "ZYPREXA"), ("LAMICTAL", "LAMISIL"), ("PAXIL", "PLAVIX"), ("ADDERALL", "INDERAL"),
    ("XANAX", "ZANTAC"), ("FLOMAX", "FOSAMAX"), ("ACTOS", "ACTONEL"), ("TORADOL", "TRAMADOL"),
    ("NORVASC", "NAVANE"), ("SEROQUEL", "SINEQUAN"), ("KEPPRA", "KALETRA"),
]}
# Name stems shared by one drug class. Class siblings look and sound alike on labels.
CLASS_STEMS = ("PRIL", "SARTAN", "OLOL", "STATIN", "DIPINE", "AZEPAM", "AZOLAM", "TRIPTAN", "GLIPTIN",
               "PRAZOLE", "TIDINE", "FLOXACIN", "CYCLINE", "MYCIN", "CILLIN", "GLINIDE", "DRONATE", "TEROL")
# Two slots count as "too close" for a risk pair when they share a shelf row or touch
# (side by side or directly above/below). A plan never trades one of these away for a
# softer preference; it is reported when the shelving can't avoid it.
SAME_UNIT_PENALTY = 20.0
MOVE_PENALTY = 50.0  # keeping shelved medications where they are beats a slightly better slot
BAND_PENALTY = {"waist": 0.0, "eye": 0.0, "low": 2.0, "high": 3.0}
ACRONYMS = {"HCL": "HCl", "DR": "DR", "ER": "ER", "XR": "XR", "XL": "XL", "SR": "SR", "ODT": "ODT", "IU": "IU"}
MIXUP_KINDS = ["same drug, different strength or form", "known mix-up", "sound-alike names", "look-alike names",
               "same drug class"]

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "mixup_pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "medication_keys": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
                    "kind": {"type": "string", "enum": MIXUP_KINDS},
                    "reason": {"type": "string"},
                },
                "required": ["medication_keys", "kind", "reason"],
            },
        },
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "medication_key": {"type": "string"},
                    "slot_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["medication_key", "slot_id", "reason"],
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["mixup_pairs", "assignments"],
}

SYSTEM_PROMPT = """You plan where medications go on pharmacy shelves so a technician is less likely to pick the wrong bottle.
First, find mix-up risks. Look at every pair of medications and list in mixup_pairs each pair not already in risk_pairs that could be confused when picking:
- names that sound alike when spoken, or look alike when written or printed (including brand and generic names),
- the same drug in another strength, salt or release form (for example ER versus immediate release),
- drugs of one class with a shared name stem (for example two -sartans), and
- pairs known to be confused, such as those on the ISMP list of confused drug names.
Only use medication_key values from the list. Give each pair a short reason (under 20 words).
Then place the medications. Rules, in priority order:
1. Assign every medication exactly one slot_id from the list. Never invent slots and never put two medications in one slot.
2. Put the two medications of every pair in risk_pairs or mixup_pairs on different shelves (different shelf_id), and never in slots listed as each other's neighbors (neighbors include the slots directly above and below). Prefer different units.
3. Keep a medication that has a current_slot in that slot unless rule 2 requires a move.
4. Put medications with the most bottles at waist or eye height and close to the dispensing counter.
Give each assignment a short reason (under 20 words). Answer with JSON only."""


@dataclass
class Slot:
    slot_id: str
    unit: int
    row: int
    position: int
    box: RoomBox
    band: str
    counter_m: Optional[float]
    neighbors: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"Unit {self.unit} · row {self.row} from the floor · position {self.position}"


@dataclass
class Shelving:
    slots: List[Slot]
    rows: List[RoomBox]  # full-width row boxes, saved on apply
    current: Dict[str, List[str]]  # shelved medication -> slots its tagged box covers

    def by_id(self) -> Dict[str, Slot]:
        return {s.slot_id: s for s in self.slots}


# ---------------------------------------------------------------- shelving


def _band(height_m: float) -> str:
    return "low" if height_m < 0.6 else "waist" if height_m < 1.1 else "eye" if height_m < 1.6 else "high"


def _saved_rows(rooms_dir: Path, room: Room) -> List[Region3D]:
    path = room_dir(rooms_dir, room.room_id) / SHELVING_FILE
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8")).get("rows", [])
    return [Region3D(region_id=f"row_{i}", region_type="designated_shelf", box=RoomBox(**b)) for i, b in enumerate(rows)]


def _merge_rows(ranges: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    rows: List[List[float]] = []
    for lo, hi in sorted(ranges):
        if rows and min(rows[-1][1], hi) - max(rows[-1][0], lo) > ROW_MERGE_M:
            rows[-1] = [min(rows[-1][0], lo), max(rows[-1][1], hi)]
        else:
            rows.append([lo, hi])
    return [(lo, hi) for lo, hi in rows]


def shelving(rooms_dir: Path, room: Room) -> Shelving:
    """The room's shelf slots, from its tagged shelves plus rows saved by an earlier layout."""
    tagged = [r for r in room.regions if r.region_type == "designated_shelf"]
    tagged_ids = {id(r) for r in tagged}
    # Saved full-width rows first: they seed each unit, so slot-sized boxes that don't
    # touch each other still join the unit their row belongs to.
    members = _saved_rows(rooms_dir, room) + tagged
    if not members:
        raise ValueError("Tag at least one shelf on the Room page first. The layout fills the shelving you have tagged.")
    counters = [np.array(r.box.center) for r in room.regions if r.region_type == "dispensing_counter"]
    slots: List[Slot] = []
    rows_out: List[RoomBox] = []
    current: Dict[str, List[str]] = {}
    for u, group in enumerate(_group_shelves(members), 1):
        center, yaw = group[0].box.center, group[0].box.yaw_deg
        local = {id(r): _footprint_corners(r, center, yaw) for r in group}
        corners = np.concatenate(list(local.values()))
        x0, x1 = float(corners[:, 0].min()), float(corners[:, 0].max())
        z0, z1 = float(corners[:, 2].min()), float(corners[:, 2].max())
        rows = _merge_rows([(float(c[:, 1].min()), float(c[:, 1].max())) for c in local.values()])
        width = x1 - x0
        count = max(1, int(width // MIN_SLOT_M))
        slot_w = width / count
        grid: Dict[Tuple[int, int], Slot] = {}
        for ri, (y0, y1) in enumerate(rows, 1):
            row_center = _to_world([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2], center, yaw)
            rows_out.append(RoomBox(center=tuple(round(float(v), 4) for v in row_center),
                                    size=(round(width, 4), round(y1 - y0, 4), round(z1 - z0, 4)), yaw_deg=yaw))
            for i in range(count):
                c = _to_world([x0 + (i + 0.5) * slot_w, (y0 + y1) / 2, (z0 + z1) / 2], center, yaw)
                dist = min((float(np.hypot(*(c - k)[[0, 2]])) for k in counters), default=None)
                slot = Slot(
                    slot_id=f"u{u}-r{ri}-p{i + 1}", unit=u, row=ri, position=i + 1,
                    box=RoomBox(center=tuple(round(float(v), 4) for v in c),
                                size=(round(max(slot_w * 0.9, slot_w - SLOT_GAP_M), 4), round(y1 - y0, 4),
                                      round(z1 - z0, 4)), yaw_deg=yaw),
                    band=_band(float(c[1])), counter_m=None if dist is None else round(dist, 2),
                )
                grid[(ri, i)] = slot
                slots.append(slot)
        for (ri, i), slot in grid.items():
            slot.neighbors = [grid[k].slot_id for k in ((ri, i - 1), (ri, i + 1), (ri - 1, i), (ri + 1, i)) if k in grid]
        for r in group:
            if id(r) not in tagged_ids:
                continue
            c = local[id(r)]
            inside = [s for (ri, i), s in grid.items()
                      if c[:, 0].min() - 1e-6 <= x0 + (i + 0.5) * slot_w <= c[:, 0].max() + 1e-6
                      and c[:, 1].min() - 1e-6 <= (rows[ri - 1][0] + rows[ri - 1][1]) / 2 <= c[:, 1].max() + 1e-6]
            if not inside:  # a box narrower than a slot: the slot nearest its middle
                mid_x, mid_y = c[:, 0].mean(), c[:, 1].mean()
                inside = [min(grid.values(), key=lambda s: abs(x0 + (s.position - 0.5) * slot_w - mid_x)
                              + abs(sum(rows[s.row - 1]) / 2 - mid_y))]
            current[r.medication_key] = [s.slot_id for s in inside]
    return Shelving(slots=slots, rows=rows_out, current=current)


def save_shelving(rooms_dir: Path, room: Room, rows: List[RoomBox]) -> None:
    path = room_dir(rooms_dir, room.room_id) / SHELVING_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"rows": [r.model_dump(mode="json") for r in rows]}, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- what needs a shelf


def display_name(name: str) -> str:
    return " ".join(ACRONYMS.get(w.upper(), w.capitalize()) for w in name.split())


def demand(room: Room, catalog: Catalog, shipments: Dict[str, Any], on_hand: Dict[str, int]) -> List[Dict[str, Any]]:
    """Medications shelved in this room plus every accepted line of a not-yet-stocked shipment."""
    known = {m.medication_key: m for m in catalog.medications}
    meds: Dict[str, Dict[str, Any]] = {}

    def entry(key: str, name: str, strength: str, unit: str) -> Dict[str, Any]:
        return meds.setdefault(key, {
            "medication_key": key, "name": name, "strength": strength, "unit": unit,
            "bottles_on_hand": on_hand.get(key, 0), "incoming_bottles": 0, "earliest_expiry": None,
            "in_catalog": key in known, "shelved": False, "shipments": [],
        })

    for r in room.regions:
        if r.region_type == "designated_shelf" and r.medication_key in known:
            m = known[r.medication_key]
            entry(m.medication_key, m.name, m.strength, m.unit)["shelved"] = True
    for s in shipments.values():
        if s.get("status") != "imported":
            continue
        for line in s["lines"]:
            if not line["accepted"]:
                continue
            key = line["medication_key"]
            m = known.get(key)
            e = entry(key, m.name if m else display_name(line["name"]), m.strength if m else line["strength"].lower(),
                      m.unit if m else line["unit"])
            e["incoming_bottles"] += line["accepted"]
            e["earliest_expiry"] = min(filter(None, [e["earliest_expiry"], line["expiry"]]))
            if s["invoice"] not in e["shipments"]:
                e["shipments"].append(s["invoice"])
    for e in meds.values():
        e["bottles"] = e["bottles_on_hand"] + e["incoming_bottles"]
    return sorted(meds.values(), key=lambda e: e["medication_key"])


def _words(name: str) -> List[str]:
    return [w for w in name.upper().replace("-", " ").replace("/", " ").split() if w.isalpha()]


def _base(name: str) -> str:
    words = _words(name)
    return words[0] if words else name.upper()


def _sound_key(word: str) -> str:
    """A rough phonetic key: spellings of one sound merge, doubled letters and inner vowels drop."""
    w = word.upper()
    for a, b in (("PH", "F"), ("CK", "K"), ("QU", "KW"), ("X", "KS"), ("Y", "I"), ("Z", "S")):
        w = w.replace(a, b)
    w = re.sub(r"C(?=[EI])", "S", w).replace("C", "K")
    w = re.sub(r"(.)\1+", r"\1", w)
    return w[:1] + re.sub(r"[AEIOU]", "", w[1:])


def _stem(word: str) -> Optional[str]:
    return next((s for s in CLASS_STEMS if word.endswith(s) and len(word) > len(s) + 2), None)


def mixup(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Why two medications could be picked in place of each other, or None."""
    ba, bb = _base(a["name"]), _base(b["name"])
    if ba == bb:
        return MIXUP_KINDS[0], f"Both are {display_name(ba)}: {a['strength']} vs {b['strength']}."
    if frozenset((ba, bb)) in KNOWN_MIXUPS:
        return MIXUP_KINDS[1], "Listed as a commonly confused pair."
    ka, kb = _sound_key(ba), _sound_key(bb)
    if min(len(ka), len(kb)) >= 3 and SequenceMatcher(None, ka, kb).ratio() >= SOUND_ALIKE_RATIO:
        return MIXUP_KINDS[2], "The names sound alike when spoken."
    stem = _stem(ba)
    if stem and stem == _stem(bb):
        return MIXUP_KINDS[4], f"Same class (-{stem.lower()}); labels look alike."
    if SequenceMatcher(None, ba, bb).ratio() >= SIMILAR_NAME_RATIO or (
            min(len(ba), len(bb)) >= 6 and ba[:SHARED_PREFIX] == bb[:SHARED_PREFIX]):
        return MIXUP_KINDS[3], "The names look alike on a label."
    return None


def risk_pairs(meds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pairs that are easy to confuse when picking, found by name rules (source "built-in")."""
    pairs = []
    for i, a in enumerate(meds):
        for b in meds[i + 1:]:
            found = mixup(a, b)
            if found:
                pairs.append({"medication_keys": [a["medication_key"], b["medication_key"]], "kind": found[0],
                              "reason": found[1], "source": "built-in"})
    return pairs


def extra_pairs(proposed: Any, meds: List[Dict[str, Any]], known: List[Dict[str, Any]],
                source: str) -> List[Dict[str, Any]]:
    """Validated pairs from outside the name rules (the model's, or a reviewer's); unknown keys are dropped."""
    keys = {m["medication_key"] for m in meds}
    seen = {frozenset(p["medication_keys"]) for p in known}
    out = []
    for p in proposed if isinstance(proposed, list) else []:
        pair = p.get("medication_keys") if isinstance(p, dict) else p
        if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(k, str) for k in pair):
            continue
        a, b = sorted(pair)
        if a == b or a not in keys or b not in keys or frozenset((a, b)) in seen:
            continue
        seen.add(frozenset((a, b)))
        kind = p.get("kind") if isinstance(p, dict) and p.get("kind") in MIXUP_KINDS else MIXUP_KINDS[1]
        reason = str(p.get("reason") or "") if isinstance(p, dict) else ""
        out.append({"medication_keys": [a, b], "kind": kind, "reason": reason[:200] or "Flagged as a mix-up risk.",
                    "source": source})
    return out


# ---------------------------------------------------------------- planners


def _check(assignments: Dict[str, str], meds: List[Dict[str, Any]], slots: Dict[str, Slot]) -> None:
    keys = {m["medication_key"] for m in meds}
    if set(assignments) != keys:
        missing, extra = sorted(keys - set(assignments)), sorted(set(assignments) - keys)
        raise ValueError(f"Every medication needs exactly one slot (missing {missing or 'none'}, unknown {extra or 'none'}).")
    unknown = sorted(set(assignments.values()) - set(slots))
    if unknown:
        raise ValueError(f"Unknown slots: {', '.join(unknown)}.")
    if len(set(assignments.values())) != len(assignments):
        raise ValueError("Two medications were given the same slot.")


def too_close(a: Slot, b: Slot) -> bool:
    """Same shelf row, or touching (side by side or directly above/below)."""
    return (a.unit, a.row) == (b.unit, b.row) or b.slot_id in a.neighbors


def conflicts(risks: List[Dict[str, Any]], assignments: Dict[str, str], slots: Dict[str, Slot]) -> List[Dict[str, Any]]:
    """Risk pairs the assignments leave on one shelf or in touching slots."""
    return [p for p in risks if all(k in assignments for k in p["medication_keys"])
            and too_close(*(slots[assignments[k]] for k in p["medication_keys"]))]


class _Scorer:
    """Plan cost as (mix-up pairs too close, of those how many touch, soft cost); lower is better,
    compared in that order, so a pair the shelving can't separate is at least not side by side."""

    def __init__(self, meds: List[Dict[str, Any]], shelf: Shelving, risks: List[Dict[str, Any]]):
        self.meds = {m["medication_key"]: m for m in meds}
        self.shelf, self.slots = shelf, shelf.by_id()
        self.pairs = [tuple(p["medication_keys"]) for p in risks]
        self.most = max([m["bottles"] for m in meds] + [1])

    def was(self, key: str) -> List[str]:
        return self.shelf.current.get(key, []) if self.meds[key]["shelved"] else []

    def own(self, key: str, sid: str) -> float:
        m, s = self.meds[key], self.slots[sid]
        busy = 1 + m["bottles"] / self.most
        moved = MOVE_PENALTY if self.was(key) and sid not in self.was(key) else 0.0
        return BAND_PENALTY[s.band] * busy + 0.5 * busy * (s.counter_m or 0.0) + moved

    def __call__(self, placed: Dict[str, str]) -> Tuple[int, int, float]:
        close, touching, soft = 0, 0, sum(self.own(k, sid) for k, sid in placed.items())
        for a, b in self.pairs:
            if a in placed and b in placed:
                sa, sb = self.slots[placed[a]], self.slots[placed[b]]
                if too_close(sa, sb):
                    close += 1
                    touching += sb.slot_id in sa.neighbors
                elif sa.unit == sb.unit:
                    soft += SAME_UNIT_PENALTY
        return close, touching, round(soft, 6)


def builtin_plan(meds: List[Dict[str, Any]], shelf: Shelving, risks: List[Dict[str, Any]]) -> Dict[str, str]:
    """Deterministic plan: keep shelved medications unless that leaves a mix-up pair on one shelf,
    place the rest busiest first, then move or swap medications while that separates more pairs
    or lowers the soft cost."""
    score = _Scorer(meds, shelf, risks)
    order = [m["medication_key"] for m in sorted(meds, key=lambda m: (-m["bottles"], m["medication_key"]))]
    placed: Dict[str, str] = {}
    for key in order:  # the busier of two clashing shelved medications stays put
        for sid in score.was(key):
            if sid not in placed.values() and score({**placed, key: sid})[0] == score(placed)[0]:
                placed[key] = sid
                break
    for key in order:
        if key not in placed:
            free = [s.slot_id for s in shelf.slots if s.slot_id not in placed.values()]
            placed[key] = min(free, key=lambda sid: score({**placed, key: sid}))
    best = score(placed)
    for _ in range(4 * len(order)):  # local search; each step strictly improves, so it ends
        improved = False
        for key in order:
            taken = {sid: k for k, sid in placed.items()}
            for s in shelf.slots:
                if s.slot_id == placed[key]:
                    continue
                trial = dict(placed, **{key: s.slot_id})
                other = taken.get(s.slot_id)
                if other:
                    trial[other] = placed[key]
                cost = score(trial)
                if cost < best:
                    placed, best, improved = trial, cost, True
                    taken = {sid: k for k, sid in placed.items()}
        if not improved:
            break
    return placed


def meta_plan(meds: List[Dict[str, Any]], shelf: Shelving, risks: List[Dict[str, Any]], client=None):
    """Ask the Llama API for mix-up pairs and a plan.

    Returns (mix-up pairs, assignments or None, reasons, notes, why the assignments were
    unusable). The pairs are kept even when the assignments fail validation, so the
    fallback planner separates them too. Raises MetaApiError if there is no usable answer.
    """
    context = {
        "medications": [{
            "medication_key": m["medication_key"], "name": m["name"], "strength": m["strength"], "form": m["unit"],
            "bottles": m["bottles"], "earliest_expiry": m["earliest_expiry"],
            "current_slot": (shelf.current.get(m["medication_key"]) or [None])[0] if m["shelved"] else None,
        } for m in meds],
        "slots": [{"slot_id": s.slot_id, "shelf_id": f"u{s.unit}-r{s.row}", "unit": s.unit, "row_from_floor": s.row,
                   "position": s.position, "height": s.band, "distance_to_counter_m": s.counter_m,
                   "neighbors": s.neighbors} for s in shelf.slots],
        "risk_pairs": [{k: p[k] for k in ("medication_keys", "kind", "reason")} for p in risks],
    }
    answer = meta_llama.structured_chat(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(context)}],
        PLAN_SCHEMA, "shelf_layout", client=client,
    )
    pairs = extra_pairs(answer.get("mixup_pairs"), meds, risks, "meta-llama")
    notes = [str(n)[:300] for n in answer.get("notes") or [] if isinstance(n, str)][:5]
    try:
        rows = answer.get("assignments")
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("The answer had no assignment list.")
        assignments: Dict[str, str] = {}
        reasons: Dict[str, str] = {}
        for r in rows:
            key, sid = str(r.get("medication_key", "")), str(r.get("slot_id", ""))
            if key in assignments:
                raise ValueError(f"{key} was assigned twice.")
            assignments[key] = sid
            reasons[key] = str(r.get("reason", ""))[:200]
        _check(assignments, meds, shelf.by_id())
    except ValueError as exc:
        return pairs, None, {}, notes, str(exc)
    return pairs, assignments, reasons, notes, None


def _default_reason(m: Dict[str, Any], s: Slot, status: str, apart: List[str]) -> str:
    if status == "kept":
        text = "Already shelved here."
    else:
        near = f", {s.counter_m} m from the counter" if s.counter_m is not None else ""
        text = f"{m['bottles']} bottles; {s.band} height{near}."
    if apart:
        text += f" Kept off the shelf of {', '.join(apart[:3])}{' and others' if len(apart) > 3 else ''}."
    return text


def separation(risks: List[Dict[str, Any]], assignments: Dict[str, str], slots: Dict[str, Slot]) -> List[Dict[str, Any]]:
    """Where each risk pair ended up; `warning` marks pairs on one shelf or in touching slots."""
    out = []
    for p in risks:
        a, b = (slots[assignments[k]] for k in p["medication_keys"])
        same_shelf = (a.unit, a.row) == (b.unit, b.row)
        status = ("same shelf, side by side" if same_shelf and b.slot_id in a.neighbors else
                  "same shelf" if same_shelf else
                  "shelf directly above or below" if b.slot_id in a.neighbors else
                  "different shelves, same unit" if a.unit == b.unit else "different units")
        out.append({**p, "slots": [a.slot_id, b.slot_id], "separation": status, "warning": too_close(a, b)})
    return out


def _names(keys, meds_by_key) -> str:
    return " and ".join(f"{meds_by_key[k]['name']} {meds_by_key[k]['strength']}" for k in keys)


def plan_layout(rooms_dir: Path, room: Room, catalog: Catalog, shipments: Dict[str, Any], on_hand: Dict[str, int],
                use_meta: bool = True, client=None) -> Dict[str, Any]:
    shelf = shelving(rooms_dir, room)
    meds = demand(room, catalog, shipments, on_hand)
    if not meds:
        raise ValueError("Nothing to place: import a shipment first, or tag shelves for configured medications.")
    if len(meds) > len(shelf.slots):
        raise ValueError(f"The tagged shelving has {len(shelf.slots)} slots (each at least {int(MIN_SLOT_M * 100)} cm "
                         f"wide) for {len(meds)} medications. Tag more shelving on the Room page.")
    by_key = {m["medication_key"]: m for m in meds}
    risks = risk_pairs(meds)
    slots = shelf.by_id()
    source, model, fallback_reason, reasons, notes = "built-in", None, None, {}, []
    assignments: Optional[Dict[str, str]] = None
    if use_meta and meta_llama.api_key():
        try:
            pairs, proposed, reasons, notes, problem = meta_plan(meds, shelf, risks, client=client)
            risks += pairs
            model = meta_llama.model_name()
            if proposed is None:
                fallback_reason = f"Meta Llama placement not used: {problem}"
            else:
                # The model's placement must separate mix-up pairs at least as well as the built-in plan.
                builtin = builtin_plan(meds, shelf, risks)
                close, fallback = conflicts(risks, proposed, slots), conflicts(risks, builtin, slots)
                if len(close) > len(fallback):
                    worst = close[0]["medication_keys"]
                    fallback_reason = (f"Meta Llama placement not used: it put {_names(worst, by_key)} on one shelf "
                                       f"or side by side; the built-in planner separates them.")
                    assignments = builtin
                else:
                    assignments, source = proposed, "meta-llama"
        except meta_llama.MetaApiError as exc:
            fallback_reason = f"Meta Llama plan not used: {exc}"
    elif use_meta:
        fallback_reason = "No Meta API token configured (META_API_KEY); used the built-in planner."
    if assignments is None:
        assignments = builtin_plan(meds, shelf, risks)
    if source == "built-in":
        reasons = {}
    placed_risks = separation(risks, assignments, slots)
    unresolved = [p for p in placed_risks if p["warning"]]
    if unresolved:
        notes = notes + [f"{len(unresolved)} mix-up pair(s) could not be put on different shelves with this shelving. "
                         f"Tag another shelf row or unit on the Room page, or separate them by hand."]
    apart: Dict[str, List[str]] = {k: [] for k in by_key}
    for p in placed_risks:
        if not p["warning"]:
            a, b = p["medication_keys"]
            apart[a].append(by_key[b]["name"])
            apart[b].append(by_key[a]["name"])
    rows = []
    for m in meds:
        s = slots[assignments[m["medication_key"]]]
        was = shelf.current.get(m["medication_key"], []) if m["shelved"] else []
        status = "new" if not m["shelved"] else "kept" if s.slot_id in was else "moved"
        rows.append({**m, "slot_id": s.slot_id, "label": s.label, "band": s.band, "counter_m": s.counter_m,
                     "status": status, "box": s.box.model_dump(mode="json"),
                     "reason": reasons.get(m["medication_key"])
                     or _default_reason(m, s, status, sorted(set(apart[m["medication_key"]])))})
    return {
        "room_id": room.room_id, "room_version": room.room_version, "source": source, "model": model,
        "fallback_reason": fallback_reason, "notes": notes, "slots_total": len(shelf.slots),
        "assignments": rows, "risks": placed_risks, "unresolved_risks": len(unresolved),
    }


# ---------------------------------------------------------------- applying


def catalog_with(catalog: Catalog, meds: List[Dict[str, Any]]) -> Tuple[Catalog, List[str]]:
    """The catalog plus any planned medication it lacks (no opening stock: stock arrives by stocking)."""
    have = {m.medication_key for m in catalog.medications}
    added = [Medication(medication_key=m["medication_key"], name=m["name"], strength=m["strength"], unit=m["unit"])
             for m in meds if m["medication_key"] not in have]
    for m in added:
        if medication_key_for(m.name, m.strength) != m.medication_key:
            raise ValueError(f"Cannot add {m.medication_key}: its name and strength give a different key.")
    return catalog.model_copy(update={"medications": list(catalog.medications) + added}), [m.medication_key for m in added]


def layout_regions(room: Room, shelf: Shelving, assignments: Dict[str, str], meds: List[Dict[str, Any]]) -> List[Region3D]:
    """The room's regions with its shelves replaced by one slot-sized box per planned medication."""
    slots = shelf.by_id()
    _check(assignments, meds, slots)
    return [r for r in room.regions if r.region_type != "designated_shelf"] + [
        Region3D(region_id=shelf_region_id(key), region_type="designated_shelf", medication_key=key, box=slots[sid].box)
        for key, sid in sorted(assignments.items())
    ]
