from django.db import transaction
from rest_framework.exceptions import ValidationError, PermissionDenied
from .models import Test, Question, Option, TestAttempt

def validate_test(data):
    if not isinstance(data, dict):
        raise ValidationError({'test': 'Ожидается объект'})
    title = str(data.get('title', '')).strip()
    if not title or len(title) > 200:
        raise ValidationError({'title': 'Укажите название до 200 символов'})
    try:
        passing = int(data.get('passing_percent', 70))
    except (TypeError, ValueError):
        raise ValidationError({'passing_percent': 'Ожидается число'})
    if passing < 0 or passing > 100:
        raise ValidationError({'passing_percent': 'Значение от 0 до 100'})
    limit = data.get('max_attempts')
    if limit in ('', None):
        limit = None
    else:
        try: limit = int(limit)
        except (TypeError, ValueError): raise ValidationError({'max_attempts': 'Ожидается число'})
        if limit < 1: raise ValidationError({'max_attempts': 'Минимум одна попытка'})
    questions = data.get('questions', [])
    if not isinstance(questions, list) or len(questions) > 100:
        raise ValidationError({'questions': 'Ожидается список до 100 вопросов'})
    if data.get('is_published') and not questions:
        raise ValidationError({'questions': 'Для публикации нужен хотя бы один вопрос'})
    for qi, q in enumerate(questions):
        if not isinstance(q, dict) or not str(q.get('text', '')).strip() or q.get('position') != qi:
            raise ValidationError({'questions': 'Проверьте текст и порядок вопросов'})
        competency = q.get('competency', 'general')
        if not isinstance(competency, str) or not competency.strip() or len(competency.strip()) > 100:
            raise ValidationError({'competency': 'Укажите компетенцию длиной до 100 символов'})
        options = q.get('options', [])
        if not isinstance(options, list) or len(options) < 2 or len(options) > 20:
            raise ValidationError({'options': 'Требуется от 2 до 20 вариантов'})
        if sum(o.get('is_correct') is True for o in options if isinstance(o, dict)) != 1:
            raise ValidationError({'options': 'Ровно один вариант должен быть правильным'})
        try: points = int(q.get('points', 1))
        except (TypeError, ValueError): raise ValidationError({'points': 'Ожидается число'})
        if points < 1 or points > 100: raise ValidationError({'points': 'Баллы от 1 до 100'})
        for oi, o in enumerate(options):
            if not isinstance(o, dict) or not str(o.get('text', '')).strip() or o.get('position') != oi:
                raise ValidationError({'options': 'Проверьте текст и порядок вариантов'})
    return title, passing, limit, questions

@transaction.atomic
def save_test(lesson, data):
    test = Test.objects.select_for_update().filter(lesson=lesson).first()
    return _save_test(test or Test(lesson=lesson), data)


@transaction.atomic
def save_item_test(item, data):
    if item.type != 'TEST' or not item.test_id:
        raise ValidationError({'type': 'Это не тест'})
    test = _save_test(Test.objects.select_for_update().get(pk=item.test_id), data)
    item.title = test.title
    item.description = test.description
    item.status = 'PUBLISHED' if test.is_published else 'DRAFT'
    item.save(update_fields=['title', 'description', 'status', 'updated_at'])
    return test


def _save_test(test, data):
    title, passing, limit, questions = validate_test(data)
    if test.pk:
        test.version += 1
    test.title = title
    test.description = str(data.get('description', ''))
    test.passing_percent = passing
    test.max_attempts = limit
    test.is_published = data.get('is_published') is True
    test.save()
    test.questions.all().delete()
    for q in questions:
        question = Question.objects.create(test=test, text=q['text'], position=q['position'], points=int(q.get('points', 1)), competency=q.get('competency', 'general').strip())
        Option.objects.bulk_create([Option(question=question, text=o['text'], position=o['position'],
            is_correct=o['is_correct']) for o in q['options']])
    if hasattr(test, 'learning_item'):
        item = test.learning_item
        item.title = test.title
        item.description = test.description
        item.status = 'PUBLISHED' if test.is_published else 'DRAFT'
        item.save(update_fields=['title', 'description', 'status', 'updated_at'])
    return test

@transaction.atomic
def submit_attempt(test_id, user, answers):
    from apps.accounts.models import User
    User.objects.select_for_update().get(pk=user.pk)
    test = Test.objects.select_for_update(of=('self',)).get(pk=test_id)
    from apps.education.access import can_access_test
    if not test.is_published or not can_access_test(user, test):
        raise PermissionDenied()
    learning_item = test.learning_item if hasattr(test, 'learning_item') else None
    adaptive = bool(learning_item and learning_item.module.course.adaptive_learning_enabled)
    practice = None
    if adaptive:
        from apps.education.adaptive import prepare_module_attempt
        practice = prepare_module_attempt(user, learning_item)
    if test.max_attempts is not None and TestAttempt.objects.filter(test=test, user=user).count() >= test.max_attempts:
        raise ValidationError({'attempts': 'Лимит попыток исчерпан'})
    questions = list(test.questions.prefetch_related('options'))
    if adaptive and not questions:
        raise ValidationError({'detail': 'В итоговом тесте ещё нет вопросов.'})
    if not isinstance(answers, list) or len(answers) != len(questions):
        raise ValidationError({'answers': 'Ответьте на все вопросы'})
    selected = {}
    for item in answers:
        if not isinstance(item, dict) or not isinstance(item.get('question'), int) or not isinstance(item.get('option'), int):
            raise ValidationError({'answers': 'Некорректные ответы'})
        if item['question'] in selected:
            raise ValidationError({'answers': 'Повторяющийся вопрос'})
        selected[item['question']] = item['option']
    if set(selected) != {q.id for q in questions}:
        raise ValidationError({'answers': 'Ответы не соответствуют тесту'})
    earned = total = 0
    snapshot = []
    question_results = []
    for q in questions:
        options = list(q.options.all())
        choice = next((o for o in options if o.id == selected[q.id]), None)
        if choice is None:
            raise ValidationError({'answers': 'Вариант не принадлежит вопросу'})
        question_results.append((q, choice.is_correct))
        total += q.points
        earned += q.points if choice.is_correct else 0
        snapshot.append({'question': q.text, 'selected': choice.text, 'correct': next(o.text for o in options if o.is_correct), 'points': q.points, 'competency': q.competency})
    percent = round(100 * earned / total, 2) if total else 0
    adaptive_result = None
    if adaptive:
        from apps.education.adaptive import evaluate_module_attempt
        adaptive_result = evaluate_module_attempt(user, learning_item, question_results, practice)
    attempt = TestAttempt.objects.create(test=test, user=user, test_version=test.version,
        answers=answers, snapshot=snapshot, earned_points=earned, total_points=total,
        percent=percent, passed=percent >= test.passing_percent, adaptive_result=adaptive_result)
    if learning_item and (adaptive_result['module_completed'] if adaptive else attempt.passed):
        from apps.education.progress import complete_item_from_server
        complete_item_from_server(user, test.learning_item)
    return attempt
