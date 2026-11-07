"""Экспорт учебных материалов (ТЗ, п. 36): DOCX, PDF, PPTX, TXT и Markdown.

Материал хранится в Markdown. Он разбирается в список блоков (заголовок, абзац, список,
таблица, цитата, код), из которого собираются файлы всех форматов. PDF формируется на сервере
(reportlab) со шрифтом DejaVu Sans, поэтому кириллица и казахские буквы отображаются правильно.
Для PPTX каждый раздел (или «Слайд N» из работы № 5) становится отдельным слайдом.
"""
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

NOTICE = {
    'ru': 'Материал создан с использованием искусственного интеллекта и проверен преподавателем. '
          'Пользователь несёт ответственность за научную и педагогическую корректность итогового материала.',
    'kz': 'Материал жасанды интеллект көмегімен жасалып, оқытушы тексерген. '
          'Қорытынды материалдың ғылыми және педагогикалық дұрыстығына пайдаланушы жауап береді.',
    'en': 'This material was created with the help of artificial intelligence and reviewed by the teacher. '
          'The user is responsible for the scientific and pedagogical accuracy of the final material.',
}
FONT_DIR = Path(__file__).resolve().parent.parent / 'fonts'
FORMATS = {
    'docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'docx'),
    'pdf': ('application/pdf', 'pdf'),
    'pptx': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', 'pptx'),
    'txt': ('text/plain; charset=utf-8', 'txt'),
    'md': ('text/markdown; charset=utf-8', 'md'),
}


@dataclass
class Block:
    kind: str                 # heading | para | ul | ol | table | quote | code | hr
    text: str = ''
    level: int = 0
    items: list = field(default_factory=list)


INLINE_RE = re.compile(r'(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_|\[[^\]]+\]\([^)]+\))')


def inline_runs(text):
    """Строка Markdown → [(текст, жирный, курсив, код)]."""
    out, last = [], 0
    for m in INLINE_RE.finditer(text):
        if m.start() > last:
            out.append((text[last:m.start()], False, False, False))
        x = m.group(0)
        if x.startswith('**'):
            out.append((x[2:-2], True, False, False))
        elif x.startswith('`'):
            out.append((x[1:-1], False, False, True))
        elif x.startswith('['):
            label, url = re.match(r'\[([^\]]+)\]\(([^)]+)\)', x).groups()
            out.append((f'{label} ({url})', False, False, False))
        else:
            out.append((x[1:-1], False, True, False))
        last = m.end()
    if last < len(text):
        out.append((text[last:], False, False, False))
    return out or [('', False, False, False)]


def plain(text):
    return ''.join(r[0] for r in inline_runs(text))


def parse(markdown):
    lines = str(markdown or '').replace('\r\n', '\n').split('\n')
    blocks, i = [], 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith('```'):
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code.append(lines[i])
                i += 1
            blocks.append(Block('code', '\n'.join(code)))
            i += 1
            continue
        m = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        if m:
            blocks.append(Block('heading', m.group(2).strip(), len(m.group(1))))
            i += 1
            continue
        if re.match(r'^(-{3,}|\*{3,}|_{3,})$', stripped):
            blocks.append(Block('hr'))
            i += 1
            continue
        if stripped.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.match(r'^:?-{2,}:?$', c) for c in cells if c):
                    rows.append(cells)
                i += 1
            if rows:
                width = max(len(r) for r in rows)
                blocks.append(Block('table', items=[r + [''] * (width - len(r)) for r in rows]))
            continue
        if re.match(r'^[-*•]\s+', stripped):
            items = []
            while i < len(lines) and re.match(r'^\s*[-*•]\s+', lines[i]):
                items.append(re.sub(r'^\s*[-*•]\s+', '', lines[i]).strip())
                i += 1
            blocks.append(Block('ul', items=items))
            continue
        if re.match(r'^\d+[.)]\s+', stripped):
            items = []
            while i < len(lines) and re.match(r'^\s*\d+[.)]\s+', lines[i]):
                items.append(re.sub(r'^\s*\d+[.)]\s+', '', lines[i]).strip())
                i += 1
            blocks.append(Block('ol', items=items))
            continue
        if stripped.startswith('>'):
            quote = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                quote.append(lines[i].strip().lstrip('>').strip())
                i += 1
            blocks.append(Block('quote', ' '.join(quote)))
            continue
        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r'^(#{1,6}\s|[-*•]\s|\d+[.)]\s|\||>|```)', lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        blocks.append(Block('para', ' '.join(para)))
    return blocks


def filename(title, ext):
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', str(title or 'material')).strip()[:80] or 'material'
    return f'{base}.{ext}'


