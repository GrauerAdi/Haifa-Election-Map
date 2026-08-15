# -*- coding: utf-8 -*-
"""
Ballot-letter -> party-name mapping for the November 2022 (25th Knesset) election.

Best-effort mapping for the major parties that passed the electoral threshold,
sourced from public election-results reporting. Not independently verified
against the official Central Elections Committee ballot-letter list — spot
check before treating as authoritative. Any code not in this dict (mostly
minor/technical list letters with negligible vote counts) falls back to
displaying the raw Hebrew ballot letter via get_party_name().
"""

PARTY_NAMES = {
    "מחל": "הליכוד",
    "פה": "יש עתיד",
    "ט": "הציונות הדתית",
    "אמת": "העבודה",
    "שס": "ש\"ס",
    "ג": "יהדות התורה",
    "ל": "ישראל ביתנו",
    "עם": "רע\"מ",
    "ום": "חד\"ש-תע\"ל",
    "כן": "המחנה הממלכתי",
    "מרצ": "מרצ",
}


def get_party_name(code: str) -> str:
    """Return the party name for a ballot-letter code, or the raw code if unmapped."""
    return PARTY_NAMES.get(code, code)
