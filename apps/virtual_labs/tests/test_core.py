"""Ядро: экспертиза и Prompt Engine совпадают с интерфейсом, каталог, рекомендации, экспорт."""
import io
import json
import zipfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from .. import catalog
from ..models import EvaluationCriterion
from ..services import evaluation, expertise, exporting, prompt_engine, recommendations
from .helpers import LECTURE

CASES = json.loads((Path(__file__).parent / 'parity_cases.json').read_text(encoding='utf-8'))


class ParityTests(SimpleTestCase):
    """Те же 31 случай, что и в тестах интерфейса: сервер ставит те же баллы."""
    def test_evaluation_matches_frontend(self):
        for case in CASES:
            got = evaluation.evaluate(case['module'], case['text'], case['inputs'])
            exp = case['expected']
            self.assertEqual(got['total'], exp['total'], case['module']['id'])
            self.assertEqual([c['score'] for c in got['criteria']], [c['score'] for c in exp['criteria']])
            self.assertEqual([s['status'] for s in got['sections']], [s['status'] for s in exp['sections']])

    def test_prompt_analysis_matches_frontend(self):
        for case in CASES:
            got = prompt_engine.analyze(case['prompt'])
            self.assertEqual(got['score'], case['expectedPrompt']['score'])
            self.assertEqual(got['missing'], case['expectedPrompt']['missing'])


class CatalogTests(TestCase):
    def test_catalog_has_14_works_and_db_is_seeded(self):
        self.assertEqual(len(catalog.works()), 14)
        from ..models import Course, VirtualWork
        self.assertEqual(VirtualWork.objects.count(), 14)
        self.assertEqual(Course.objects.count(), 5)
        self.assertEqual(EvaluationCriterion.objects.count(), 10)

    def test_criteria_weights_change_total(self):
        base = expertise.evaluate_work('vw01', LECTURE, {'topic': 'Методы активного обучения'})
        EvaluationCriterion.objects.filter(key='sources').update(weight=0)
        EvaluationCriterion.objects.filter(key='structure').update(name='Структура материала')
        weighted = expertise.evaluate_work('vw01', LECTURE, {'topic': 'Методы активного обучения'})
        self.assertNotEqual(base['total'], weighted['total'])
        self.assertIn('Структура материала', [c['name'] for c in weighted['criteria']])
        EvaluationCriterion.objects.filter(key='sources').update(weight=1, is_active=False)
        self.assertNotIn('sources', [c['key'] for c in expertise.evaluate_work('vw01', LECTURE, {})['criteria']])

    def test_expertise_work_uses_target(self):
        ev = expertise.evaluate_work('vw14', 'отчёт', {'targetWork': 'vw01', 'content': LECTURE})
        self.assertEqual(len(ev['sections']), 10)


class RecommendationTests(SimpleTestCase):
    def test_rules(self):
        ev = evaluation.evaluate(catalog.work('vw01'), 'Короткий текст', {'topic': 'Тема'})
        recs = recommendations.for_work(catalog.work('vw01'), 'напиши лекцию', ev, {'generations': 3, 'promptImprovements': 0, 'edits': 0})
        kinds = [k for k, _ in recs]
        self.assertIn('prompt', kinds)
        self.assertIn('content', kinds)
        self.assertTrue(any('перегенерировали' in t for _, t in recs))

    def test_next_work(self):
        self.assertEqual(recommendations.next_work(set(), ['vw01', 'vw13'])['id'], 'vw13')
        self.assertEqual(recommendations.next_work({'vw13'}, ['vw01', 'vw13', 'vw14'], avg_sources=5)['id'], 'vw14')
        self.assertIsNone(recommendations.next_work({'vw01'}, ['vw01'])['id'])


class ExportTests(SimpleTestCase):
    def test_docx(self):
        data = exporting.export('docx', 'Лекция', LECTURE, 'Педагогика', 'ru')
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = z.read('word/document.xml').decode()
        self.assertIn('Цель лекции', xml)
        self.assertIn('Кейс-метод', xml)

    def test_pdf_has_cyrillic_and_kazakh(self):
        data = exporting.export('pdf', 'Дәріс: әдістер', LECTURE + '\n\n| А | Ә |\n|---|---|\n| қ | ң |', '', 'kz')
        self.assertTrue(data.startswith(b'%PDF'))
        from pypdf import PdfReader
        text = ''.join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
        self.assertIn('Цель лекции', text)
        self.assertIn('Дәріс', text)

    def test_pptx_slides_from_work_5(self):
        md = '## Структура\n- Титульный слайд\n\n## Слайды\n### Слайд 1. Титульный слайд\n- Текст: тема\n### Слайд 2. Цели\n- Текст: цели\n### Слайд 3. Итоги\n- Текст: итоги'
        data = exporting.export('pptx', 'Презентация', md)
        from pptx import Presentation
        prs = Presentation(io.BytesIO(data))
        titles = [s.shapes.title.text for s in prs.slides if s.shapes.title is not None]
        self.assertIn('Титульный слайд', titles)
        self.assertIn('Цели', titles)
        self.assertEqual(len(prs.slides), 5)   # обложка + 3 слайда + финальный

    def test_pptx_from_sections(self):
        data = exporting.export('pptx', 'Лекция', LECTURE)
        from pptx import Presentation
        self.assertEqual(len(Presentation(io.BytesIO(data)).slides), 12)

    def test_txt_and_md(self):
        self.assertIn('ЦЕЛЬ ЛЕКЦИИ', exporting.export('txt', 'Лекция', LECTURE).decode())
        self.assertTrue(exporting.export('md', 'Лекция', LECTURE, lang='en').decode().strip().endswith('final material._'))


class WeightedCriteriaTests(SimpleTestCase):
    """Веса и отключение критериев (раздел «Администрирование → Критерии»): те же ожидания проверяет тест интерфейса."""
    def test_weighted_cases(self):
        cases = json.loads((Path(__file__).parent / 'weighted_cases.json').read_text(encoding='utf-8'))
        for item in cases:
            case = CASES[item['case']]
            got = evaluation.evaluate(case['module'], case['text'], case['inputs'], item['config'])
            self.assertEqual(got['total'], item['total'])
            self.assertEqual([c['key'] for c in got['criteria']], item['criteria'])


class PptxSlidesTests(SimpleTestCase):
    def test_lecture_sections_become_slides(self):
        from ..services.exporting import slides_from_markdown
        md = '> Демо-режим\n\n# Лекция: Тема\n\n## Цель лекции\nТекст.\n\n## План\n1. A\n2. B\n\n## Выводы\n- x\n\n## Вопросы\n- q'
        slides, cover = slides_from_markdown('Лекция: Тема', md)
        self.assertEqual([s['title'] for s in slides], ['Цель лекции', 'План', 'Выводы', 'Вопросы'])
        self.assertIn('Лекция: Тема', cover)

    def test_plain_text_gives_one_slide(self):
        from ..services.exporting import slides_from_markdown
        slides, cover = slides_from_markdown('Заметка', 'Первый абзац.\n\nВторой абзац.')
        self.assertEqual(len(slides), 1)
        self.assertEqual(slides[0]['bullets'], ['Первый абзац.', 'Второй абзац.'])
        self.assertEqual(cover, [])
