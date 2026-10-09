from common.permissions import is_admin
from .adaptive import module_is_locked
from .models import Course


def accessible_courses(user):
    courses = Course.objects.select_related('learning_track')
    if is_admin(user):
        return courses.all()
    if not user.is_authenticated or not user.is_active or user.role != 'STUDENT' or not user.learning_track_id:
        return courses.none()
    courses = courses.filter(is_published=True, learning_track_id=user.learning_track_id,
        learning_track__is_published=True, learning_track__is_active=True)
    if user.course_access_mode == 'CUSTOM':
        from apps.accounts.models import UserCourseAccess
        courses = courses.filter(pk__in=UserCourseAccess.objects.filter(user=user, is_active=True).values('course_id'))
    return courses


def can_access_course(user, course):
    return is_admin(user) or accessible_courses(user).filter(pk=course.pk).exists()

def can_access_lesson(user, lesson):
    if is_admin(user):
        return True
    course = lesson.module.course
    return bool(lesson.status == 'PUBLISHED' and lesson.module.is_published and can_access_course(user, course)
                and not module_is_locked(user, lesson.module))


def can_access_item(user, item):
    if is_admin(user):
        return True
    course = item.module.course
    return bool(item.status == 'PUBLISHED' and item.module.is_published and can_access_course(user, course)
                and not module_is_locked(user, item.module))


def can_access_test(user, test):
    item = test.learning_item if hasattr(test, 'learning_item') else None
    if item:
        return can_access_item(user, item)
    return bool(test.lesson_id and can_access_lesson(user, test.lesson))
