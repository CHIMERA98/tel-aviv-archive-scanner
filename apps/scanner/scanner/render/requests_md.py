"""requests.md: one section per Haifa permit request — the richest text Complot has, at no extra cost."""

from datetime import date

from scanner.models import Document, PermitRequest, ScanManifest

# Events that tell the building's story; routine office steps (fees, mailing, internal waits) are left out.
KEY_EVENTS = (
    "החלטה",
    "דיון",
    "חתימת ההיתר",
    "הופק היתר",
    "אושרה בקשה",
    'פס"ד',
    "צו ",
    "בניה ללא היתר",
    "תעודת גמר",
    "טופס 4",
    "אכלוס",
    "ביטול",
    "סירוב",
    "הריסה",
)
ROUTINE = ("תשלום", "אגר", "דרישות", "פקדון", "גביה")


def _is_key(description: str) -> bool:
    return any(word in description for word in KEY_EVENTS) and not any(word in description for word in ROUTINE)


def _d(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else "—"


def _section(request: PermitRequest, documents: list[Document]) -> list[str]:
    lines = [f"## בקשה {request.request_no} · הוגשה {_d(request.submitted)}", ""]
    facts = [
        ("תיק בניין", request.building_file),
        ("סוג הבקשה", request.request_type),
        ("שימוש עיקרי", request.main_use),
        ("תיאור", request.description),
        ("היתר", f"{request.permit_no} מיום {_d(request.permit_date)}" if request.permit_no else None),
        ("מספר ברישוי זמין", request.online_request_no),
    ]
    lines += [f"- **{label}:** {value}" for label, value in facts if value]
    if request.essence:
        lines += ["", "**מהות הבקשה:** " + " ".join(request.essence)]
    if request.parties:
        lines += ["", "**בעלי עניין:** " + "; ".join(f"{role}: {name}" for role, name in request.parties)]
    key_events = [e for e in request.events if _is_key(e.description)]
    if key_events:
        lines += ["", "**אירועים מרכזיים:**"]
        lines += [f"- {_d(e.date)} — {e.description}" for e in sorted(key_events, key=lambda e: e.date or date.min)]
    if request.decisions:
        lines += ["", "**החלטות הוועדה:**", "", *[f"> {line}" for line in request.decisions]]
    if documents:
        lines += ["", "**מסמכים:**"]
        for doc in documents:
            state = (
                "" if doc.status == "ready" else " (עדיין לא הורד)" if doc.status == "pending" else " (ההורדה נכשלה)"
            )
            lines.append(f"- `{doc.doc_id}` {doc.title} · {_d(doc.date)}{state}")
    if request.url:
        lines += ["", f"[הבקשה באתר ההנדסי]({request.url})"]
    return [*lines, ""]


def render(manifest: ScanManifest) -> str:
    by_request: dict[str, list[Document]] = {}
    for doc in manifest.documents:
        if doc.source == "archive" and doc.request_no:
            by_request.setdefault(doc.request_no, []).append(doc)
    lines = [
        f"# בקשות להיתר: {manifest.address.label_he}",
        "",
        f"תיקי בניין: {', '.join(manifest.building_files) or '—'} · {len(manifest.requests)} בקשות. "
        "מקור: אתר ההנדסה של עיריית חיפה (קומפלוט).",
        "",
    ]
    for request in sorted(manifest.requests, key=lambda r: r.submitted or date.min):
        lines += _section(request, by_request.get(request.request_no, []))
    return "\n".join(lines)
