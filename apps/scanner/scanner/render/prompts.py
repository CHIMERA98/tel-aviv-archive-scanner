"""PROMPT.he.md / PROMPT.en.md: instructions the user pastes into their own AI along with the files."""

from scanner.models import ScanManifest


def _counts(manifest: ScanManifest) -> dict[str, int]:
    counts = {"archive": 0, "plan": 0, "map": 0}
    for doc in manifest.documents:
        counts[doc.source] += 1
    return counts


def _pending(manifest: ScanManifest) -> int:
    return sum(1 for d in manifest.documents if d.status == "pending")


def _intro_en(manifest: ScanManifest) -> str:
    if manifest.city == "haifa":
        return (
            "You are helping me research the history of a building in Haifa. The attached files were collected\n"
            f"from public municipal sources by the Haifa Archive Scanner (scan `{manifest.scan_id}`)."
        )
    return (
        "You are helping me research the history of a building in Tel Aviv-Yafo. The attached files were collected\n"
        f"from public municipal sources by the Tel Aviv Archive Scanner (scan `{manifest.scan_id}`)."
    )


def _intro_he(manifest: ScanManifest) -> str:
    if manifest.city == "haifa":
        return (
            "אני חוקר/ת את ההיסטוריה של מבנה בחיפה. הקבצים המצורפים נאספו ממקורות עירוניים ציבוריים\n"
            f"באמצעות סורק הארכיון של חיפה (סריקה `{manifest.scan_id}`)."
        )
    return (
        "אני חוקר/ת את ההיסטוריה של מבנה בתל אביב-יפו. הקבצים המצורפים נאספו ממקורות עירוניים ציבוריים\n"
        f"באמצעות סורק הארכיון של תל אביב (סריקה `{manifest.scan_id}`)."
    )


def _archive_en(manifest: ScanManifest, c: dict[str, int]) -> str:
    if manifest.city == "haifa":
        pending = _pending(manifest)
        lines = [
            f"- `archive/` — {c['archive']} documents attached to {len(manifest.requests)} permit requests in Haifa's "
            f"engineering site (building files {', '.join(manifest.building_files) or 'unknown'}): signed permits, "
            "permit plans, committee decisions. Mostly scans.",
            "- `requests.md` — every permit request: type, use, description, applicant and architect, permit, key "
            "events and committee decisions. **Read it right after `index.csv`; it is the building's timeline.**",
        ]
        if pending:
            lines.append(
                f"- {pending} documents were still downloading when these files were made (`status` = pending in "
                "`index.csv`); their `original_url` opens them on the municipal site."
            )
        return "\n".join(lines)
    b = manifest.building
    return (
        f"- `archive/` — {c['archive']} scanned documents from the municipal engineering archive (building file\n"
        f"  {manifest.archive_folder_no or 'unknown'}, building ID {b.building_id}). Mostly scans of typed or "
        "handwritten Hebrew."
    )


def _archive_he(manifest: ScanManifest, c: dict[str, int]) -> str:
    if manifest.city == "haifa":
        pending = _pending(manifest)
        lines = [
            f"- `archive/` — {c['archive']} מסמכים מ-{len(manifest.requests)} בקשות להיתר באתר ההנדסה של חיפה "
            f"(תיקי בניין {', '.join(manifest.building_files) or 'לא ידוע'}): היתרים חתומים, תוכניות היתר, "
            "החלטות ועדה. בעיקר סריקות.",
            "- `requests.md` — כל הבקשות להיתר: סוג, שימוש, תיאור, מבקש ועורך, היתר, אירועים מרכזיים "
            "והחלטות הוועדה. **קרא/י אותו מיד אחרי `index.csv` — זה ציר הזמן של הבניין.**",
        ]
        if pending:
            lines.append(
                f"- {pending} מסמכים עדיין ירדו כשהקבצים נוצרו (`status` = pending ב-`index.csv`); "
                "הקישור ב-`original_url` פותח אותם באתר העירייה."
            )
        return "\n".join(lines)
    b = manifest.building
    return (
        f"- `archive/` — {c['archive']} מסמכים סרוקים מארכיון ההנדסה "
        f"(תיק בניין {manifest.archive_folder_no or 'לא ידוע'},\n"
        f"  מזהה מבנה {b.building_id}). בעיקר סריקות של עברית מודפסת או בכתב יד."
    )


