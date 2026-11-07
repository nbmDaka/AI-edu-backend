"""Загрузка документов преподавателя (ТЗ, п. 9, 34.4) и контроль загружаемых файлов (п. 38).

Проверки: расширение из белого списка, размер, сигнатура содержимого (а не только имя файла),
для DOCX/PPTX — структура архива, защита от «zip-бомб» и вложенных макросов,
для PDF — отсутствие встроенного JavaScript. Из документа извлекается текст,
который потом подставляется в поля виртуальных работ.
"""
import hashlib
import io
import re
import unicodedata
import zipfile
from pathlib import Path

from ..lab_settings import conf

CONTENT_TYPES = {
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.pdf': 'application/pdf',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
}


class UploadRejected(Exception):
    pass


def safe_name(name):
    name = unicodedata.normalize('NFC', Path(str(name or 'file')).name)
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', '_', name).strip(' .') or 'file'
    return name[:200]


def _check_zip(data, ext):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadRejected('Файл повреждён или не является документом Office.') from None
    names = set(archive.namelist())
    if '[Content_Types].xml' not in names:
        raise UploadRejected('Файл не похож на документ Office.')
    marker = 'word/document.xml' if ext == '.docx' else 'ppt/presentation.xml'
    if marker not in names:
        raise UploadRejected('Содержимое файла не соответствует расширению.')
    if any(n.lower().endswith('vbaproject.bin') for n in names):
        raise UploadRejected('Документы с макросами не принимаются.')
    total = sum(i.file_size for i in archive.infolist())
    if total > conf('MAX_UNPACKED_BYTES') or len(names) > 5000:
        raise UploadRejected('Документ слишком большой после распаковки.')
    return archive


def validate(name, data):
    """Возвращает (безопасное имя, расширение, content-type) или бросает UploadRejected."""
    clean = safe_name(name)
    ext = Path(clean).suffix.lower()
    if ext not in conf('UPLOAD_EXTENSIONS'):
        raise UploadRejected('Поддерживаются файлы DOCX, PDF, PPTX, TXT и MD.')
    if not data:
        raise UploadRejected('Файл пустой.')
    limit = conf('MAX_UPLOAD_BYTES')
    if len(data) > limit:
        raise UploadRejected(f'Файл больше {limit // (1024 * 1024)} МБ.')
    if ext in ('.docx', '.pptx'):
        if not data.startswith(b'PK\x03\x04'):
            raise UploadRejected('Содержимое файла не соответствует расширению.')
        _check_zip(data, ext)
    elif ext == '.pdf':
        if not data.lstrip()[:5] == b'%PDF-':
            raise UploadRejected('Содержимое файла не соответствует расширению.')
        if re.search(rb'/(JavaScript|JS|Launch|EmbeddedFile)\b', data):
            raise UploadRejected('PDF со встроенными сценариями или вложениями не принимаются.')
    else:
        if b'\x00' in data[:4096]:
            raise UploadRejected('Текстовый файл содержит двоичные данные.')
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            try:
                data.decode('cp1251')
            except UnicodeDecodeError:
                raise UploadRejected('Не удалось прочитать кодировку текста. Сохраните файл в UTF-8.') from None
    return clean, ext, CONTENT_TYPES[ext]


def _pdf_text(data, limit):
    """Текст PDF через pypdf; если его нет, через pypdfium2 (он уже есть в зависимостях импорта AI edu)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return _pdfium_text(data, limit)
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise UploadRejected('PDF защищён паролем.')
    chunks, size = [], 0
    for page in reader.pages:
        chunk = page.extract_text() or ''
        chunks.append(chunk)
        size += len(chunk)
        if size > limit:
            break
    return '\n\n'.join(chunks), len(reader.pages)


def _pdfium_text(data, limit):
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as e:
        if 'password' in str(e).lower():
            raise UploadRejected('PDF защищён паролем.') from None
        raise
    try:
        chunks, size = [], 0
        for i in range(len(pdf)):
            page = pdf[i]
            textpage = page.get_textpage()
            chunk = textpage.get_text_range() or ''
            textpage.close()
            page.close()
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                break
        return '\n\n'.join(chunks), len(pdf)
    finally:
        pdf.close()


def extract_text(data, ext):
    """Извлекает текст документа. Возвращает (текст, число страниц или слайдов)."""
    limit = conf('MAX_EXTRACTED_CHARS')
    pages = None
    if ext == '.docx':
        import docx
        doc = docx.Document(io.BytesIO(data))
        lines = []
        for block in doc.element.body.iterchildren():
            tag = block.tag.rsplit('}', 1)[-1]
            if tag == 'p':
                text = ''.join(node.text or '' for node in block.iter() if node.tag.endswith('}t'))
                style = block.find('.//{*}pStyle')
                level = style.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val', '') if style is not None else ''
                if text.strip():
                    m = re.search(r'(\d)$', level) if level.lower().startswith(('heading', 'заголовок')) else None
                    lines.append(('#' * min(int(m.group(1)) + 1, 4) + ' ' if m else '') + text.strip())
            elif tag == 'tbl':
                for row in block.iter('{*}tr'):
                    cells = [''.join(t.text or '' for t in cell.iter('{*}t')).strip() for cell in row.iter('{*}tc')]
                    lines.append('| ' + ' | '.join(cells) + ' |')
        text = '\n'.join(lines)
    elif ext == '.pptx':
        from pptx import Presentation
        prs = Presentation(io.BytesIO(data))
        parts = []
        for i, slide in enumerate(prs.slides, 1):
            texts = [shape.text_frame.text.strip() for shape in slide.shapes if shape.has_text_frame and shape.text_frame.text.strip()]
            parts.append(f'## Слайд {i}\n' + '\n'.join(texts))
        pages = len(prs.slides)
        text = '\n\n'.join(parts)
    elif ext == '.pdf':
        text, pages = _pdf_text(data, limit)
    else:
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('cp1251')
    text = re.sub(r'[ \t]+\n', '\n', text.replace('\r\n', '\n'))
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text[:limit], pages


def sha256(data):
    return hashlib.sha256(data).hexdigest()
