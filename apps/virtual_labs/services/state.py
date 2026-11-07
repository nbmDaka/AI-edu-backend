"""Данные преподавателя: сборка состояния для интерфейса и сохранение изменений по отдельным таблицам.

Интерфейс работает с объектом состояния (профиль, настройки, сессии работ, портфолио, события).
Сервер раскладывает его по таблицам ТЗ (п. 31) и собирает обратно. Сессия сохраняется целиком,
версии промпта и результата — по своим идентификаторам, без удаления старых строк.
Оценки экспертизы пересчитываются на сервере, а не берутся из запроса.
"""
from datetime import datetime, timezone as dt_tz

from django.db import transaction
from django.utils import timezone

from .. import catalog
from ..models import (Discipline, Evaluation, Generation, LearningEvent, PortfolioItem, Progress, Prompt,
                      Recommendation, TeacherProfile, TrainingSession, VirtualWork)
from . import expertise, prompt_engine, recommendations

SECRET_SETTINGS = {'apiKey', 'elevenKey'}
PROFILE_COLUMNS = ('fio', 'organization', 'position')
MAX_VERSIONS = 60
MAX_EVENTS = 2000
SESSION_FIELDS = {'inputs', 'prompt', 'promptVersions', 'versions', 'currentVersionId', 'startedAt', 'timeSpent', 'step',
                  'stats', 'completed', 'lastEvaluation', 'lastPromptScore'}


def ms_to_dt(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value / 1000, tz=dt_tz.utc)
    return timezone.now()


def dt_to_ms(value):
    return int(value.timestamp() * 1000) if value else 0


def _num(v, default=None):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _str(v, limit=None):
    s = '' if v is None else str(v)
    return s[:limit] if limit else s


# ---------- профиль ----------
def profile_dict(user):
    p = TeacherProfile.objects.filter(user=user).select_related('discipline').first()
    if not p:
        return None, {}
    out = dict(p.data or {})
    out.update({k: getattr(p, k) for k in PROFILE_COLUMNS})
    out['discipline'] = p.discipline.name if p.discipline else out.get('discipline', '')
    return out, dict(p.settings or {})


def discipline_for(user, name, program='', level=''):
    name = _str(name, 300).strip()
    if not name:
        return None
    d, created = Discipline.objects.get_or_create(owner=user, name=name, defaults={'program': _str(program, 300), 'level': _str(level, 200)})
    if not created and (program or level) and (d.program != program or d.level != level):
        d.program, d.level = _str(program, 300), _str(level, 200)
        d.save(update_fields=['program', 'level'])
    return d


def save_profile(user, data):
    if not isinstance(data, dict):
        raise ValueError('Ожидается объект профиля')
    p, _ = TeacherProfile.objects.get_or_create(user=user)
    for key in PROFILE_COLUMNS:
        setattr(p, key, _str(data.get(key), 300).strip())
    p.discipline = discipline_for(user, data.get('discipline'), data.get('program', ''), data.get('audience', ''))
    p.data = {k: v for k, v in data.items() if k not in PROFILE_COLUMNS and k != 'discipline'}
    p.save()
    return profile_dict(user)[0]


def save_settings(user, data):
    p, _ = TeacherProfile.objects.get_or_create(user=user)
    p.settings = {k: v for k, v in (data or {}).items() if k not in SECRET_SETTINGS}
    p.save(update_fields=['settings', 'updated_at'])
    return p.settings


# ---------- сессии ----------
def session_dict(s):
    obj = dict(s.state or {})
    gens = list(s.generations_set.select_related('evaluation').order_by('number'))
    versions = []
    for g in gens:
        item = dict(g.extra or {})
        ev = getattr(g, 'evaluation', None)
        item.update({'id': g.client_id, 'n': g.number, 'ts': dt_to_ms(g.created_at), 'kind': g.kind, 'prompt': g.prompt_text,
                     'promptScore': g.prompt_score, 'text': g.text, 'score': ev.total if ev else None,
                     'provider': g.provider or None, 'model': g.model or None, 'ms': g.duration_ms, 'error': g.error or None})
        versions.append(item)
    obj.update({
        'inputs': s.inputs or {}, 'prompt': s.current_prompt, 'versions': versions,
        'promptVersions': [{'text': p.text, 'score': p.score, 'kind': p.kind, 'ts': dt_to_ms(p.created_at)} for p in s.prompts.all()],
        'currentVersionId': s.current_generation.client_id if s.current_generation_id else (versions[-1]['id'] if versions else None),
        'startedAt': dt_to_ms(s.started_at), 'timeSpent': s.time_spent_ms, 'step': s.step,
        'stats': {'generations': s.generations, 'promptImprovements': s.prompt_improvements, 'edits': s.edits},
        'completed': s.status == TrainingSession.Status.COMPLETED,
    })
    last = next((g for g in reversed(gens) if getattr(g, 'evaluation', None)), None)
    if last and last.evaluation.criteria:
        e = last.evaluation
        obj['lastEvaluation'] = {'total': e.total, 'level': e.level, 'criteria': e.criteria, 'sections': e.sections,
                                 'flags': e.flags, 'stats': e.stats, 'engine': e.engine}
    return obj


