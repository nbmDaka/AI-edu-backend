"""Rolling user quotas, reserved under the same user lock as workspace writes."""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import Throttled
from apps.accounts.models import User
from apps.activities.models import WorkspaceOperation, WorkspaceUsage

HOURLY_LIMITS = {'generate': 5, 'edit': 10, 'docx': 10}
MAX_ACTIVE = 10


class WorkspaceLimit(Throttled):
    default_code = 'workspace_limit'


def ensure_capacity(user):
    if WorkspaceOperation.objects.filter(document__owner=user, status__in=['PENDING', 'RUNNING']).count() >= MAX_ACTIVE:
        raise WorkspaceLimit(detail='Одновременно можно выполнять не более 10 запросов. Дождитесь завершения одного из них.')


@transaction.atomic
def reserve_usage(operation, categories):
    # Lock order matches create/edit/finish; reservations also cover failed provider calls.
    User.objects.select_for_update().get(pk=operation.document.owner_id)
    now = timezone.now()
    for category in categories:
        if WorkspaceUsage.objects.filter(operation=operation, category=category).exists():
            continue
        recent = WorkspaceUsage.objects.filter(user_id=operation.document.owner_id, category=category,
                                               reserved_at__gt=now - timedelta(hours=1)).order_by('reserved_at')
        if recent.count() >= HOURLY_LIMITS[category]:
            wait = max(1, int((recent.first().reserved_at + timedelta(hours=1) - now).total_seconds()) + 1)
            label = {'generate': 'генераций лекций', 'edit': 'редактирований', 'docx': 'созданий DOCX'}[category]
            raise WorkspaceLimit(wait=wait, detail=f'Лимит {label}: {HOURLY_LIMITS[category]} в час. Попробуйте позже.')
        WorkspaceUsage.objects.create(user_id=operation.document.owner_id, operation=operation, category=category)
