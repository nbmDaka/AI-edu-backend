from pathlib import Path
from zipfile import BadZipFile, ZipFile
from django.conf import settings
from rest_framework.exceptions import ValidationError


def validate_upload(upload):
    if not upload or not upload.size:
        raise ValidationError({'file': 'Выберите непустой файл PDF, DOCX или PPTX.'})
    if upload.size > settings.LECTURE_IMPORT_MAX_BYTES:
        raise ValidationError({'file': 'Максимальный размер файла — 10 МБ.'})
    suffix = Path(upload.name).suffix.lower()
    if suffix not in {'.pdf', '.docx', '.pptx'}:
        raise ValidationError({'file': 'Поддерживаются PDF, DOCX и PPTX. Сохраните старый DOC/PPT в новом формате.'})
    try:
        if suffix == '.pdf':
            if not upload.read(1024).lstrip().startswith(b'%PDF-'):
                raise ValueError()
        else:
            with ZipFile(upload) as archive:
                entries = archive.infolist()
                if len(entries) > 10000 or sum(item.file_size for item in entries) > 200 * 1024 * 1024:
                    raise ValidationError({'file': 'Распакованный документ слишком большой.'})
                required = 'word/document.xml' if suffix == '.docx' else 'ppt/presentation.xml'
                if required not in archive.namelist() or '[Content_Types].xml' not in archive.namelist():
                    raise ValueError()
                if any('vbaproject' in item.filename.lower() for item in entries):
                    raise ValidationError({'file': 'Сохраните документ без макросов.'})
    except (ValueError, BadZipFile, OSError):
        raise ValidationError({'file': 'Содержимое файла не соответствует формату или документ повреждён.'})
    finally:
        upload.seek(0)