def work_row(code):
    w = VirtualWork.objects.filter(code=code).first()
    if not w:
        meta = catalog.work(code)
        if not meta:
            raise ValueError('Неизвестная виртуальная работа')
        w = VirtualWork.objects.create(code=code, number=meta['n'], title=meta['title'], position=1000 + meta['n'])
    return w


@transaction.atomic
def save_session(user, code, data):
    """Создаёт или обновляет прохождение работы со всеми версиями. Возвращает сохранённую сессию."""
    if not isinstance(data, dict):
        raise ValueError('Ожидается объект сессии')
    work = work_row(code)
    meta = catalog.work(code) or {}
    s, _ = TrainingSession.objects.select_for_update().get_or_create(user=user, work=work)
    inputs = data.get('inputs') if isinstance(data.get('inputs'), dict) else {}
    stats = data.get('stats') if isinstance(data.get('stats'), dict) else {}
    was_completed = s.status == TrainingSession.Status.COMPLETED
    s.inputs = inputs
    s.current_prompt = _str(data.get('prompt'), 40000)
    s.step = max(0, min(int(_num(data.get('step'), 0)), 6))
    s.time_spent_ms = max(int(_num(data.get('timeSpent'), 0)), s.time_spent_ms)
    s.generations = int(_num(stats.get('generations'), 0))
    s.prompt_improvements = int(_num(stats.get('promptImprovements'), 0))
    s.edits = int(_num(stats.get('edits'), 0))
    if data.get('startedAt'):
        s.started_at = ms_to_dt(data.get('startedAt'))
    s.status = TrainingSession.Status.COMPLETED if data.get('completed') else TrainingSession.Status.IN_PROGRESS
    if s.status == TrainingSession.Status.COMPLETED and not was_completed:
        s.completed_at = timezone.now()
    s.discipline = discipline_for(user, inputs.get('discipline')) or s.discipline
    s.state = {k: v for k, v in data.items() if k not in SESSION_FIELDS}
    s.save()

    # промпты
    pvs = [p for p in (data.get('promptVersions') or []) if isinstance(p, dict)][:MAX_VERSIONS]
    existing = {p.number: p for p in s.prompts.all()}
    for n, pv in enumerate(pvs, 1):
        text = _str(pv.get('text'), 40000)
        analysis = prompt_engine.analyze(text)
        row = existing.get(n)
        if row is None:
            Prompt.objects.create(session=s, number=n, kind=_str(pv.get('kind') or 'initial', 20), text=text,
                                  score=analysis['score'], analysis=analysis, created_at=ms_to_dt(pv.get('ts')))
        elif row.text != text:
            row.text, row.score, row.analysis, row.kind = text, analysis['score'], analysis, _str(pv.get('kind') or row.kind, 20)
            row.save()
    s.prompts.filter(number__gt=len(pvs)).delete()

    # версии материала
    versions = [v for v in (data.get('versions') or []) if isinstance(v, dict) and v.get('id')][-MAX_VERSIONS:]
    rows = {g.client_id: g for g in s.generations_set.select_related('evaluation')}
    current_id = data.get('currentVersionId')
    last_eval, new_rows = None, []
    for v in versions:
        cid = _str(v['id'], 64)
        text = _str(v.get('text'), 200000)
        prompt_text = _str(v.get('prompt'), 40000)
        g = rows.get(cid)
        extra = {k: val for k, val in v.items() if k not in ('id', 'n', 'ts', 'kind', 'prompt', 'promptScore', 'text', 'score',
                                                               'provider', 'model', 'ms', 'error', 'evaluation')}
        if g is None:
            g = Generation(session=s, client_id=cid, created_at=ms_to_dt(v.get('ts')))
            new_rows.append(g)
        elif g.text == text and g.prompt_text == prompt_text:
            continue
        g.number = int(_num(v.get('n'), len(rows) + 1))
        g.kind = _str(v.get('kind') or 'generation', 20)
        g.prompt_text = prompt_text
        g.prompt_score = _num(v.get('promptScore'))
        g.text = text
        g.provider = _str(v.get('provider'), 40)
        g.model = _str(v.get('model'), 200)
        g.duration_ms = _num(v.get('ms'))
        g.error = _str(v.get('error'))
        g.extra = extra
        g.prompt = s.prompts.filter(text=prompt_text).order_by('-number').first()
        g.save()
        rows[cid] = g
        if meta.get('special') == 'expertise' and g not in new_rows:
            continue   # для ВР № 14 повторно оценивается только новая версия
        ev = expertise.evaluate_work(code, text, inputs)
        Evaluation.objects.update_or_create(generation=g, defaults={
            'total': ev['total'], 'level': ev['level'], 'criteria': ev['criteria'], 'sections': ev['sections'],
            'flags': ev['flags'], 'stats': ev['stats'], 'engine': 'server'})
        last_eval = (g, ev)
    if current_id and current_id in rows:
        s.current_generation = rows[current_id]
    elif rows:
        s.current_generation = max(rows.values(), key=lambda g: g.number)
    s.save(update_fields=['current_generation'])

    if last_eval:
        g, ev = last_eval
        Recommendation.objects.filter(generation=g).delete()
        for kind, text in recommendations.for_work(meta, s.current_prompt, ev, stats):
            Recommendation.objects.create(session=s, generation=g, kind=kind, text=text)
    update_progress(s)
    return s


