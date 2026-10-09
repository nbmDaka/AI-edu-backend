from django.db import transaction
from django.utils import timezone

from .models import LearningItem, UserLearningItemProgress


LECTURE_COMPLETION_PERCENT = 95


def complete_item_from_server(user, item):
    """Called only by a verified server-side test or future practice completion."""
    with transaction.atomic():
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
        progress, _ = UserLearningItemProgress.objects.select_for_update().get_or_create(
            user=user, learning_item=item)
        new_percent = max(progress.progress_percent, percent)
        completed = progress.is_completed or new_percent >= LECTURE_COMPLETION_PERCENT
        if new_percent != progress.progress_percent or completed != progress.is_completed:
            progress.progress_percent = new_percent
            progress.is_completed = completed
            if completed and not progress.completed_at:
                progress.completed_at = timezone.now()
            progress.save(update_fields=['progress_percent', 'is_completed', 'completed_at', 'updated_at'])
    return progress


def course_progress(user, course):
    from .adaptive import module_states
    modules = module_states(user, course)
    items = LearningItem.objects.filter(
        module__course=course, status=LearningItem.Status.PUBLISHED,
        module__is_published=True, module__course__is_published=True)
    total = items.count()
    if not total:
        return {'percent': 0, 'items': {}, 'modules': modules}
    rows = UserLearningItemProgress.objects.filter(user=user, learning_item__in=items).values(
        'learning_item__short_id', 'progress_percent', 'is_completed')
    item_progress = {row['learning_item__short_id']: {
        'progress_percent': row['progress_percent'], 'is_completed': row['is_completed']}
        for row in rows}
    return {'percent': round(sum(row['progress_percent'] for row in item_progress.values()) / total),
            'items': item_progress, 'modules': modules}
