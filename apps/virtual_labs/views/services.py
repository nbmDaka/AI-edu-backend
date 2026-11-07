"""API сервисов: генерация и озвучивание через AI Orchestrator, поиск литературы, файлы и экспорт."""
import time

from django.core.files.base import ContentFile
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from ..models import AIRequest, UploadedFile
from ..services import ai, exporting, literature, uploads
from .base import LabView, body, int_field, text_field

PROMPT_LIMIT = 60000


class GenerateView(LabView):
    """Генерация текста языковой моделью сервера. Если модель не подключена — 409 и интерфейс использует демо-режим."""
    lab_throttle = 'ai'

    def post(self, request):
        data = body(request)
        prompt = text_field(data, 'prompt', PROMPT_LIMIT)
        system = text_field(data, 'system', 20000)
        kind = data.get('kind') or 'generate'
        if kind not in ('generate', 'chat', 'expert'):
            raise ValidationError({'kind': 'Неизвестный тип запроса'})
        messages = data.get('messages')
        if messages is not None:
            if not isinstance(messages, list) or not 1 <= len(messages) <= 60:
                raise ValidationError({'messages': 'От 1 до 60 сообщений'})
            for m in messages:
                if (not isinstance(m, dict) or m.get('role') not in ('user', 'assistant')
                        or not isinstance(m.get('content'), str) or len(m['content']) > 20000):
                    raise ValidationError({'messages': 'Некорректное сообщение'})
            messages = [{'role': m['role'], 'content': m['content']} for m in messages]
        if not prompt and not messages:
            raise ValidationError({'prompt': 'Нужен промпт или сообщения'})
        provider = ai.active_provider('text')
        if not provider:
            return Response({'detail': 'Языковая модель не подключена', 'code': 'ai_not_configured'}, status=409)
        from .learner import templates_payload
        system = system or templates_payload().get('system', {}).get('system') or None
        log = AIRequest(user=request.user, kind=kind, provider=provider.kind, model=provider.model,
                        chars=len(prompt) + sum(len(m['content']) for m in messages or []))
        t0 = time.monotonic()
        try:
            result = ai.generate(prompt or None, messages, system)
        except ai.AIError as exc:
            log.ok, log.error = False, str(exc)[:2000]
            return Response({'detail': str(exc), 'code': 'ai_error'}, status=502)
        finally:
            log.duration_ms = int((time.monotonic() - t0) * 1000)
            log.save()
        return Response(result)


class TTSView(LabView):
    lab_throttle = 'tts'

    def post(self, request):
        data = body(request)
        text = text_field(data, 'text', 4000, required=True)
        voice = text_field(data, 'voice', 64) or None
        provider = ai.active_provider('tts')
        if not provider:
            return Response({'detail': 'Озвучивание не подключено', 'code': 'tts_not_configured'}, status=409)
        log = AIRequest(user=request.user, kind='tts', provider=provider.kind, model=provider.model, chars=len(text))
        t0 = time.monotonic()
        try:
            audio, _ = ai.synthesize(text, voice)
        except ai.AIError as exc:
            log.ok, log.error = False, str(exc)[:2000]
            return Response({'detail': str(exc), 'code': 'ai_error'}, status=502)
        finally:
            log.duration_ms = int((time.monotonic() - t0) * 1000)
            log.save()
        return HttpResponse(audio, content_type='audio/mpeg')


class LiteratureView(LabView):
    """Поиск публикаций (ТЗ, п. 16): OpenAlex, Crossref, Semantic Scholar."""
    lab_throttle = 'literature'

    def get(self, request):
        query = (request.query_params.get('q') or '').strip()
        if not 2 <= len(query) <= 300:
            raise ValidationError({'q': 'Введите запрос от 2 до 300 символов'})
        names = [s for s in (request.query_params.get('sources') or ','.join(literature.SOURCES)).split(',') if s]
        if not names or any(s not in literature.SOURCES for s in names):
            raise ValidationError({'sources': 'Источники: openalex, crossref, semantic_scholar'})
        try:
            limit = max(1, min(int(request.query_params.get('limit') or 8), 20))
        except ValueError:
            raise ValidationError({'limit': 'Ожидается число'}) from None
        t0 = time.monotonic()
        result = literature.search(query, names, limit)
        AIRequest.objects.create(user=request.user, kind='literature', provider=','.join(names), chars=len(query),
                                 ok=not result['errors'] or bool(result['results']),
                                 error='; '.join(f'{k}: {v}' for k, v in result['errors'].items())[:2000],
                                 duration_ms=int((time.monotonic() - t0) * 1000))
        return Response(result)


