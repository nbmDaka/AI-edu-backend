from django.db import transaction
from rest_framework.exceptions import ValidationError
from .models import LearningTrack, Course, Module, Lesson, LearningItem


def set_item_positions(items):
    # Move to unused positions first to avoid the per-module unique constraint.
    if not items or all(item.position == index for index, item in enumerate(items)):
        return
    offset = max(item.position for item in items) + len(items) + 1
    for index, item in enumerate(items):
        LearningItem.objects.filter(pk=item.pk).update(position=offset + index)
    for position, item in enumerate(items):
        LearningItem.objects.filter(pk=item.pk).update(position=position)
        if item.lesson_id:
            Lesson.objects.filter(pk=item.lesson_id).update(position=position)
        item.position = position


def order_module_items(module_id):
    items = list(LearningItem.objects.filter(module_id=module_id).order_by('position', 'id'))
    items.sort(key=lambda item: item.type == LearningItem.Type.TEST)
    set_item_positions(items)


@transaction.atomic
def delete_item(item):
    from apps.assessments.models import Test, TestAttempt
    from apps.activities.models import PracticeDefinition
    from apps.lessons.models import LessonBlock
    Module.objects.select_for_update().get(pk=item.module_id)
    if item.type == LearningItem.Type.TEST and item.test_id:
        if TestAttempt.objects.filter(test_id=item.test_id).exists():
            raise ValidationError({'detail': 'Тест содержит результаты студентов и не может быть удалён'})
        test = item.test
        item.delete()
        test.delete()
    elif item.type == LearningItem.Type.PRACTICE and item.practice_id:
        practice = item.practice
        item.delete()
        practice.delete()
    else:
        lesson = item.lesson
        if lesson and TestAttempt.objects.filter(test__lesson=lesson, test__learning_item__isnull=True).exists():
            raise ValidationError({'detail': 'Лекция содержит результаты старого теста без отдельного элемента'})
        item.delete()
        if lesson:
            Test.objects.filter(lesson=lesson).update(lesson=None)
            PracticeDefinition.objects.filter(lesson=lesson).update(lesson=None)
            LessonBlock.objects.filter(lesson=lesson).delete()
            lesson.delete()
    order_module_items(item.module_id)

@transaction.atomic
def delete_content(instance):
    from apps.assessments.models import TestAttempt, Test
    from apps.lessons.models import LessonBlock
    from apps.activities.models import PracticeDefinition
    if isinstance(instance, Lesson):
        lessons = [instance]
        items = LearningItem.objects.filter(lesson=instance)
    elif isinstance(instance, Module):
        lessons = list(instance.lessons.all())
        items = LearningItem.objects.filter(module=instance)
    elif isinstance(instance, Course):
        lessons = list(Lesson.objects.filter(module__course=instance))
        items = LearningItem.objects.filter(module__course=instance)
    elif isinstance(instance, LearningTrack):
        lessons = list(Lesson.objects.filter(module__course__learning_track=instance))
        items = LearningItem.objects.filter(module__course__learning_track=instance)
    else:
        raise TypeError('Unsupported content type')
    ids = [lesson.pk for lesson in lessons]
    if (TestAttempt.objects.filter(test__lesson_id__in=ids).exists()
            or TestAttempt.objects.filter(test__learning_item__in=items).exists()):
        raise ValidationError({'detail': 'Материал содержит результаты студентов и не может быть удалён'})
    for item in list(items.order_by('type', 'id')):
        if LearningItem.objects.filter(pk=item.pk).exists():
            delete_item(item)
    LessonBlock.objects.filter(lesson_id__in=ids).delete()
    PracticeDefinition.objects.filter(lesson_id__in=ids).delete()
    Test.objects.filter(lesson_id__in=ids).delete()
    Lesson.objects.filter(pk__in=ids).delete()
    if isinstance(instance, (Course, LearningTrack)):
        modules = Module.objects.filter(course=instance) if isinstance(instance, Course) else Module.objects.filter(course__learning_track=instance)
        modules.delete()
    if isinstance(instance, LearningTrack):
        Course.objects.filter(learning_track=instance).delete()
    if not isinstance(instance, Lesson):
        instance.delete()
