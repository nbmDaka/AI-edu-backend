from django.conf import settings
from django.db import models
from common.soft_delete import SoftDeleteModel

class MediaFile(SoftDeleteModel):
    file = models.FileField(upload_to='lessons/%Y/%m/')
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255)
    size = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
