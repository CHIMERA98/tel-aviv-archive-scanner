"""PROMPT.he.md / PROMPT.en.md: instructions the user pastes into their own AI along with the files."""

from scanner.models import ScanManifest


def _counts(manifest: ScanManifest) -> dict[str, int]:
    counts = {"archive": 0, "plan": 0, "map": 0}
    for doc in manifest.documents:
        counts[doc.source] += 1
    return counts


def prompt_en(manifest: ScanManifest) -> str:
    c = _counts(manifest)
    b = manifest.building
    return f"""# Building research: {manifest.address.label_en}

You are helping me research the history of a building in Tel Aviv-Yafo. The attached files were collected
from public municipal sources by the Tel Aviv Archive Scanner (scan `{manifest.scan_id}`).

## What is in the files
- `index.csv` — one row per document: `doc_id`, source, title, document type, date, permit/request
  numbers, plan number and status, page count, file path and original link. **Read it first.**
- `archive/` — {c["archive"]} scanned documents from the municipal engineering archive (building file
  {manifest.archive_folder_no or "unknown"}, building ID {b.building_id}). Mostly scans of typed or handwritten Hebrew.
- `plans/` — {c["plan"]} files from the {len(manifest.plans)} zoning plans (תב"ע) that cover the building.
- `maps/` — {c["map"]} historic maps cropped around the building (red circle = the building).

Known facts: year built {b.year_built or "unknown"}, floors {b.floors or "unknown"},
block/parcel (גוש/חלקה) {manifest.address.gush or "?"}/{manifest.address.helka or "?"}.

## How to work
1. Start from `index.csv`. Group documents by date and type; identify building permits (היתר בניה),
   permit requests (בקשה להיתר), plans and correspondence.
2. Open only the documents you need — there may be hundreds of pages. If you cannot read all files at
   once, work in batches: earliest permits first, then later additions and changes.
3. Read the scans yourself (they are images; there is no text layer). Quote Hebrew where useful and
   translate it.
4. Always cite the `doc_id` and date for every claim. Say clearly when something is unreadable or uncertain.

## What I want
- A chronological history of the building: original construction, additions, changes of use,
  demolitions or preservation decisions.
- Key permits with numbers and dates.
- The zoning plans that apply and what each one allows or changed.
- What the historic maps show about the site over time.
- Open questions and which documents would answer them.
"""


def prompt_he(manifest: ScanManifest) -> str:
    c = _counts(manifest)
    b = manifest.building
    return f"""# מחקר מבנה: {manifest.address.label_he}

אני חוקר/ת את ההיסטוריה של מבנה בתל אביב-יפו. הקבצים המצורפים נאספו ממקורות עירוניים ציבוריים
באמצעות סורק הארכיון של תל אביב (סריקה `{manifest.scan_id}`).

## מה יש בקבצים
- `index.csv` — שורה לכל מסמך: `doc_id`, מקור, כותרת, סוג מסמך, תאריך, מספרי היתר/בקשה,
  מספר תב"ע וסטטוס, מספר עמודים, נתיב הקובץ וקישור למקור. **קרא/י אותו קודם.**
- `archive/` — {c["archive"]} מסמכים סרוקים מארכיון ההנדסה (תיק בניין {manifest.archive_folder_no or "לא ידוע"},
  מזהה מבנה {b.building_id}). בעיקר סריקות של עברית מודפסת או בכתב יד.
- `plans/` — {c["plan"]} קבצים מתוך {len(manifest.plans)} תוכניות בניין עיר (תב"ע) החלות על המבנה.
- `maps/` — {c["map"]} מפות היסטוריות חתוכות סביב המבנה (עיגול אדום = המבנה).

נתונים ידועים: שנת בנייה {b.year_built or "לא ידועה"}, מספר קומות {b.floors or "לא ידוע"},
גוש/חלקה {manifest.address.gush or "?"}/{manifest.address.helka or "?"}.

## איך לעבוד
1. התחל/י מ-`index.csv`. קבץ/י את המסמכים לפי תאריך וסוג; זהה/י היתרי בנייה, בקשות להיתר,
   תוכניות והתכתבויות.
2. פתח/י רק את המסמכים הנחוצים — ייתכנו מאות עמודים. אם אי אפשר לקרוא הכול בבת אחת,
   עבוד/י בקבוצות: קודם ההיתרים המוקדמים, אחר כך תוספות ושינויים.
3. קרא/י את הסריקות בעצמך (הן תמונות, אין בהן שכבת טקסט).
4. לכל טענה ציין/י את ה-`doc_id` והתאריך. ציין/י במפורש כשמשהו לא קריא או לא ודאי.

## מה אני צריך/ה
- היסטוריה כרונולוגית של המבנה: בנייה מקורית, תוספות, שינויי שימוש, הריסות או החלטות שימור.
- ההיתרים המרכזיים עם מספרים ותאריכים.
- תוכניות הבניין החלות ומה כל אחת מהן מתירה או שינתה.
- מה המפות ההיסטוריות מראות על המגרש לאורך השנים.
- שאלות פתוחות ואילו מסמכים יכולים לענות עליהן.
"""
