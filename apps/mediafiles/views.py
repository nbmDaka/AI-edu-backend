from io import BytesIO
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

class MediaUploadSerializer(serializers.Serializer):
    file = serializers.ImageField()

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

class MediaView(GenericAPIView):
    serializer_class = MediaUploadSerializer
    def get(self, request, pk):
        media = MediaFile.objects.filter(pk=pk).first()
        if not media:
            raise NotFound()
        from apps.lessons.models import LessonBlock
        from apps.education.models import LearningTrack, Course
        user_track_id = getattr(request.user, 'learning_track_id', None)
        from apps.education.access import can_access_lesson
        public_block = (
            any(can_access_lesson(request.user, block.lesson) for block in LessonBlock.objects.select_related('lesson__module__course__learning_track').filter(
                media=media,
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
            or Course.objects.filter(cover=media, is_published=True, learning_track__is_published=True, learning_track__is_active=True).exists()
        )
        if not (is_admin(request.user) or public_block or public_cover):
            raise PermissionDenied()
        return FileResponse(media.file.open('rb'), content_type=media.content_type)

    def delete(self, request, pk):
        if not is_admin(request.user):
            raise PermissionDenied()
        media = MediaFile.objects.filter(pk=pk).first()
        if not media:
            raise NotFound()
        from apps.lessons.models import LessonBlock
        from apps.education.models import LearningTrack, Course
        if LessonBlock.objects.filter(media=media).exists() or LearningTrack.objects.filter(cover=media).exists() or Course.objects.filter(cover=media).exists():
            raise ValidationError({'detail': 'Файл используется'})
        media.file.delete(save=False)
        media.delete()
        return Response(status=204)
