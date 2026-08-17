# -*- coding: utf-8 -*-
"""
Party bloc groupings for the November 2022 (25th Knesset) election — additional
context alongside the existing per-party breakdown, not a replacement for it.

Bloc membership reflects how each party actually aligned politically, not
just whether it crossed the 3.25% electoral threshold:
- "ימני-חרדי" (right-religious): the four parties that formed the governing
  coalition after this election (64 seats combined).
- "שינוי" (change bloc / anti-Netanyahu): includes מרצ despite it winning 0
  seats (it fell just under the threshold) — grouped by political alignment,
  not by whether it crossed the threshold.
- "רשימות ערביות" (Arab-majority parties): kept separate from the "שינוי"
  bloc, even though רע"מ was part of the outgoing coalition — Arab-majority
  lists are conventionally reported as their own category rather than folded
  into either Jewish-Israeli bloc. Includes בל"ד despite its 0 seats, for the
  same political-alignment reasoning as מרצ above.

Every other list (~28 minor/technical lists with negligible vote counts) is
bucketed as "מתחת לאחוז החסימה" (below the electoral threshold) — derived via
full_blocs() as the set difference against the full party-code list, not
hand-enumerated, so it can't silently drift out of sync if that list changes.
"""

BLOCS = {
    "bloc_right_religious": ["מחל", "ט", "שס", "ג"],
    "bloc_change": ["פה", "כן", "ל", "אמת", "מרצ"],
    "bloc_arab": ["עם", "ום", "ד"],
}

BLOC_LABELS = {
    "bloc_right_religious": "גוש ימני-חרדי",
    "bloc_change": "גוש השינוי",
    "bloc_arab": "רשימות ערביות",
    "bloc_below_threshold": "מתחת לאחוז החסימה",
}


def full_blocs(all_party_codes):
    """BLOCS plus a residual 'below threshold' bucket containing every party
    code not already assigned to a named bloc."""
    assigned = {code for codes in BLOCS.values() for code in codes}
    residual = [c for c in all_party_codes if c not in assigned]
    return {**BLOCS, "bloc_below_threshold": residual}
