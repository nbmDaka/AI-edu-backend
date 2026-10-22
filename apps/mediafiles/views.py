from io import BytesIO
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.http import FileResponse
from rest_framework.response import Response
from rest_framework.generics import GenericAPIView
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError, PermissionDenied
from rest_framework.parsers import MultiPartParser
from django.core.files.base import ContentFile
from common.permissions import IsPlatformAdmin, is_admin
from .models import MediaFile
from .uploads import ATTACHMENT_TYPES
from .usage import draft_media_ids, practice_source_is_used

class MediaUploadSerializer(serializers.Serializer):
    file = serializers.ImageField()


class AttachmentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

class MediaUploadView(GenericAPIView):
    serializer_class = MediaUploadSerializer
    permission_classes = [IsPlatformAdmin]
    parser_classes = [MultiPartParser]
    def post(self, request):
        upload = request.FILES.get('file')
        if not upload or upload.size == 0:
            raise ValidationError({'file': 'Файл не передан или пуст'})
        if upload.size > settings.MAX_IMAGE_BYTES:
            limit_mb = settings.MAX_IMAGE_BYTES // (1024 * 1024)
            raise ValidationError({'file': f'Размер файла не должен превышать {limit_mb} МБ'})
        raw = upload.read()
        try:
            image = Image.open(BytesIO(raw))
            image.verify()
            image = Image.open(BytesIO(raw))
            if image.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError()
            image.load()
            fmt = image.format.lower()
        except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
            raise ValidationError({'file': 'Допустимы только корректные JPEG, PNG или WebP'})
        ext = 'jpg' if fmt == 'jpeg' else fmt
        media = MediaFile.objects.create(original_name=upload.name[:255], content_type='image/' + ('jpeg' if ext == 'jpg' else ext),
            size=len(raw), uploaded_by=request.user)
        media.file.save('image.' + ext, ContentFile(raw), save=True)
        return Response({'id': media.pk, 'url': f'/api/v1/media/{media.pk}/'}, status=201)


class AttachmentUploadView(GenericAPIView):
    serializer_class = AttachmentUploadSerializer
    permission_classes = [IsPlatformAdmin]
    parser_classes = [MultiPartParser]
    allowed_types = ATTACHMENT_TYPES

    def post(self, request):
        upload = request.FILES.get('file')
        if not upload or not upload.size:
            raise ValidationError({'file': 'Выберите непустой файл'})
        if upload.size > settings.MAX_UPLOAD_BYTES:
            raise ValidationError({'file': 'Максимальный размер файла — 10 МБ'})
        name = Path(upload.name.replace('\\', '/')).name[:255]
        extension = Path(name).suffix.lower()
        if extension not in self.allowed_types:
            raise ValidationError({'file': 'Допустимы PDF, Word, PowerPoint, Excel, TXT, CSV и ZIP'})
        media = MediaFile.objects.create(original_name=name, content_type=self.allowed_types[extension],
                                         size=upload.size, uploaded_by=request.user)
        media.file.save(name, upload, save=True)
        return Response({'id': media.pk, 'media': media.pk, 'name': name, 'size': media.size,
                         'url': f'/api/v1/media/{media.pk}/download/'}, status=201)

class MediaView(GenericAPIView):
    serializer_class = MediaUploadSerializer
    def authorized_media(self, request, pk):
        media = MediaFile.objects.filter(pk=pk).first()
        if not media:
            raise NotFound()
        from apps.lessons.models import LessonBlock, LessonAttachment
        from apps.education.models import LearningTrack, Course
        user_track_id = getattr(request.user, 'learning_track_id', None)
        from apps.education.access import can_access_lesson, accessible_courses
        public_block = (
            any(can_access_lesson(request.user, block.lesson) for block in LessonBlock.objects.select_related('lesson__module__course__learning_track').filter(
                media=media,
                lesson__deleted_at__isnull=True,
                lesson__status='PUBLISHED',
                lesson__module__is_published=True,
                lesson__module__course__is_published=True,
                lesson__module__course__learning_track_id=user_track_id,
                lesson__module__course__learning_track__is_published=True,
                lesson__module__course__learning_track__is_active=True,
            ))
            if user_track_id
            else False
        )
        public_cover = (
            LearningTrack.objects.filter(cover=media, is_published=True, is_active=True).exists()
            or accessible_courses(request.user).filter(cover=media).exists()
        )
        public_attachment = (
            LessonAttachment.objects.filter(
                media=media,
                lesson__deleted_at__isnull=True,
                lesson__status='PUBLISHED',
                lesson__module__is_published=True,
                lesson__module__course__is_published=True,
                lesson__module__course__learning_track_id=user_track_id,
                lesson__module__course__learning_track__is_published=True,
                lesson__module__course__learning_track__is_active=True,
            ).exists()
            if user_track_id else False
        )
        from .usage import can_download_practice_source
        if not (is_admin(request.user) or public_block or public_cover or public_attachment
                or can_download_practice_source(request.user, media.pk)):
            raise PermissionDenied()
        return media

    def get(self, request, pk):
        media = self.authorized_media(request, pk)
        is_image = media.content_type.startswith('image/')
        return FileResponse(media.file.open('rb'), content_type=media.content_type if is_image else 'application/octet-stream',
                            as_attachment=not is_image, filename=media.original_name if not is_image else None)

    def delete(self, request, pk):
        if not is_admin(request.user):
            raise PermissionDenied()
        media = MediaFile.objects.filter(pk=pk).first()
        if not media:
            raise NotFound()
        from apps.lessons.models import LessonBlock, LessonAttachment
        from apps.education.models import LearningTrack, Course
        if (media.lecture_import_assets.exists() or LessonBlock.objects.filter(media=media).exists() or LessonAttachment.objects.filter(media=media).exists()
                or LearningTrack.all_objects.filter(cover=media).exists() or Course.all_objects.filter(cover=media).exists()
                or media.pk in draft_media_ids() or practice_source_is_used(media.pk)):
            raise ValidationError({'detail': 'Файл используется'})
        media.delete()
        return Response(status=204)


class MediaDownloadView(MediaView):
    def get(self, request, pk):
        media = self.authorized_media(request, pk)
        return FileResponse(media.file.open('rb'), as_attachment=True, filename=media.original_name,
                            content_type='application/octet-stream')