# ---------- TXT / Markdown ----------
def to_txt(title, markdown, meta='', lang='ru'):
    lines = [title, '=' * min(len(title), 80)]
    if meta:
        lines += [meta]
    lines.append('')
    for b in parse(markdown):
        if b.kind == 'heading':
            lines += ['', plain(b.text).upper() if b.level <= 2 else plain(b.text), '']
        elif b.kind == 'para' or b.kind == 'quote':
            lines += [plain(b.text), '']
        elif b.kind == 'ul':
            lines += [f'• {plain(x)}' for x in b.items] + ['']
        elif b.kind == 'ol':
            lines += [f'{n}. {plain(x)}' for n, x in enumerate(b.items, 1)] + ['']
        elif b.kind == 'table':
            lines += [' | '.join(plain(c) for c in row) for row in b.items] + ['']
        elif b.kind == 'code':
            lines += [b.text, '']
    lines += ['', NOTICE.get(lang, NOTICE['ru'])]
    return '\n'.join(lines).encode('utf-8')


def to_md(title, markdown, meta='', lang='ru'):
    head = f'# {title}\n\n' + (f'_{meta}_\n\n' if meta else '')
    return (head + str(markdown or '').strip() + f'\n\n---\n_{NOTICE.get(lang, NOTICE["ru"])}_\n').encode('utf-8')


