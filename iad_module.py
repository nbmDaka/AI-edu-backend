"""Структура двух траекторий по учебным планам и модуль 1 «ИАД» для IT-траектории.

Что делает скрипт:
- Траектория для IT-специальностей: курс «Инструменты искусственного интеллекта для IT-специалистов»
  из 3 модулей. Модуль 1 «ИАД» заполняется целиком: 9 лекций, 9 практических работ на Python и итоговый
  тест (6 тем по 5 вопросов). Модули 2 «Нейронные сети» и 3 «Распознавание образов» создаются пустыми
  и скрытыми — их наполняют авторы этих модулей.
- Траектория для не IT-специальностей: курс «Применение генеративного ИИ в образовании и науке»
  из 3 скрытых пустых модулей по учебному плану.
- В обоих курсах включены адаптивное обучение и сертификат за весь курс (3 модуля, 72 часа).
  Сертификата за отдельный модуль нет. Сертификат станет доступен, когда все 3 модуля будут опубликованы.
- Лекции и практики ИАД помечены темами итогового теста, чтобы рекомендация называла, что повторить.
- Старый отдельный курс «Интеллектуальный анализ данных на Python» (если он есть) скрывается от студентов.

Файл iad_module.json должен лежать рядом. Запуск из папки бэкенда:
    .venv\\Scripts\\python.exe manage.py shell -c "exec(open('iad_module.py', encoding='utf-8').read())"
Повторный запуск ничего не дублирует: созданное пропускается, темы дополняются, порядок восстанавливается.
"""
import base64
import json

from django.core.files.base import ContentFile
from django.db import transaction

from apps.accounts.models import User
from apps.activities.services import publish_draft, save_draft
from apps.assessments.models import Option, Question, Test
from apps.education.models import Course, LearningItem, LearningTrack, Lesson, Module
from apps.lessons.models import LessonBlock
from apps.mediafiles.models import MediaFile

IT_COURSE = 'Инструменты искусственного интеллекта для IT-специалистов'
IT_MODULES = [
    'Модуль 1. Интеллектуальный анализ данных (ИАД)',
    'Модуль 2. Нейронные сети',
    'Модуль 3. Распознавание образов (биометрия)',
]
TEACHER_COURSE = 'Применение генеративного ИИ в образовании и науке'
TEACHER_MODULES = [
    'Модуль 1. Применение генеративного ИИ в преподавательской деятельности',
    'Модуль 2. Применение генеративного ИИ в исследовательской деятельности',
    'Модуль 3. Прикладные примеры использования генеративного ИИ',
]
OLD_COURSE = 'Интеллектуальный анализ данных на Python'
TEST_TITLE = 'Итоговый тест модуля «ИАД»'
PASSING_PERCENT = 60
CERTIFICATE_HOURS = 72

with open('iad_module.json', encoding='utf-8') as source:
    DATA = json.load(source)
report = []


def find_track(it):
    tracks = list(LearningTrack.objects.order_by('pk'))
    for track in tracks:
        title = track.title.casefold()
        if 'it' in title and (('не it' in title or 'не-it' in title) != it):
            return track
    return None


def ensure_course(track, title, module_titles, publish_first):
    course = Course.objects.filter(title=title).first()
    if course is None:
        course = Course.objects.create(learning_track=track, title=title, is_published=publish_first)
        report.append(f'курс «{title}»')
    changed = []
    for field, value in (('adaptive_learning_enabled', True), ('certificate_enabled', True),
                         ('certificate_module_count', len(module_titles)), ('certificate_hours', CERTIFICATE_HOURS)):
        if getattr(course, field) != value:
            setattr(course, field, value)
            changed.append(field)
    if changed:
        course.save(update_fields=changed)
    modules = []
    for position, module_title in enumerate(module_titles):
        prefix = module_title.split('.')[0] + '.'
        module = (Module.objects.filter(course=course, title=module_title).first()
                  or Module.objects.filter(course=course, title__startswith=prefix).first())
        if module is None:
            module = Module.objects.create(course=course, title=module_title, position=position,
                                           is_published=publish_first and position == 0)
            report.append(f'модуль «{module_title}»')
        modules.append(module)
    return course, modules


def merge_topics(item, topics):
    merged = list(item.competencies or [])
    seen = {topic.casefold() for topic in merged}
    for topic in topics:
        if topic.casefold() not in seen and len(merged) < 10:
            merged.append(topic)
            seen.add(topic.casefold())
    if merged != item.competencies:
        item.competencies = merged
        item.save(update_fields=['competencies'])


def upload(admin, name, content, content_type):
    media = MediaFile(original_name=name, content_type=content_type, size=len(content), uploaded_by=admin)
    media.file.save(name, ContentFile(content), save=True)
    return media


