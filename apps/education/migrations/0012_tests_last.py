from django.db import migrations


def move_tests_last(apps, schema_editor):
    Item = apps.get_model('education', 'LearningItem')
    Lesson = apps.get_model('education', 'Lesson')
    db = schema_editor.connection.alias
    module_ids = Item.objects.using(db).order_by().values_list('module_id', flat=True).distinct()
    for module_id in module_ids:
        items = list(Item.objects.using(db).filter(module_id=module_id).order_by('position', 'id'))
        items.sort(key=lambda item: item.type == 'TEST')
        if all(item.position == index for index, item in enumerate(items)):
            continue
        offset = max(item.position for item in items) + len(items) + 1
        for index, item in enumerate(items):
            Item.objects.using(db).filter(pk=item.pk).update(position=offset + index)
        for position, item in enumerate(items):
            Item.objects.using(db).filter(pk=item.pk).update(position=position)
            if item.lesson_id:
                Lesson.objects.using(db).filter(pk=item.lesson_id).update(position=position)


class Migration(migrations.Migration):
    dependencies = [('education', '0011_backfill_passed_test_progress')]
    operations = [migrations.RunPython(move_tests_last, migrations.RunPython.noop)]
