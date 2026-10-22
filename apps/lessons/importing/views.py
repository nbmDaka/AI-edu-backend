from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.http import FileResponse
from rest_framework.exceptions import NotFound
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from common.permissions import IsPlatformAdmin
from apps.education.models import Lesson, LearningItem
from apps.lessons.models import LectureImportJob
from .validation import validate_upload
from .serializers import ImportJobSerializer, ImportListSerializer, ImportUploadSerializer

ERRORS = {
    'timeout': 'Обработка заняла слишком много времени. Разделите документ на несколько файлов.',
    'dependencies_missing': 'На сервере не установлены библиотеки импорта. Запустите import-worker по инструкции в README.',
    'ocr_unavailable': 'На сервере недоступен Tesseract или языковые пакеты rus, eng, kaz.',
    'invalid_document': 'Не удалось прочитать документ. Проверьте, что он открывается и не защищён паролем.',
    'document_limit': 'Документ слишком большой или сложный. Разделите его на несколько файлов.',
    'empty_document': 'В документе не удалось распознать содержимое.',
    'worker_interrupted': 'Обработка прервалась. Можно повторить импорт.',
    'processing_failed': 'Не удалось обработать документ. Можно повторить импорт.',
}


def job_data(job, include_result=True):
    root = (f'/api/v1/items/{job.practice_item.short_id}/practice/imports/{job.pk}/' if job.practice_item_id
            else f'/api/v1/lessons/{job.lesson.short_id}/imports/{job.pk}/')
    return {'id': str(job.pk), 'name': job.original_name, 'size': job.size,
            'status': job.status, 'stage': job.stage, 'created_at': job.created_at,
            'source_url': root + 'source/', 'error': ERRORS.get(job.error_code, ''),
            'result': job.result if include_result and job.status == 'SUCCEEDED' else None}


def import_target(short_id, practice=False, lock=False):
    query = LearningItem.objects.filter(type='PRACTICE') if practice else Lesson.objects.all()
    target = (query.select_for_update() if lock else query).filter(short_id=short_id).first()
    if not target:
        raise NotFound()
    return target


class ImportListView(APIView):
    permission_classes = [IsPlatformAdmin]
    parser_classes = [MultiPartParser, FormParser]
    practice = False

    def jobs(self, target):
        return target.instruction_imports if self.practice else target.imports

    @extend_schema(operation_id='lecture_imports_list', responses=ImportListSerializer)
    def get(self, request, short_id):
        target = import_target(short_id, self.practice)
        jobs = self.jobs(target).select_related('lesson', 'practice_item').defer('result').all()[:5]
        return Response({'jobs': [job_data(job, include_result=False) for job in jobs], 'ai_available': bool(settings.AI_BASE_URL and settings.AI_MODEL)})

    @extend_schema(operation_id='lecture_import_create', request=ImportUploadSerializer, responses={202: ImportJobSerializer})
    def post(self, request, short_id):
        upload = request.FILES.get('file')
        validate_upload(upload)
        with transaction.atomic():
            target = import_target(short_id, self.practice, lock=True)
            active = self.jobs(target).filter(status__in=['PENDING', 'RUNNING']).first()
            if active:
                return Response({'detail': 'Импорт этого материала уже выполняется.', 'job': job_data(active)}, status=409)
            destination = {'practice_item': target} if self.practice else {'lesson': target}
            job = LectureImportJob(**destination, created_by=request.user, size=upload.size,
                                   original_name=Path(upload.name.replace('\\', '/')).name[:255])
            job.source.save(job.original_name, upload, save=False)
            try:
                job.save()
            except Exception:
                job.source.delete(save=False)
                raise
        return Response(job_data(job), status=202)


class ImportDetailView(APIView):
    permission_classes = [IsPlatformAdmin]
    practice = False

    def get_job(self, short_id, job_id):
        # Resolve the live target first: imports of deleted items must stay inaccessible.
        import_target(short_id, self.practice)
        lookup = {'practice_item__short_id': short_id} if self.practice else {'lesson__short_id': short_id}
        job = LectureImportJob.objects.select_related('lesson', 'practice_item').filter(pk=job_id, **lookup).first()
        if not job:
            raise NotFound()
        return job

    @extend_schema(operation_id='lecture_import_retrieve', responses=ImportJobSerializer)
    def get(self, request, short_id, job_id):
        return Response(job_data(self.get_job(short_id, job_id)))


class ImportSourceView(ImportDetailView):
    @extend_schema(operation_id='lecture_import_source', responses={(200, 'application/pdf'): OpenApiTypes.BINARY, (200, 'application/octet-stream'): OpenApiTypes.BINARY})
    def get(self, request, short_id, job_id):
        job = self.get_job(short_id, job_id)
        is_pdf = job.original_name.lower().endswith('.pdf')
        response = FileResponse(job.source.open('rb'), as_attachment=not is_pdf,
                                filename=job.original_name, content_type='application/pdf' if is_pdf else 'application/octet-stream')
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PracticeImportListView(ImportListView):
    practice = True

    @extend_schema(operation_id='practice_instruction_imports_list', responses=ImportListSerializer)
    def get(self, request, short_id):
        return super().get(request, short_id)

    @extend_schema(operation_id='practice_instruction_import_create', request=ImportUploadSerializer, responses={202: ImportJobSerializer})
    def post(self, request, short_id):
        return super().post(request, short_id)


class PracticeImportDetailView(ImportDetailView):
    practice = True

    @extend_schema(operation_id='practice_instruction_import_retrieve', responses=ImportJobSerializer)
    def get(self, request, short_id, job_id):
        return super().get(request, short_id, job_id)


class PracticeImportSourceView(ImportSourceView):
    practice = True

    @extend_schema(operation_id='practice_instruction_import_source', responses={(200, 'application/octet-stream'): OpenApiTypes.BINARY})
    def get(self, request, short_id, job_id):
        return super().get(request, short_id, job_id)
