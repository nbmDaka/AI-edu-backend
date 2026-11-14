from django.contrib import admin
from django.utils import timezone
from .models import Certificate, LearningEvent


@admin.register(LearningEvent)
class LearningEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_at', 'user_id', 'kind', 'module_id', 'item_id', 'attempt_id', 'submission_id')
    list_filter = ('kind',)
    readonly_fields = tuple(field.name for field in LearningEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ('token', 'learner_name', 'course_title', 'hours', 'issued_at', 'revoked_at')
    search_fields = ('learner_name', 'course_title', 'token')
    readonly_fields = ('token', 'user', 'course', 'learner_name', 'course_title', 'track_title', 'hours', 'completed_at', 'issued_at', 'revoked_at')
    actions = ['revoke']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description='Аннулировать выбранные сертификаты')
    def revoke(self, request, queryset):
        queryset.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
