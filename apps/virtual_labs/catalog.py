"""Каталог виртуальных работ.

Описания 14 работ (поля, разделы, промпты, вопросы) живут в интерфейсе, в реестре модулей
(frontend/src/virtual-labs/core/works.ts). Сервер получает их копию catalog/works.json
(собирается командой npm run catalog) — это нужно для экспертизы на сервере и для практик AI edu.
В базе хранится только то, чем управляет администратор: курсы, порядок и доступность работ,
критерии экспертизы и шаблоны промптов. Недостающие записи создаются после migrate.
"""
import json
from functools import lru_cache
from pathlib import Path

CATALOG_FILE = Path(__file__).resolve().parent / 'catalog' / 'works.json'

DEFAULT_COURSES = (
    ('Основы работы с генеративным ИИ', ('vw13', 'vw14')),
    ('Учебные материалы', ('vw01', 'vw02', 'vw03', 'vw04', 'vw11')),
    ('Оценивание и AI-ассистенты', ('vw10', 'vw09')),
    ('Мультимедийный контент', ('vw05', 'vw06', 'vw12')),
    ('Научная деятельность', ('vw07', 'vw08')),
)

DEFAULT_CRITERIA = (
    ('structure', 'Структура'), ('completeness', 'Полнота'), ('topic', 'Соответствие теме'),
    ('outcomes', 'Соответствие результатам обучения'), ('audience', 'Соответствие аудитории'),
    ('clarity', 'Понятность'), ('terms', 'Корректность терминов'), ('pedagogy', 'Педагогическая ценность'),
    ('science', 'Научная корректность'), ('sources', 'Достоверность и риск галлюцинаций'),
)


@lru_cache(maxsize=1)
def works():
    data = json.loads(CATALOG_FILE.read_text(encoding='utf-8'))
    return {w['id']: w for w in data['works']}


def work(code):
    return works().get(code)


def sync_catalog(**_kwargs):
    """Создаёт недостающие курсы, работы и критерии. Изменения администратора не затираются."""
    from .models import Course, EvaluationCriterion, VirtualWork
    catalog = works()
    if not VirtualWork.objects.exists() and not Course.objects.exists():
        for pos, (title, codes) in enumerate(DEFAULT_COURSES):
            course = Course.objects.create(title=title, position=pos)
            for wpos, code in enumerate(codes):
                if code in catalog:
                    w = catalog[code]
                    VirtualWork.objects.create(code=code, number=w['n'], title=w['title'], course=course, position=pos * 100 + wpos)
    for code, w in catalog.items():
        VirtualWork.objects.get_or_create(code=code, defaults={'number': w['n'], 'title': w['title'], 'position': 1000 + w['n']})
    for pos, (key, name) in enumerate(DEFAULT_CRITERIA):
        EvaluationCriterion.objects.get_or_create(key=key, defaults={'name': name, 'position': pos})


def criteria_config():
    from .models import EvaluationCriterion
    return [{'key': c.key, 'name': c.name, 'weight': c.weight, 'is_active': c.is_active}
            for c in EvaluationCriterion.objects.all()]


def criteria_changed(config):
    defaults = dict(DEFAULT_CRITERIA)
    return any(c['weight'] != 1 or not c['is_active'] or c['name'] != defaults.get(c['key'], c['name']) for c in config)