def update_progress(s):
    gens = list(s.generations_set.select_related('evaluation').order_by('number'))
    scores = [g.evaluation.total for g in gens if getattr(g, 'evaluation', None)]
    current = s.current_generation
    cur_score = current.evaluation.total if current and getattr(current, 'evaluation', None) else (scores[-1] if scores else None)
    quiz = (s.state or {}).get('quiz')
    Progress.objects.update_or_create(user=s.user, work=s.work, defaults={
        'is_completed': s.status == TrainingSession.Status.COMPLETED,
        'first_score': scores[0] if scores else None, 'last_score': cur_score, 'best_score': max(scores) if scores else None,
        'prompt_score': prompt_engine.analyze(s.current_prompt)['score'] if s.current_prompt else None,
        'quiz_score': _num(quiz.get('score')) if isinstance(quiz, dict) else None,
        'generations': s.generations, 'prompt_improvements': s.prompt_improvements, 'edits': s.edits,
        'time_spent_ms': s.time_spent_ms,
    })


def delete_session(user, code):
    TrainingSession.objects.filter(user=user, work__code=code).delete()
    Progress.objects.filter(user=user, work__code=code).delete()


# ---------- портфолио ----------
def portfolio_dict(p):
    item = dict(p.extra or {})
    item.update({'id': p.client_id, 'ts': dt_to_ms(p.created_at), 'modId': p.work_code, 'title': p.title, 'category': p.category,
                 'text': p.text, 'score': p.score, 'promptScore': p.prompt_score, 'prompt': p.prompt, 'versionN': p.version_number})
    return item


def save_portfolio_item(user, data):
    if not isinstance(data, dict) or not data.get('id'):
        raise ValueError('Ожидается материал портфолио')
    code = _str(data.get('modId'), 32)
    session = TrainingSession.objects.filter(user=user, work__code=code).first() if code else None
    extra = {k: v for k, v in data.items() if k not in ('id', 'ts', 'modId', 'title', 'category', 'text', 'score', 'promptScore', 'prompt', 'versionN')}
    item, _ = PortfolioItem.objects.update_or_create(user=user, client_id=_str(data['id'], 64), defaults={
        'session': session, 'work_code': code, 'category': _str(data.get('category') or 'Другое', 100),
        'title': _str(data.get('title') or 'Материал', 500), 'text': _str(data.get('text'), 300000),
        'score': _num(data.get('score')), 'prompt_score': _num(data.get('promptScore')), 'prompt': _str(data.get('prompt'), 40000),
        'version_number': _num(data.get('versionN')), 'extra': extra, 'created_at': ms_to_dt(data.get('ts')),
        'generation': session.current_generation if session else None})
    return item


# ---------- события ----------
def add_events(user, events):
    rows = []
    for e in (events or [])[:200]:
        if isinstance(e, dict) and e.get('type'):
            rows.append(LearningEvent(user=user, type=_str(e['type'], 40), work_code=_str(e.get('modId'), 32),
                                      data=e.get('data'), created_at=ms_to_dt(e.get('ts'))))
    LearningEvent.objects.bulk_create(rows)
    extra = LearningEvent.objects.filter(user=user).count() - MAX_EVENTS
    if extra > 0:
        ids = list(LearningEvent.objects.filter(user=user).order_by('id').values_list('id', flat=True)[:extra])
        LearningEvent.objects.filter(id__in=ids).delete()
    return len(rows)


# ---------- полное состояние ----------
def load_state(user):
    profile, settings = profile_dict(user)
    sessions = {}
    qs = (TrainingSession.objects.filter(user=user).select_related('work', 'current_generation')
          .prefetch_related('prompts'))
    for s in qs:
        sessions[s.work.code] = session_dict(s)
    portfolio = [portfolio_dict(p) for p in PortfolioItem.objects.filter(user=user)]
    events = [{'ts': dt_to_ms(e.created_at), 'type': e.type, 'modId': e.work_code or None, 'data': e.data}
              for e in LearningEvent.objects.filter(user=user).order_by('-id')[:800]][::-1]
    return {'version': 3, 'profile': profile, 'settings': settings, 'sessions': sessions, 'portfolio': portfolio, 'events': events}
