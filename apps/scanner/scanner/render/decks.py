"""Two right-to-left PowerPoint decks: archive documents, and historic maps + zoning plans."""

from collections.abc import Iterable
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation as PresentationType
from pptx.util import Emu, Inches, Pt

from scanner.models import Document, ScanManifest

FONT = "Arial"
INK = RGBColor(0x1F, 0x23, 0x29)
MUTED = RGBColor(0x6B, 0x72, 0x80)
LINK = RGBColor(0x1D, 0x4E, 0xD8)
SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)
INDEX_LINES_PER_SLIDE = 14


def _new_deck(title: str) -> PresentationType:
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    prs.core_properties.title = title
    prs.core_properties.author = "Tel Aviv Archive Scanner"
    return prs


def _blank(prs: PresentationType):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _text(
    slide,
    left,
    top,
    width,
    height,
    lines: Iterable[tuple[str, int, bool]],
    *,
    link: str | None = None,
    color: RGBColor = INK,
    align=PP_ALIGN.RIGHT,
):
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    first = True
    for text, size, bold in lines:
        paragraph = frame.paragraphs[0] if first else frame.add_paragraph()
        first = False
        paragraph.alignment = align
        paragraph._p.get_or_add_pPr().set("rtl", "1" if align == PP_ALIGN.RIGHT else "0")
        run = paragraph.add_run()
        run.text = text
        run.font.size, run.font.bold, run.font.name = Pt(size), bold, FONT
        run.font.color.rgb = color
        run._r.get_or_add_rPr().set("lang", "he-IL")
    if link:
        paragraph = frame.add_paragraph()
        paragraph.alignment = align
        paragraph._p.get_or_add_pPr().set("rtl", "1")
        run = paragraph.add_run()
        run.text = "קישור למקור"
        run.font.size, run.font.name = Pt(14), FONT
        run.font.color.rgb = LINK
        run.hyperlink.address = link
    return box


def _picture(slide, image: Path, left, top, max_w, max_h) -> None:
    pic = slide.shapes.add_picture(str(image), left, top)
    scale = min(max_w / pic.width, max_h / pic.height)
    pic.width, pic.height = Emu(int(pic.width * scale)), Emu(int(pic.height * scale))
    pic.left = Emu(int(left + (max_w - pic.width) / 2))


def _title_slide(prs: PresentationType, heading: str, sub: str) -> None:
    slide = _blank(prs)
    _text(slide, Inches(0.8), Inches(2.6), Inches(11.7), Inches(2.4), [(heading, 40, True), (sub, 20, False)])


def _index_slides(prs: PresentationType, heading: str, lines: list[str]) -> None:
    for start in range(0, len(lines), INDEX_LINES_PER_SLIDE):
        chunk = lines[start : start + INDEX_LINES_PER_SLIDE]
        slide = _blank(prs)
        page = f" ({start // INDEX_LINES_PER_SLIDE + 1})" if len(lines) > INDEX_LINES_PER_SLIDE else ""
        _text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.9), [(heading + page, 28, True)])
        _text(slide, Inches(0.8), Inches(1.3), Inches(11.7), Inches(5.9), [(line, 15, False) for line in chunk])


def _doc_date(doc: Document) -> str:
    if doc.date:
        return doc.date.strftime("%d.%m.%Y")
    return str(doc.year) if doc.year else "ללא תאריך"


def build_archive_deck(manifest: ScanManifest, root: Path, dest: Path) -> None:
    docs = [d for d in manifest.documents if d.source == "archive"]
    prs = _new_deck(f"ארכיון הנדסה — {manifest.address.label_he}")
    _title_slide(
        prs,
        manifest.address.label_he,
        f"ארכיון הנדסה · תיק {manifest.archive_folder_no or '—'} · {len(docs)} מסמכים",
    )
    _index_slides(prs, "מסמכים", [f"{d.doc_id} · {d.doc_type or d.title} · {_doc_date(d)}" for d in docs])
    for doc in docs:
        slide = _blank(prs)
        _text(
            slide,
            Inches(0.6),
            Inches(0.35),
            Inches(12.1),
            Inches(0.9),
            [(f"{doc.doc_id} · {doc.doc_type or doc.title}", 24, True)],
        )
        details = [(f"תאריך: {_doc_date(doc)}", 16, False), (f"עמודים: {doc.pages}", 16, False)]
        if doc.permit_no:
            details.append((f"מספר היתר: {doc.permit_no}", 16, False))
        if doc.request_no:
            details.append((f"מספר בקשה: {doc.request_no}", 16, False))
        _text(slide, Inches(7.6), Inches(1.4), Inches(5.1), Inches(4.5), details, link=doc.original_url)
        if doc.page_images:
            _picture(slide, root / doc.page_images[0], Inches(0.6), Inches(1.3), Inches(6.6), Inches(5.9))
    prs.save(str(dest))


def build_maps_plans_deck(manifest: ScanManifest, root: Path, dest: Path) -> None:
    maps = [d for d in manifest.documents if d.source == "map"]
    plan_docs = [d for d in manifest.documents if d.source == "plan"]
    prs = _new_deck(f'מפות ותב"ע — {manifest.address.label_he}')
    _title_slide(
        prs, manifest.address.label_he, f"{len(maps)} מפות היסטוריות · {len(manifest.plans)} תוכניות בניין עיר"
    )

    plan_lines = []
    for plan in manifest.plans:
        when = plan.in_force_date.strftime("%d.%m.%Y") if plan.in_force_date else "—"
        plan_lines.append(f'תב"ע {plan.plan_no} · {plan.name or ""} · {plan.status or ""} · {when}')
    if plan_lines:
        _index_slides(prs, "תוכניות בניין עיר", plan_lines)
    if maps:
        _index_slides(prs, "מפות היסטוריות", [f"{d.doc_id} · {d.year or '—'} · {d.title}" for d in maps])

    for doc in maps:
        slide = _blank(prs)
        _text(
            slide,
            Inches(0.6),
            Inches(0.3),
            Inches(12.1),
            Inches(0.8),
            [(f"{doc.doc_id} · {doc.year or 'שנה לא ידועה'} · {doc.title}", 22, True)],
        )
        if doc.page_images:
            _picture(slide, root / doc.page_images[0], Inches(0.6), Inches(1.15), Inches(12.1), Inches(5.9))
        _text(
            slide,
            Inches(0.6),
            Inches(7.05),
            Inches(12.1),
            Inches(0.4),
            [("מקור: MapJunction · עיגול אדום = המבנה", 11, False)],
            color=MUTED,
        )

    for plan in manifest.plans:
        files = [d for d in plan_docs if d.plan_no == plan.plan_no]
        slide = _blank(prs)
        _text(slide, Inches(0.6), Inches(0.35), Inches(12.1), Inches(0.9), [(f'תב"ע {plan.plan_no}', 26, True)])
        lines = [(plan.name or "", 16, False), (f"סטטוס: {plan.status or '—'}", 15, False)]
        if plan.in_force_date:
            lines.append((f"מתן תוקף: {plan.in_force_date.strftime('%d.%m.%Y')}", 15, False))
        lines.append((f"קבצים: {', '.join(d.doc_id for d in files) or '—'}", 15, False))
        _text(slide, Inches(7.6), Inches(1.4), Inches(5.1), Inches(5.0), lines, link=plan.documents_url)
        preview = next((d for d in files if d.page_images), None)
        if preview:
            _picture(slide, root / preview.page_images[0], Inches(0.6), Inches(1.3), Inches(6.6), Inches(5.9))
    prs.save(str(dest))