def create_lecture(module, admin, data, figures):
    lesson = Lesson.objects.create(module=module, title=data['title'], position=module.lessons.count(), status='PUBLISHED')
    for position, block in enumerate(data['blocks']):
        if block['type'] == 'IMAGE':
            key = block['figure']
            if key not in figures:
                figures[key] = upload(admin, f'iad-{key}.png', base64.b64decode(DATA['figures'][key]), 'image/png')
            LessonBlock.objects.create(lesson=lesson, type='IMAGE', position=position, content=block['caption'],
                                       media=figures[key], config={'alt': block['caption'] or data['title']})
        else:
            LessonBlock.objects.create(lesson=lesson, type='TEXT', position=position,
                                       content=json.dumps(block['doc'], ensure_ascii=False))
    return LearningItem.objects.create(module=module, type='LECTURE', title=data['title'], lesson=lesson,
                                       status='PUBLISHED', position=10000 + module.items.count())


def create_practical(module, admin, data, datasets):
    for name in data['datasets']:
        if name not in datasets:
            media = upload(admin, name, DATA['datasets'][name].encode('utf-8'), 'text/csv')
            datasets[name] = {'media': media.pk, 'name': name, 'size': media.size, 'url': f'/api/v1/media/{media.pk}/download/'}
    item = LearningItem.objects.create(module=module, type='PRACTICE', title=data['title'], status='PUBLISHED',
                                       position=10000 + module.items.count())
    save_draft(item, admin, {'practice_type': 'python_lab', 'plugin_version': '1.0.0', 'title': data['title'],
                             'description': data['description'], 'config': {
                                 'packages': data['packages'], 'datasets': [datasets[name] for name in data['datasets']],
                                 'steps': data['steps']}})
    publish_draft(item)
    return LearningItem.objects.get(pk=item.pk)


def create_test(module):
    test = Test.objects.create(title=TEST_TITLE, passing_percent=PASSING_PERCENT, is_published=True,
                               description='30 вопросов по 6 темам модуля. По результату платформа подскажет, что повторить.')
    for position, data in enumerate(DATA['test']):
        question = Question.objects.create(test=test, text=data['text'], position=position, competency=data['competency'])
        for index, text in enumerate(data['options']):
            Option.objects.create(question=question, text=text, position=index, is_correct=index == data['answer'])
    return LearningItem.objects.create(module=module, type='TEST', title=TEST_TITLE, test=test, status='PUBLISHED',
                                       position=10000 + module.items.count())


def fill_iad(module, admin):
    items = list(module.items.all())
    figures, datasets, ordered = {}, {}, []
    for key in DATA['layout']:
        if key.startswith('lec'):
            data = DATA['lectures'][key]
            item = next((entry for entry in items if entry.type == 'LECTURE' and entry.title == data['title']), None)
            if item is None:
                item = create_lecture(module, admin, data, figures)
                report.append(data['title'])
        elif key.startswith('pr'):
            data = DATA['practicals'][int(key[2:]) - 1]
            item = next((entry for entry in items if entry.type == 'PRACTICE'
                         and entry.title.startswith(f'Практическая работа {data["number"]}.')), None)
            if item is None:
                item = create_practical(module, admin, data, datasets)
                report.append(data['title'])
        else:
            data = None
            item = next((entry for entry in items if entry.type == 'TEST'), None)
            if item is None:
                item = create_test(module)
                report.append(TEST_TITLE)
        if data is not None:
            merge_topics(item, data['topics'])
        ordered.append(item)
    planned = {item.pk for item in ordered}
    extra = [entry for entry in module.items.all().order_by('position', 'id') if entry.pk not in planned]
    final = ordered + extra
    for offset, item in enumerate(final):
        LearningItem.objects.filter(pk=item.pk).update(position=20000 + offset)
    for position, item in enumerate(final):
        LearningItem.objects.filter(pk=item.pk).update(position=position)
    if not module.is_published:
        module.is_published = True
        module.save(update_fields=['is_published'])


def build():
    admin = User.objects.filter(role='ADMIN', is_active=True).order_by('pk').first()
    if admin is None:
        raise SystemExit('Сначала создайте администратора: python manage.py create_platform_admin --email ...')
    it_track, teacher_track = find_track(True), find_track(False)
    if it_track is None or teacher_track is None:
        raise SystemExit('Не нашла траектории «для IT» и «для не IT»: создайте их в админке.')
    it_course, it_modules = ensure_course(it_track, IT_COURSE, IT_MODULES, publish_first=True)
    fill_iad(it_modules[0], admin)
    ensure_course(teacher_track, TEACHER_COURSE, TEACHER_MODULES, publish_first=False)
    old = Course.objects.filter(title=OLD_COURSE, is_published=True).first()
    if old:
        old.is_published = False
        old.save(update_fields=['is_published'])
        report.append(f'старый курс «{OLD_COURSE}» скрыт от студентов')
    return it_course


with transaction.atomic():
    course = build()
if report:
    print('Сделано:\n- ' + '\n- '.join(report))
else:
    print('Всё уже на месте, ничего не добавлено. Темы и порядок материалов проверены.')
print(f'Модуль ИАД: {course.modules.order_by("position").first().items.count()} элементов, '
      f'траектория «{course.learning_track.title}».')
