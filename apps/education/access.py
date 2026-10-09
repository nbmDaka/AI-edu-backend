from common.permissions import is_admin
from .adaptive import module_is_locked

def can_access_lesson(user, lesson):
    if is_admin(user):
        return True
    course = lesson.module.course
    track = course.learning_track
    return bool(lesson.status == 'PUBLISHED' and lesson.module.is_published and course.is_published
                and track and track.is_published and track.is_active
                and user.is_authenticated and user.learning_track_id == track.pk
                and not module_is_locked(user, lesson.module))


def can_access_item(user, item):
    if is_admin(user):
        return True
    course = item.module.course
    track = course.learning_track
    return bool(item.status == 'PUBLISHED' and item.module.is_published and course.is_published
                and track and track.is_published and track.is_active
                and user.is_authenticated and user.learning_track_id == track.pk
                and not module_is_locked(user, item.module))


def can_access_test(user, test):
    item = test.learning_item if hasattr(test, 'learning_item') else None
    if item:
        return can_access_item(user, item)
    return bool(test.lesson_id and can_access_lesson(user, test.lesson))
