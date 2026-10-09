from django.db import transaction
from django.utils import timezone

from .models import UserCourseAccess


@transaction.atomic
def reconcile_course_grants(user, course_ids=None, granted_by=None):
    """Keep grant audit rows, but revoke anything outside the user's current track."""
    grants = UserCourseAccess.objects.filter(user=user)
    if user.role != 'STUDENT' or user.learning_track_id is None:
        grants.update(is_active=False)
        return
    grants.exclude(course__learning_track_id=user.learning_track_id).update(is_active=False)
    if course_ids is None:
        return
    grants.exclude(course_id__in=course_ids).update(is_active=False)
    for course_id in course_ids:
        grant, created = UserCourseAccess.objects.get_or_create(user=user, course_id=course_id,
            defaults={'granted_by': granted_by})
        if not created and not grant.is_active:
            grant.is_active = True
            grant.granted_by = granted_by
            grant.granted_at = timezone.now()
            grant.save(update_fields=['is_active', 'granted_by', 'granted_at'])