# ---------- DOCX ----------
def to_docx(title, markdown, meta='', lang='ru'):
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, Cm

    doc = docx.Document()
    section = doc.sections[0]
    section.left_margin, section.right_margin = Cm(3), Cm(1.5)
    section.top_margin = section.bottom_margin = Cm(2)
    normal = doc.styles['Normal']
    normal.font.name = 'Times New Roman'
    normal.font.size = Pt(14)
    normal.paragraph_format.line_spacing = 1.5

    def add_runs(paragraph, text, bold=False):
        for chunk, b, it, code in inline_runs(text):
            run = paragraph.add_run(chunk)
            run.bold = bold or b
            run.italic = it
            if code:
                run.font.name = 'Consolas'

    heading = doc.add_heading(level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    heading.add_run(title)
    if meta:
        p = doc.add_paragraph()
        r = p.add_run(meta)
        r.italic = True
        r.font.size = Pt(11)
    for b in parse(markdown):
        if b.kind == 'heading':
            h = doc.add_heading(level=min(max(b.level - 1, 1), 4))
            add_runs(h, b.text)
        elif b.kind == 'para':
            add_runs(doc.add_paragraph(), b.text)
        elif b.kind == 'quote':
            p = doc.add_paragraph(style='Intense Quote')
            add_runs(p, b.text)
        elif b.kind in ('ul', 'ol'):
            for item in b.items:
                add_runs(doc.add_paragraph(style='List Bullet' if b.kind == 'ul' else 'List Number'), item)
        elif b.kind == 'table':
            table = doc.add_table(rows=len(b.items), cols=len(b.items[0]))
            table.style = 'Table Grid'
            for r, row in enumerate(b.items):
                for c, cell in enumerate(row):
                    par = table.cell(r, c).paragraphs[0]
                    add_runs(par, cell, bold=(r == 0))
                    for run in par.runs:
                        run.font.size = Pt(12)
        elif b.kind == 'code':
            p = doc.add_paragraph()
            run = p.add_run(b.text)
            run.font.name = 'Consolas'
            run.font.size = Pt(11)
    p = doc.add_paragraph()
    r = p.add_run(NOTICE.get(lang, NOTICE['ru']))
    r.italic = True
    r.font.size = Pt(11)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _page_number(footer)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _page_number(paragraph):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    run = paragraph.add_run()
    for kind, text in (('begin', None), (None, 'PAGE'), ('end', None)):
        if kind:
            el = OxmlElement('w:fldChar')
            el.set(qn('w:fldCharType'), kind)
        else:
            el = OxmlElement('w:instrText')
            el.set(qn('xml:space'), 'preserve')
            el.text = text
        run._r.append(el)


# ---------- PDF ----------
_fonts_ready = False


def _register_fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.fonts import addMapping
    pdfmetrics.registerFont(TTFont('DejaVu', str(FONT_DIR / 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont('DejaVu-Bold', str(FONT_DIR / 'DejaVuSans-Bold.ttf')))
    addMapping('DejaVu', 0, 0, 'DejaVu')
    addMapping('DejaVu', 1, 0, 'DejaVu-Bold')
    addMapping('DejaVu', 0, 1, 'DejaVu')
    addMapping('DejaVu', 1, 1, 'DejaVu-Bold')
    _fonts_ready = True


def _rl_inline(text):
    from xml.sax.saxutils import escape
    parts = []
    for chunk, b, it, code in inline_runs(text):
        s = escape(chunk)
        if b:
            s = f'<b>{s}</b>'
        if it:
            s = f'<i>{s}</i>'
        parts.append(s)
    return ''.join(parts)


def to_pdf(title, markdown, meta='', lang='ru'):
    _register_fonts()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (HRFlowable, ListFlowable, ListItem, Paragraph, Preformatted,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    base = ParagraphStyle('base', fontName='DejaVu', fontSize=11, leading=16)
    styles = {
        'title': ParagraphStyle('t', parent=base, fontName='DejaVu-Bold', fontSize=17, leading=22, alignment=1, spaceAfter=10),
        'meta': ParagraphStyle('m', parent=base, fontSize=9, textColor=colors.HexColor('#65728a'), alignment=1, spaceAfter=12),
        1: ParagraphStyle('h1', parent=base, fontName='DejaVu-Bold', fontSize=15, leading=20, spaceBefore=12, spaceAfter=6,
                          textColor=colors.HexColor('#293b86')),
        2: ParagraphStyle('h2', parent=base, fontName='DejaVu-Bold', fontSize=13.5, leading=18, spaceBefore=11, spaceAfter=5,
                          textColor=colors.HexColor('#293b86')),
        3: ParagraphStyle('h3', parent=base, fontName='DejaVu-Bold', fontSize=12, leading=16, spaceBefore=8, spaceAfter=4),
        'para': ParagraphStyle('p', parent=base, spaceAfter=6),
        'quote': ParagraphStyle('q', parent=base, leftIndent=14, textColor=colors.HexColor('#4058af'), spaceAfter=6),
        'code': ParagraphStyle('c', parent=base, fontSize=9, leading=12, backColor=colors.HexColor('#f4f6fa')),
        'cell': ParagraphStyle('cell', parent=base, fontSize=9.5, leading=12.5),
        'notice': ParagraphStyle('n', parent=base, fontSize=9, textColor=colors.HexColor('#65728a'), spaceBefore=14),
    }
    story = [Paragraph(_rl_inline(title), styles['title'])]
    if meta:
        story.append(Paragraph(_rl_inline(meta), styles['meta']))
    for b in parse(markdown):
        if b.kind == 'heading':
            story.append(Paragraph(_rl_inline(b.text), styles[min(max(b.level - 1, 1), 3)]))
        elif b.kind == 'para':
            story.append(Paragraph(_rl_inline(b.text), styles['para']))
        elif b.kind == 'quote':
            story.append(Paragraph(_rl_inline(b.text), styles['quote']))
        elif b.kind in ('ul', 'ol'):
            story.append(ListFlowable([ListItem(Paragraph(_rl_inline(x), styles['para'])) for x in b.items],
                                      bulletType='bullet' if b.kind == 'ul' else '1', bulletFontName='DejaVu',
                                      bulletFontSize=9, leftIndent=14))
        elif b.kind == 'table':
            data = [[Paragraph(_rl_inline(c), styles['cell']) for c in row] for row in b.items]
            width = (A4[0] - 4.5 * cm) / max(1, len(b.items[0]))
            table = Table(data, colWidths=[width] * len(b.items[0]), repeatRows=1)
            table.setStyle(TableStyle([
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cfd8e9')),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8edff')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            story += [table, Spacer(1, 6)]
        elif b.kind == 'code':
            story.append(Preformatted(b.text, styles['code']))
        elif b.kind == 'hr':
            story.append(HRFlowable(width='100%', color=colors.HexColor('#d9e0ea')))
    story.append(Paragraph(_rl_inline(NOTICE.get(lang, NOTICE['ru'])), styles['notice']))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('DejaVu', 8)
        canvas.setFillColor(colors.HexColor('#7b879d'))
        canvas.drawRightString(A4[0] - 1.5 * cm, 1.1 * cm, str(doc.page))
        canvas.drawString(3 * cm, 1.1 * cm, 'AI edu · Виртуальные лаборатории')
        canvas.restoreState()

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=3 * cm, rightMargin=1.5 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                      title=title, author='AI edu').build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


# ---------- PPTX ----------
SLIDE_RE = re.compile(r'^(слайд|slide|слайд №)\s*\d+', re.I)


def slides_from_markdown(title, markdown):
    """Делит материал на слайды: «Слайд N. …» из работы № 5, иначе — по заголовкам разделов.
    Возвращает (слайды, заметки к обложке). Текст вне слайдов в режиме «Слайд N» уходит в заметки обложки."""
    blocks = parse(markdown)
    headings = [b for b in blocks if b.kind == 'heading']
    marks = [h for h in headings if SLIDE_RE.match(plain(h.text))]
    slide_level = min((h.level for h in marks), default=None)
    # Уровень разделов: самый крупный заголовок, который встречается хотя бы дважды
    # (одиночный «# Лекция: …» — это название документа, оно уже на обложке).
    levels = sorted({h.level for h in headings})
    top = next((lv for lv in levels if sum(h.level == lv for h in headings) > 1), levels[0] if levels else 2)
    section_count = sum(h.level == top for h in headings)
    slides, current, cover_notes = [], None, []

    def add_content(target, b):
        if b.kind == 'heading':
            target.append(plain(b.text))
        elif b.kind in ('ul', 'ol'):
            target.extend(plain(x) for x in b.items)
        elif b.kind in ('para', 'quote'):
            text = plain(b.text)
            target.append(text if len(text) <= 220 else text[:217].rsplit(' ', 1)[0] + '…')
            if target is not cover_notes and current is not None:
                current['notes'].append(text)
        elif b.kind == 'table':
            rows = b.items[1:] or b.items
            target.extend(' — '.join(plain(c) for c in row if c) for row in rows)

    for b in blocks:
        if b.kind == 'heading':
            is_mark = bool(SLIDE_RE.match(plain(b.text)))
            if marks and is_mark:
                name = re.sub(r'^(слайд|slide)\s*№?\s*\d+\s*[.:—-]?\s*', '', plain(b.text), flags=re.I) or plain(b.text)
                current = {'title': name, 'bullets': [], 'notes': []}
                slides.append(current)
                continue
            if marks and b.level <= slide_level:
                current = None
                cover_notes.append(plain(b.text))
                continue
            if not marks and b.level < top:
                current = None
                cover_notes.append(plain(b.text))
                continue
            if not marks and b.level <= top + (0 if section_count > 3 else 1):
                current = {'title': plain(b.text), 'bullets': [], 'notes': []}
                slides.append(current)
                continue
        add_content(current['bullets'] if current is not None else cover_notes, b)
    if not slides and cover_notes:
        # Текст без разделов: один слайд с тезисами
        slides.append({'title': title, 'bullets': cover_notes, 'notes': list(cover_notes)})
        cover_notes = []
    for s in slides:
        if len(s['bullets']) > 8:
            s['notes'] += s['bullets'][8:]
            s['bullets'] = s['bullets'][:8]
    return slides, cover_notes


def to_pptx(title, markdown, meta='', lang='ru'):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    primary, ink = RGBColor(0x29, 0x3B, 0x86), RGBColor(0x1E, 0x2B, 0x44)

    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = title
    cover.shapes.title.text_frame.paragraphs[0].runs[0].font.color.rgb = primary
    cover.placeholders[1].text = meta or 'AI edu · Виртуальные лаборатории'
    slides, cover_notes = slides_from_markdown(title, markdown)
    if cover_notes:
        cover.notes_slide.notes_text_frame.text = '\n'.join(cover_notes)[:4000]

    for s in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = s['title'][:120]
        for run in slide.shapes.title.text_frame.paragraphs[0].runs:
            run.font.color.rgb = primary
            run.font.size = Pt(32)
        body = slide.placeholders[1]
        body.left, body.top, body.width, body.height = Inches(0.8), Inches(1.6), Inches(11.7), Inches(5.3)
        tf = body.text_frame
        tf.clear()
        items = s['bullets'] or ['']
        for n, text in enumerate(items):
            p = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
            p.text = text
            for run in p.runs:
                run.font.size = Pt(20 if len(items) <= 5 else 16)
                run.font.color.rgb = ink
        if s['notes']:
            slide.notes_slide.notes_text_frame.text = '\n'.join(s['notes'])[:4000]

    end = prs.slides.add_slide(prs.slide_layouts[5])
    end.shapes.title.text = {'kz': 'Назарларыңызға рахмет!', 'en': 'Thank you!'}.get(lang, 'Спасибо за внимание!')
    box = end.shapes.add_textbox(Inches(0.8), Inches(6.2), Inches(11.7), Inches(0.9))
    box.text_frame.text = NOTICE.get(lang, NOTICE['ru'])
    box.text_frame.word_wrap = True
    box.text_frame.paragraphs[0].runs[0].font.size = Pt(12)
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


RENDERERS = {'docx': to_docx, 'pdf': to_pdf, 'pptx': to_pptx, 'txt': to_txt, 'md': to_md}


def export(fmt, title, markdown, meta='', lang='ru'):
    if fmt not in RENDERERS:
        raise ValueError('Неизвестный формат')
    return RENDERERS[fmt](title, markdown, meta, lang)