def _facts_en(manifest: ScanManifest) -> str:
    b, a = manifest.building, manifest.address
    if manifest.city == "haifa":
        facts = [
            f"neighbourhood {a.neighborhood or 'unknown'}",
            f"block/parcel (גוש/חלקה) {a.gush or '?'}/{a.helka or '?'}",
        ]
        if b.height_m:
            facts.append(f"roof height about {b.height_m:.0f} m")
        if b.conservation:
            k = b.conservation
            facts.append(
                f"conservation register: {k.status or 'listed'}"
                + (f", grade {k.grade}" if k.grade else "")
                + (f", file {k.file_no}" if k.file_no else "")
                + (f", survey {k.survey}" if k.survey else "")
            )
        return "Known facts: " + "; ".join(facts) + "."
    return (
        f"Known facts: year built {b.year_built or 'unknown'}, floors {b.floors or 'unknown'},\n"
        f"block/parcel (גוש/חלקה) {a.gush or '?'}/{a.helka or '?'}."
    )


def _facts_he(manifest: ScanManifest) -> str:
    b, a = manifest.building, manifest.address
    if manifest.city == "haifa":
        facts = [f"שכונה {a.neighborhood or 'לא ידועה'}", f"גוש/חלקה {a.gush or '?'}/{a.helka or '?'}"]
        if b.height_m:
            facts.append(f"גובה הגג כ-{b.height_m:.0f} מ'")
        if b.conservation:
            k = b.conservation
            facts.append(
                f"רשימת השימור: {k.status or 'רשום'}"
                + (f", דרגה {k.grade}" if k.grade else "")
                + (f", תיק {k.file_no}" if k.file_no else "")
                + (f", סקר {k.survey}" if k.survey else "")
            )
        return "נתונים ידועים: " + "; ".join(facts) + "."
    return (
        f"נתונים ידועים: שנת בנייה {b.year_built or 'לא ידועה'}, מספר קומות {b.floors or 'לא ידוע'},\n"
        f"גוש/חלקה {a.gush or '?'}/{a.helka or '?'}."
    )


def prompt_en(manifest: ScanManifest) -> str:
    c = _counts(manifest)
    return f"""# Building research: {manifest.address.label_en}

{_intro_en(manifest)}

## What is in the files
- `index.csv` — one row per document: `doc_id`, source, title, document type, date, permit/request
  numbers, plan number and status, page count, file path and original link. **Read it first.**
{_archive_en(manifest, c)}
- `plans/` — {c["plan"]} files from the {len(manifest.plans)} zoning plans (תב"ע) that cover the building.
- `maps/` — {c["map"]} historic maps cropped around the building (red circle = the building).

{_facts_en(manifest)}

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
    return f"""# מחקר מבנה: {manifest.address.label_he}

{_intro_he(manifest)}

## מה יש בקבצים
- `index.csv` — שורה לכל מסמך: `doc_id`, מקור, כותרת, סוג מסמך, תאריך, מספרי היתר/בקשה,
  מספר תב"ע וסטטוס, מספר עמודים, נתיב הקובץ וקישור למקור. **קרא/י אותו קודם.**
{_archive_he(manifest, c)}
- `plans/` — {c["plan"]} קבצים מתוך {len(manifest.plans)} תוכניות בניין עיר (תב"ע) החלות על המבנה.
- `maps/` — {c["map"]} מפות היסטוריות חתוכות סביב המבנה (עיגול אדום = המבנה).

{_facts_he(manifest)}

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
