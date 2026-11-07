"""Recommendation Engine (ТЗ, п. 28) и адаптивный модуль (п. 30): правила (rule-based).

Анализируются промпт, результат экспертизы, число генераций, улучшений промпта и правок.
Логика совпадает с интерфейсом (core/recommendations.ts), сервер сохраняет рекомендации в таблицу.
"""
from . import prompt_engine

TRAJECTORY = ['vw13', 'vw01', 'vw02', 'vw03', 'vw10', 'vw11', 'vw04', 'vw05', 'vw06', 'vw12', 'vw09', 'vw14', 'vw07', 'vw08']
LABELS = {b['key']: b['label'] for b in prompt_engine.BLOCKS}


def for_work(work, prompt, evaluation, stats):
    out = []
    pa = prompt_engine.analyze(prompt) if prompt else None
    if pa and pa['missing']:
        out.append(('prompt', 'В промпте не хватает блоков: ' + ', '.join(LABELS[k] for k in pa['missing']) +
                    '. Нажмите «Улучшить промпт» — система добавит их из ваших исходных данных.'))
    if evaluation:
        weak = sorted([c for c in evaluation.get('criteria', []) if c['score'] < 7], key=lambda c: c['score'])[:3]
        for c in weak:
            out.append(('content', f"{c['name']} ({c['score']}/10): {c['rec']}"))
        if evaluation.get('flags'):
            out.append(('content', 'Перед сохранением проверьте фрагменты, отмеченные в блоке «Требует проверки».'))
        if evaluation.get('total', 0) >= 85 and not weak:
            out.append(('process', 'Материал хорошего качества. Прочитайте его целиком, внесите авторские правки и сохраните в портфолио.'))
    gens, imps, edits = stats.get('generations', 0), stats.get('promptImprovements', 0), stats.get('edits', 0)
    if gens >= 3 and imps == 0:
        out.append(('process', 'Вы несколько раз перегенерировали материал без изменения промпта. '
                               'Качество результата зависит прежде всего от промпта — измените его.'))
    if gens >= 1 and edits == 0 and evaluation and evaluation.get('total', 0) < 85:
        out.append(('process', 'Попробуйте отредактировать результат вручную: ИИ создаёт черновик, а финальную версию формирует преподаватель.'))
    if work and work.get('special') == 'research':
        out.append(('content', 'Используйте поиск OpenAlex, Crossref и Semantic Scholar: в обзор включайте только реально существующие публикации.'))
    if not out:
        out.append(('process', 'Замечаний нет. Сохраните итоговую версию в портфолио.'))
    return out


def next_work(completed, enabled, avg_prompt=None, avg_sources=None, avg_science=None):
    """Адаптивный выбор следующей работы по прогрессу пользователя."""
    order = [c for c in TRAJECTORY if c in enabled] + [c for c in enabled if c not in TRAJECTORY]
    if avg_prompt is not None and avg_prompt < 60 and 'vw13' not in completed and 'vw13' in enabled:
        return {'id': 'vw13', 'reason': f'Средняя оценка ваших промптов — {round(avg_prompt)}%. Промпт-лаборатория поможет освоить структуру запроса.'}
    if ((avg_sources is not None and avg_sources < 7) or (avg_science is not None and avg_science < 7)) and 'vw14' not in completed and 'vw14' in enabled:
        return {'id': 'vw14', 'reason': 'В ваших материалах встречаются непроверенные источники или утверждения. Рекомендуем лабораторию AI-экспертизы.'}
    nxt = next((c for c in order if c not in completed), None)
    if not nxt:
        return {'id': None, 'reason': 'Все виртуальные работы выполнены. Сформируйте итоговое портфолио и экспортируйте его.'}
    return {'id': nxt, 'reason': 'Следующая работа по рекомендуемой траектории.' if completed else
            'Начните с Промпт-лаборатории: это базовый навык для всех остальных работ.'}
