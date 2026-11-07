"""Общий AI Evaluation Engine (ТЗ, п. 27) с настройками администратора и каталогом работ на сервере."""
from .. import catalog
from . import evaluation


def module_for(work_code, inputs=None):
    """Описание работы для экспертизы. Для ВР № 14 оценивается материал выбранного типа."""
    w = catalog.work(work_code)
    if not w:
        return None
    if w.get('special') == 'expertise':
        target = catalog.work((inputs or {}).get('targetWork') or 'vw01') or catalog.work('vw01')
        return target
    return w


def evaluate_work(work_code, text, inputs=None):
    inputs = inputs or {}
    mod = module_for(work_code, inputs)
    if not mod:
        raise ValueError('Неизвестная виртуальная работа')
    w = catalog.work(work_code)
    body = inputs.get('content', '') if w.get('special') == 'expertise' else text
    return evaluate_module(mod, body, inputs)


def evaluate_module(mod, text, inputs=None):
    config = catalog.criteria_config()
    return evaluation.evaluate(mod, text or '', inputs or {}, config if catalog.criteria_changed(config) else None)