def file_dict(f, with_text=False):
    out = {'id': f.pk, 'name': f.original_name, 'kind': f.kind, 'size': f.size, 'contentType': f.content_type,
           'pages': f.pages, 'chars': len(f.text), 'createdAt': f.created_at,
           'url': f'/api/v1/virtual-labs/files/{f.pk}/download/'}
    if with_text:
        out['text'] = f.text
    return out


class FilesView(LabView):
    """Файловое хранилище преподавателя (ТЗ, п. 9, 34.4)."""
    parser_classes = [MultiPartParser, FormParser]

    def get_throttles(self):
        self.lab_throttle = 'upload' if self.request.method == 'POST' else 'write'
        return super().get_throttles()

    def get(self, request):
        return Response([file_dict(f) for f in UploadedFile.objects.filter(owner=request.user)])

    def post(self, request):
        upload = request.FILES.get('file')
        if not upload:
            raise ValidationError({'file': 'Выберите файл'})
        kind = request.data.get('kind') or UploadedFile.Kind.OTHER
        if kind not in UploadedFile.Kind.values:
            raise ValidationError({'kind': 'Неизвестный тип документа'})
        from ..lab_settings import conf
        if upload.size > conf('MAX_UPLOAD_BYTES'):
            raise ValidationError({'file': f'Файл больше {conf("MAX_UPLOAD_BYTES") // (1024 * 1024)} МБ.'})
        data = upload.read()
        try:
            name, ext, content_type = uploads.validate(upload.name, data)
            text, pages = uploads.extract_text(data, ext)
        except uploads.UploadRejected as exc:
            raise ValidationError({'file': str(exc)}) from None
        except Exception:
            raise ValidationError({'file': 'Не удалось прочитать документ. Проверьте, что файл не повреждён.'}) from None
        digest = uploads.sha256(data)
        existing = UploadedFile.objects.filter(owner=request.user, sha256=digest).first()
        if existing:
            return Response(file_dict(existing, with_text=True), status=200)
        f = UploadedFile(owner=request.user, kind=kind, original_name=name, content_type=content_type,
                         size=len(data), sha256=digest, text=text, pages=pages)
        f.file.save(name, ContentFile(data), save=False)
        f.save()
        return Response(file_dict(f, with_text=True), status=201)


class FileDetailView(LabView):
    def get(self, request, pk):
        return Response(file_dict(get_object_or_404(UploadedFile, pk=pk, owner=request.user), with_text=True))

    def patch(self, request, pk):
        f = get_object_or_404(UploadedFile, pk=pk, owner=request.user)
        kind = request.data.get('kind')
        if kind not in UploadedFile.Kind.values:
            raise ValidationError({'kind': 'Неизвестный тип документа'})
        f.kind = kind
        f.save(update_fields=['kind'])
        return Response(file_dict(f))

    def delete(self, request, pk):
        f = get_object_or_404(UploadedFile, pk=pk, owner=request.user)
        f.file.delete(save=False)
        f.delete()
        return Response(status=204)


class FileDownloadView(LabView):
    def get(self, request, pk):
        from ..permissions import ADMIN, TUTOR, role_of
        from .tutor import can_see_user
        f = get_object_or_404(UploadedFile, pk=pk)
        allowed = f.owner_id == request.user.pk or (
            role_of(request.user) in (TUTOR, ADMIN) and can_see_user(request.user, f.owner_id))
        if not allowed:
            raise NotFound('Файл не найден')
        resp = FileResponse(f.file.open('rb'), as_attachment=True, filename=f.original_name, content_type=f.content_type)
        resp['X-Content-Type-Options'] = 'nosniff'
        return resp


class ExportView(LabView):
    """Экспорт материала в DOCX, PDF, PPTX, TXT, MD (ТЗ, п. 36)."""
    lab_throttle = 'export'

    def post(self, request):
        data = body(request)
        fmt = data.get('format')
        if fmt not in exporting.FORMATS:
            raise ValidationError({'format': 'Форматы: docx, pdf, pptx, txt, md'})
        title = text_field(data, 'title', 300) or 'Учебный материал'
        text = text_field(data, 'text', 400000, required=True)
        meta = text_field(data, 'meta', 500)
        lang = data.get('lang') if data.get('lang') in ('ru', 'kz', 'en') else 'ru'
        try:
            content = exporting.export(fmt, title, text, meta, lang)
        except Exception:
            raise ValidationError({'detail': 'Не удалось сформировать файл'}) from None
        content_type, ext = exporting.FORMATS[fmt]
        resp = HttpResponse(content, content_type=content_type)
        from urllib.parse import quote
        name = exporting.filename(title, ext)
        resp['Content-Disposition'] = f"attachment; filename=\"material.{ext}\"; filename*=UTF-8''{quote(name)}"
        return resp
