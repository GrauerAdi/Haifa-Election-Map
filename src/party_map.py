# -*- coding: utf-8 -*-
"""
Ballot-letter -> party-name mapping for the November 2022 (25th Knesset) election.

Covers all 40 lists that ran, both the 11 that passed the electoral threshold
and the 29 minor/technical lists that didn't. Sourced from Wikipedia's
official 25th Knesset results table (he.wikipedia.org/wiki/הבחירות_לכנסת_העשרים_וחמש),
parsed directly from the raw page HTML (not an AI-summarized fetch, to rule out
transcription errors) and cross-checked row-by-row against the article's cited
sources (ילקוט הפרסומים 10852/10889). Names are shortened from the full ballot
nickname (which includes "בראשות/בהנהגת <name>" leadership text) to the
commonly-used party name, matching the style already used for the 11 major
parties — except the ת list, which is a fusion of four small parties with no
single short name, so its full registered name is kept as-is.
"""

PARTY_NAMES = {
    # Passed the electoral threshold (3.25%)
    "מחל": "הליכוד",
    "פה": "יש עתיד",
    "ט": "הציונות הדתית",
    "כן": "המחנה הממלכתי",
    "שס": "ש\"ס",
    "ג": "יהדות התורה",
    "ל": "ישראל ביתנו",
    "עם": "רע\"מ",
    "ום": "חד\"ש-תע\"ל",
    "אמת": "העבודה",
    "מרצ": "מרצ",
    # Below threshold, ordered by descending vote count
    "ד": "בל\"ד",
    "ב": "הבית היהודי",
    "אצ": "חופש כלכלי",
    "קץ": "באומץ בשבילך",
    "יז": "הכלכלית החדשה",
    "צ": "צעירים בוערים",
    "ף": "הפיראטים",
    "ק": "קול הסביבה והחי",
    "ת": "דעת טוב ורע וברית שבט אברהם-עלה ירוק ואוסרת אל איסלמיה",
    "ני": "נתיב",
    "קנ": "כל קול קובע",
    "נק": "יש כיוון",
    "י": "ישראל חופשית דמוקרטית",
    "קך": "סדר חדש",
    "נץ": "העצמאים החדשים",
    "ץ": "מנהיגות חברתית",
    "רז": "רשימת שלושים/ארבעים",
    "ך": "אני ואתה",
    "ז": "שחר כח חברתי",
    "קי": "הלב היהודי",
    "יק": "הגוש התנ\"כי",
    "נז": "כבוד האדם",
    "נר": "אנחנו",
    "זץ": "צומת",
    "יץ": "צו השעה",
    "נף": "שמע",
    "ינ": "איחוד בני הברית",
    "זך": "קמ\"ה",
    "זנ": "כח להשפיע",
}


def get_party_name(code: str) -> str:
    """Return the party name for a ballot-letter code, or the raw code if unmapped."""
    return PARTY_NAMES.get(code, code)
