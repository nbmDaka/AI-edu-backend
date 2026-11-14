from django.db import transaction
from django.utils import timezone

from .models import LearningItem, UserLearningItemProgress


LECTURE_COMPLETION_PERCENT = 95


def complete_item_from_server(user, item):
    """Called only by a verified server-side test or future practice completion."""
    with transaction.atomic():
        from apps.accounts.models import User
        User.objects.select_for_update().get(pk=user.pk)
        progress, _ = UserLearningItemProgress.objects.select_for_update().get_or_create(
            user=user, learning_item=item)
        if not progress.is_completed or progress.progress_percent != 100:
            progress.progress_percent = 100
            progress.is_completed = True
            progress.completed_at = progress.completed_at or timezone.now()
            progress.save(update_fields=['progress_percent', 'is_completed', 'completed_at', 'updated_at'])
    return progress


def save_lecture_progress(user, item, percent):
    if item.type != LearningItem.Type.LECTURE:
        raise ValueError('Lecture progress requires a lecture')
    with transaction.atomic():
        from apps.accounts.models import User
        from .events import record_event, record_unlocks
        from .adaptive import module_states
        from .models import LearningEvent, UserModuleProgress
        User.objects.select_for_update().get(pk=user.pk)
        progress, _ = UserLearningItemProgress.objects.select_for_update().get_or_create(
            user=user, learning_item=item)
        new_percent = max(progress.progress_percent, percent)
        completed = progress.is_completed or new_percent >= LECTURE_COMPLETION_PERCENT
        newly_completed = completed and not progress.is_completed
        before = module_states(user, item.module.course) if newly_completed else {}
        if new_percent != progress.progress_percent or completed != progress.is_completed:
            progress.progress_percent = new_percent
            progress.is_completed = completed
            if completed and not progress.completed_at:
                progress.completed_at = timezone.now()
            progress.save(update_fields=['progress_percent', 'is_completed', 'completed_at', 'updated_at'])
        if newly_completed:
            cycle = UserModuleProgress.objects.filter(user=user, module=item.module).values_list('attempts', flat=True).first() or 0
            record_event(user, item.module, LearningEvent.Kind.MATERIAL_COMPLETED,
                         f'lecture:{progress.pk}:{cycle}', item=item,
                         details={'progress_percent': new_percent, 'module_attempts': cycle})
            record_unlocks(user, item.module.course, before, f'lecture:{progress.pk}:{cycle}', item=item)
    return progress


def course_progress(user, course):
    from .learning import learning_summaries
    return learning_summaries(user, [course])[0]
