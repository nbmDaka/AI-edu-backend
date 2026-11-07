"""Таблицы модуля в стандартной админке Django (для разработчиков; основное управление — в интерфейсе)."""
from django.contrib import admin

from . import models


@admin.register(models.TrainingSession)
class TrainingSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'work', 'status', 'generations', 'updated_at')
    list_filter = ('status', 'work')
    raw_id_fields = ('user', 'current_generation', 'discipline')


@admin.register(models.UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'owner', 'kind', 'size', 'created_at')
    raw_id_fields = ('owner',)


@admin.register(models.AIProvider)
class AIProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'task', 'model', 'is_active')
    exclude = ('api_key',)


for model in (models.LabRole, models.Course, models.VirtualWork, models.EvaluationCriterion, models.PromptTemplate,
              models.StudyGroup, models.Assignment, models.Review, models.PortfolioItem, models.Progress,
              models.LtiPlatform, models.BackupRecord, models.AIRequest):
    admin.site.register(model)
