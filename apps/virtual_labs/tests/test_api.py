"""API модуля: данные слушателя, ИИ, литература, файлы, экспорт, тьютор, администратор, безопасность."""
import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from ..models import (AIProvider, AIRequest, Assignment, Evaluation, Generation, LabRole, Progress, Prompt, Recommendation, Review,
                      TrainingSession, UploadedFile)
from .helpers import LECTURE, client_for, docx_bytes, make_user, pdf_bytes, pptx_bytes, zip_with

API = '/api/v1/virtual-labs/'


def session_payload(text=LECTURE, versions=1, completed=False):
    vs = [{'id': f'v{i}', 'n': i, 'ts': 1_700_000_000_000 + i, 'kind': 'generation', 'prompt': 'РОЛЬ: методист',
           'promptScore': 12, 'text': text, 'score': 1, 'provider': 'demo', 'model': 'шаблонный генератор', 'ms': 10}
          for i in range(1, versions + 1)]
    return {'inputs': {'discipline': 'Педагогика', 'topic': 'Методы активного обучения', 'outcomes': 'применять кейс-метод'},
            'prompt': 'РОЛЬ: методист вуза\nЗАДАЧА: разработай лекцию', 'promptVersions': [{'text': 'РОЛЬ: методист', 'score': 12, 'kind': 'initial', 'ts': 1}],
            'versions': vs, 'currentVersionId': vs[-1]['id'] if vs else None, 'startedAt': 1_700_000_000_000, 'timeSpent': 60000,
            'step': 4, 'stats': {'generations': versions, 'promptImprovements': 0, 'edits': 0}, 'completed': completed,
            'quiz': {'score': 67, 'answers': [1, 1, 0]}, 'chat': []}


class LearnerApiTests(TestCase):
    def setUp(self):
        self.user = make_user('teacher1')
        self.c = client_for(self.user)

    def test_config(self):
        j = self.c.get(API + 'config/').json()
        self.assertEqual(j['user']['role'], 'teacher')
        self.assertEqual(len(j['enabledWorks']), 14)
        self.assertEqual(len(j['courses']), 5)
        self.assertFalse(j['ai']['configured'])
        self.assertEqual(len(j['criteria']), 10)

    def test_requires_login(self):
        self.assertIn(APIClient().get(API + 'state/').status_code, (401, 403))

    def test_session_roundtrip_and_server_evaluation(self):
        r = self.c.put(API + 'sessions/vw01/', session_payload(versions=2, completed=True), format='json')
        self.assertEqual(r.status_code, 200, r.content)
        s = TrainingSession.objects.get(user=self.user, work__code='vw01')
        self.assertEqual(s.status, 'completed')
        self.assertEqual(s.discipline.name, 'Педагогика')
        self.assertEqual(Generation.objects.filter(session=s).count(), 2)
        self.assertEqual(Prompt.objects.filter(session=s).count(), 1)
        ev = Evaluation.objects.get(generation__client_id='v2', generation__session=s)
        self.assertGreater(ev.total, 50)            # балл пересчитан сервером, а не взят из запроса (1)
        self.assertTrue(Recommendation.objects.filter(session=s).exists())
        p = Progress.objects.get(user=self.user, work__code='vw01')
        self.assertTrue(p.is_completed)
        self.assertEqual(p.quiz_score, 67)
        state = self.c.get(API + 'state/').json()
        sess = state['sessions']['vw01']
        self.assertEqual(len(sess['versions']), 2)
        self.assertEqual(sess['versions'][1]['score'], ev.total)
        self.assertEqual(sess['quiz']['score'], 67)
        self.assertEqual(sess['lastEvaluation']['total'], ev.total)
        # повторное сохранение не создаёт дублей
        self.c.put(API + 'sessions/vw01/', session_payload(versions=2, completed=True), format='json')
        self.assertEqual(Generation.objects.filter(session=s).count(), 2)

    def test_unknown_work(self):
        self.assertEqual(self.c.put(API + 'sessions/vw99/', session_payload(), format='json').status_code, 400)

    def test_profile_settings_portfolio_events(self):
        prof = {'fio': 'Иванова А. Б.', 'organization': 'КазНУ', 'position': 'доцент', 'discipline': 'Педагогика',
                'program': '6B01', 'audience': 'Студенты бакалавриата', 'language': 'Казахский', 'duration': 50}
        self.assertEqual(self.c.put(API + 'profile/', prof, format='json').json()['fio'], 'Иванова А. Б.')
        self.c.put(API + 'settings/', {'theme': 'dark', 'apiKey': 'secret'}, format='json')
        self.c.put(API + 'sessions/vw01/', session_payload(), format='json')
        self.assertEqual(self.c.post(API + 'portfolio/', {'id': 'p1', 'ts': 1, 'modId': 'vw01', 'title': 'Лекция', 'category': 'Лекции',
                                                          'text': LECTURE, 'score': 80, 'promptScore': 50, 'prompt': 'x', 'versionN': 1},
                                     format='json').status_code, 201)
        self.c.post(API + 'events/', {'events': [{'ts': 1, 'type': 'generation', 'modId': 'vw01'}]}, format='json')
        st = self.c.get(API + 'state/').json()
        self.assertEqual(st['profile']['discipline'], 'Педагогика')
        self.assertEqual(st['profile']['language'], 'Казахский')
        self.assertNotIn('apiKey', st['settings'])
        self.assertEqual(st['portfolio'][0]['title'], 'Лекция')
        self.assertEqual(st['events'][-1]['type'], 'generation')
        self.assertEqual(self.c.delete(API + 'portfolio/p1/').status_code, 204)
        self.assertEqual(self.c.get(API + 'state/').json()['portfolio'], [])

    def test_evaluate_and_analyze(self):
        j = self.c.post(API + 'evaluate/', {'work': 'vw01', 'text': LECTURE, 'inputs': {'topic': 'Методы активного обучения'}}, format='json').json()
        self.assertEqual(len(j['criteria']), 10)
        a = self.c.post(API + 'prompt/analyze/', {'text': 'РОЛЬ: методист. ЗАДАЧА: создай лекцию для студентов'}, format='json').json()
        self.assertIn('role', a['present'])

    def test_analytics_and_next(self):
        self.c.put(API + 'sessions/vw13/', session_payload(completed=True), format='json')
        an = self.c.get(API + 'analytics/').json()
        self.assertEqual(an['completed'], 1)
        nxt = self.c.get(API + 'next/').json()
        self.assertEqual(nxt['id'], 'vw01')


class AIApiTests(TestCase):
    def setUp(self):
        self.user = make_user('teacher2')
        self.c = client_for(self.user)

    def test_not_configured(self):
        r = self.c.post(API + 'ai/generate/', {'prompt': 'Напиши лекцию'}, format='json')
        self.assertEqual(r.status_code, 409)

    def test_generate_with_provider_and_log(self):
        AIProvider.objects.create(name='OpenAI', kind='openai', api_key='sk-test-1234567890', is_active=True)
        answer = {'choices': [{'message': {'content': '## Цель\nТекст'}}]}
        with mock.patch('apps.virtual_labs.services.ai._request', return_value=answer) as req:
            r = self.c.post(API + 'ai/generate/', {'prompt': 'Напиши лекцию', 'kind': 'generate'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['text'], '## Цель\nТекст')
        url, payload, headers = req.call_args[0][:3]
        self.assertEqual(url, 'https://api.openai.com/v1/chat/completions')
        self.assertEqual(payload['messages'][0]['role'], 'system')
        self.assertEqual(headers['Authorization'], 'Bearer sk-test-1234567890')
        log = AIRequest.objects.get()
        self.assertTrue(log.ok)
        self.assertEqual(log.user, self.user)

    def test_gemini_and_errors_logged(self):
        AIProvider.objects.create(name='Gemini', kind='gemini', api_key='g-key', is_active=True)
        from ..services.ai import AIError
        with mock.patch('apps.virtual_labs.services.ai._request', side_effect=AIError('квота исчерпана')):
            r = self.c.post(API + 'ai/generate/', {'messages': [{'role': 'user', 'content': 'Привет'}], 'kind': 'chat'}, format='json')
        self.assertEqual(r.status_code, 502)
        self.assertFalse(AIRequest.objects.get().ok)

    def test_system_template_used(self):
        from ..models import PromptTemplate
        PromptTemplate.objects.create(work=None, system='Ты — методист AI edu. Отвечай на казахском.')
        AIProvider.objects.create(name='OpenAI', kind='openai', api_key='k', is_active=True)
        with mock.patch('apps.virtual_labs.services.ai._request', return_value={'choices': [{'message': {'content': 'ok'}}]}) as req:
            self.c.post(API + 'ai/generate/', {'prompt': 'x'}, format='json')
        self.assertIn('казахском', req.call_args[0][1]['messages'][0]['content'])

    @override_settings(VIRTUAL_LABS={'THROTTLE_RATES': {'ai': '2/minute'}})
    def test_rate_limit(self):
        from django.core.cache import cache
        cache.clear()
        codes = [self.c.post(API + 'ai/generate/', {'prompt': 'x'}, format='json').status_code for _ in range(3)]
        self.assertEqual(codes, [409, 409, 429])


def fake_urlopen(responses):
    class Resp:
        def __init__(self, data):
            self.data = json.dumps(data).encode()

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        url = req.full_url
        for key, data in responses.items():
            if key in url:
                if isinstance(data, Exception):
                    raise data
                return Resp(data)
        raise AssertionError(url)
    return opener


class LiteratureTests(TestCase):
    def test_three_sources_deduplicated(self):
        import urllib.error
        responses = {
            'api.openalex.org': {'results': [{'display_name': 'Active learning in higher education', 'doi': 'https://doi.org/10.1/abc',
                                              'publication_year': 2020, 'cited_by_count': 12,
                                              'authorships': [{'author': {'display_name': 'A. Smith'}}],
                                              'primary_location': {'source': {'display_name': 'Journal'}},
                                              'abstract_inverted_index': {'Active': [0], 'learning': [1]}}]},
            'api.crossref.org': {'message': {'items': [
                {'DOI': '10.1/ABC', 'title': ['Active learning in higher education'], 'issued': {'date-parts': [[2020]]}},
                {'DOI': '10.2/xyz', 'title': ['Case method'], 'author': [{'given': 'B.', 'family': 'Lee'}], 'issued': {'date-parts': [[2019, 1]]},
                 'container-title': ['Education'], 'is-referenced-by-count': 3, 'abstract': '<jats:p>About cases</jats:p>'}]}},
            'api.semanticscholar.org': urllib.error.URLError('timeout'),
        }
        c = client_for(make_user('researcher'))
        with mock.patch('urllib.request.urlopen', side_effect=fake_urlopen(responses)):
            j = c.get(API + 'literature/', {'q': 'active learning'}).json()
        self.assertEqual([r['source'] for r in j['results']], ['openalex', 'crossref'])
        self.assertEqual(j['results'][0]['abstract'], 'Active learning')
        self.assertEqual(j['results'][1]['abstract'], 'About cases')
        self.assertIn('semantic_scholar', j['errors'])

    def test_semantic_scholar_only(self):
        responses = {'api.semanticscholar.org': {'data': [{'title': 'Prompting', 'year': 2024, 'externalIds': {'DOI': '10.3/p'},
                                                           'authors': [{'name': 'C. Wu'}], 'citationCount': 7, 'venue': 'ACL'}]}}
        c = client_for(make_user('researcher2'))
        with mock.patch('urllib.request.urlopen', side_effect=fake_urlopen(responses)):
            j = c.get(API + 'literature/', {'q': 'prompting', 'sources': 'semantic_scholar'}).json()
        self.assertEqual(j['results'][0]['doi'], '10.3/p')

    def test_validation(self):
        c = client_for(make_user('researcher3'))
        self.assertEqual(c.get(API + 'literature/', {'q': 'x'}).status_code, 400)
        self.assertEqual(c.get(API + 'literature/', {'q': 'xx', 'sources': 'google'}).status_code, 400)


@override_settings(MEDIA_ROOT='/tmp/claude-0/vl-test-media')
class FileTests(TestCase):
    def setUp(self):
        self.user = make_user('uploader')
        self.c = client_for(self.user)

    def upload(self, name, data, kind='syllabus'):
        return self.c.post(API + 'files/', {'file': SimpleUploadedFile(name, data), 'kind': kind}, format='multipart')

    def test_docx_pdf_pptx_txt(self):
        r = self.upload('Силлабус.docx', docx_bytes(['Неделя 1. Введение', 'Неделя 2. Методы']))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIn('Неделя 2. Методы', r.json()['text'])
        self.assertIn('## Силлабус дисциплины', r.json()['text'])
        r = self.upload('lecture.pdf', pdf_bytes(LECTURE), 'lecture')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIn('Цель лекции', r.json()['text'])
        r = self.upload('slides.pptx', pptx_bytes(['Введение', 'Итоги']), 'presentation')
        self.assertEqual(r.json()['pages'], 2)
        r = self.upload('notes.txt', 'Тема: активное обучение'.encode('cp1251'), 'other')
        self.assertEqual(r.json()['text'], 'Тема: активное обучение')
        self.assertEqual(len(self.c.get(API + 'files/').json()), 4)
        f = UploadedFile.objects.get(original_name='Силлабус.docx')
        self.assertEqual(self.c.get(f'{API}files/{f.pk}/download/').status_code, 200)
        other = client_for(make_user('stranger'))
        self.assertEqual(other.get(f'{API}files/{f.pk}/').status_code, 404)
        self.assertEqual(other.get(f'{API}files/{f.pk}/download/').status_code, 404)
        tutor = make_user('tutor-files', 'tutor')
        self.assertEqual(client_for(tutor).get(f'{API}files/{f.pk}/download/').status_code, 404)   # не в группе тьютора
        from ..models import GroupMembership, StudyGroup
        GroupMembership.objects.create(group=StudyGroup.objects.create(tutor=tutor, name='Группа'), user=self.user)
        self.assertEqual(client_for(tutor).get(f'{API}files/{f.pk}/download/').status_code, 200)
        self.assertEqual(self.c.delete(f'{API}files/{f.pk}/').status_code, 204)

    def test_duplicate_returns_existing(self):
        data = docx_bytes(['a'])
        first = self.upload('a.docx', data).json()
        second = self.upload('b.docx', data)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['id'], first['id'])

    def test_rejected_files(self):
        cases = [
            ('virus.exe', b'MZ\x90\x00'),
            ('fake.pdf', b'PK\x03\x04 not a pdf'),
            ('fake.docx', b'%PDF-1.4 not a docx'),
            ('macro.docx', zip_with(['[Content_Types].xml', 'word/document.xml', 'word/vbaProject.bin'])),
            ('wrong.pptx', zip_with(['[Content_Types].xml', 'word/document.xml'])),
            ('script.pdf', b'%PDF-1.4\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>'),
            ('binary.txt', b'\x00\x01\x02'),
            ('empty.md', b''),
        ]
        for name, data in cases:
            r = self.upload(name, data)
            self.assertEqual(r.status_code, 400, name)
        self.assertEqual(UploadedFile.objects.count(), 0)

    @override_settings(VIRTUAL_LABS={'MAX_UPLOAD_BYTES': 100})
    def test_size_limit(self):
        self.assertEqual(self.upload('big.txt', b'a' * 200).status_code, 400)

    def test_attachment_in_pdf_and_zip_bomb(self):
        attached = b'%PDF-1.4\n1 0 obj << /Type /Filespec /EF << /F 2 0 R >> /Type /EmbeddedFile >> endobj'
        r = self.upload('attached.pdf', attached)
        self.assertEqual(r.status_code, 400)
        self.assertIn('вложениями', r.content.decode())
        import io
        import zipfile
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml', 'x')
            z.writestr('word/document.xml', b'0' * (2 * 1024 * 1024))
        bomb = out.getvalue()
        self.assertLess(len(bomb), 20_000)
        with override_settings(VIRTUAL_LABS={'MAX_UNPACKED_BYTES': 1024 * 1024}):
            r = self.upload('bomb.docx', bomb)
        self.assertEqual(r.status_code, 400)
        self.assertIn('после распаковки', r.content.decode())
        self.assertEqual(UploadedFile.objects.count(), 0)


class ExportApiTests(TestCase):
    def test_formats(self):
        c = client_for(make_user('exporter'))
        for fmt, sig in (('docx', b'PK'), ('pptx', b'PK'), ('pdf', b'%PDF'), ('txt', 'Л'.encode()), ('md', b'# ')):
            r = c.post(API + 'export/', {'format': fmt, 'title': 'Лекция', 'text': LECTURE, 'lang': 'ru'}, format='json')
            self.assertEqual(r.status_code, 200, fmt)
            self.assertTrue(r.content.startswith(sig), fmt)
            self.assertIn('attachment', r['Content-Disposition'])
        self.assertEqual(c.post(API + 'export/', {'format': 'exe', 'text': 'x'}, format='json').status_code, 400)


class TutorTests(TestCase):
    def setUp(self):
        self.tutor = make_user('tutor1', 'tutor')
        self.t = client_for(self.tutor)
        self.student = make_user('student1')
        self.s = client_for(self.student)
        self.outsider = make_user('outsider')

    def test_full_tutor_cycle(self):
        g = self.t.post(API + 'tutor/groups/', {'name': 'ПК-2026', 'description': 'Повышение квалификации'}, format='json').json()
        self.assertEqual(self.s.post(API + 'groups/join/', {'code': g['inviteCode'].lower()}, format='json').status_code, 201)
        self.t.post(f"{API}tutor/groups/{g['id']}/members/", {'login': 'outsider'}, format='json')
        self.assertEqual(self.t.get(f"{API}tutor/groups/{g['id']}/").json()['members'], 2)
        self.t.delete(f"{API}tutor/groups/{g['id']}/members/{self.outsider.pk}/")
        a = self.t.post(API + 'tutor/assignments/', {'groupId': g['id'], 'workCode': 'vw01', 'title': 'Лекция по теме курса',
                                                     'dueAt': '2026-12-01'}, format='json')
        self.assertEqual(a.status_code, 201, a.content)
        due = timezone.localtime(Assignment.objects.get().due_at)
        self.assertEqual((due.date().isoformat(), due.hour, due.minute), ('2026-12-01', 23, 59))  # дата без времени = конец дня
        bad = self.t.post(API + 'tutor/assignments/', {'groupId': g['id'], 'workCode': 'vw01', 'dueAt': '2026-02-30'}, format='json')
        self.assertEqual(bad.status_code, 400)
        mine = self.s.get(API + 'assignments/').json()
        self.assertEqual(mine[0]['title'], 'Лекция по теме курса')
        self.assertFalse(mine[0]['completed'])
        self.s.put(API + 'sessions/vw01/', session_payload(completed=True), format='json')
        self.assertEqual(self.t.get(API + 'tutor/assignments/').json()[0]['completed'], 1)
        sessions = self.t.get(API + 'tutor/sessions/', {'group': g['id']}).json()
        self.assertEqual(len(sessions), 1)
        detail = self.t.get(f"{API}tutor/sessions/{sessions[0]['id']}/").json()
        self.assertEqual(len(detail['versions']), 1)
        r = self.t.post(f"{API}tutor/sessions/{sessions[0]['id']}/reviews/", {'comment': 'Добавьте примеры', 'grade': 85}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Review.objects.get().assignment.title, 'Лекция по теме курса')
        reviews = self.s.get(API + 'reviews/').json()
        self.assertEqual(reviews[0]['grade'], 85)
        self.assertFalse(reviews[0]['seen'])
        self.s.post(API + 'reviews/')
        self.assertTrue(self.s.get(API + 'reviews/').json()[0]['seen'])
        overview = self.t.get(API + 'tutor/overview/').json()
        self.assertEqual([u['login'] for u in overview], ['student1'])
        self.assertEqual(overview[0]['avgGrade'], 85)

    def test_tutor_sees_only_own_groups(self):
        make_user('other-tutor', 'tutor')
        self.outsider_client = client_for(self.outsider)
        self.outsider_client.put(API + 'sessions/vw01/', session_payload(), format='json')
        sid = TrainingSession.objects.get(user=self.outsider).pk
        self.assertEqual(self.t.get(f'{API}tutor/sessions/{sid}/').status_code, 403)
        self.assertEqual(self.t.get(API + 'tutor/sessions/').json(), [])

    def test_teacher_cannot_use_tutor_api(self):
        self.assertEqual(self.s.get(API + 'tutor/groups/').status_code, 403)
        self.assertEqual(self.s.get(API + 'admin/users/').status_code, 403)


class AdminTests(TestCase):
    def setUp(self):
        self.admin = make_user('admin1', 'admin')
        self.a = client_for(self.admin)
        self.teacher = make_user('teacher9')

    @override_settings(VIRTUAL_LABS={'MANAGE_ACCOUNTS': True})
    def test_roles_and_blocking(self):
        r = self.a.patch(f'{API}admin/users/{self.teacher.pk}/', {'role': 'tutor'}, format='json')
        self.assertEqual(r.json()['role'], 'tutor')
        self.assertEqual(LabRole.objects.get(user=self.teacher).role, 'tutor')
        self.assertEqual(self.a.patch(f'{API}admin/users/{self.admin.pk}/', {'role': 'teacher'}, format='json').status_code, 400)
        self.a.patch(f'{API}admin/users/{self.teacher.pk}/', {'isActive': False}, format='json')
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.is_active)
        self.assertEqual(len(self.a.get(API + 'admin/users/', {'q': 'teacher'}).json()), 1)

    @override_settings(VIRTUAL_LABS={'MANAGE_ACCOUNTS': False})
    def test_blocking_disabled_in_platform(self):
        self.assertEqual(self.a.patch(f'{API}admin/users/{self.teacher.pk}/', {'isActive': False}, format='json').status_code, 400)

    def test_courses_works_templates_criteria(self):
        c = self.a.post(API + 'admin/courses/', {'title': 'Мой курс'}, format='json').json()
        self.a.patch(API + 'admin/works/vw05/', {'courseId': c['id'], 'isEnabled': True}, format='json')
        self.a.patch(API + 'admin/works/vw06/', {'isEnabled': False}, format='json')
        self.a.put(API + 'admin/prompt-templates/vw01/', {'role': 'декан факультета', 'constraints': 'Не более 1500 слов\nБез таблиц'}, format='json')
        self.a.patch(API + 'admin/criteria/clarity/', {'weight': 2, 'name': 'Доступность изложения'}, format='json')
        cfg = client_for(self.teacher).get(API + 'config/').json()
        self.assertNotIn('vw06', cfg['enabledWorks'])
        self.assertIn('vw05', next(x for x in cfg['courses'] if x['title'] == 'Мой курс')['works'])
        self.assertEqual(cfg['templates']['vw01']['role'], 'декан факультета')
        self.assertEqual(cfg['templates']['vw01']['constraints'], ['Не более 1500 слов', 'Без таблиц'])
        clarity = next(x for x in cfg['criteria'] if x['key'] == 'clarity')
        self.assertEqual((clarity['weight'], clarity['name']), (2, 'Доступность изложения'))
        self.assertEqual(self.a.patch(API + 'admin/criteria/clarity/', {'weight': 9}, format='json').status_code, 400)
        self.assertEqual(len(self.a.get(API + 'admin/prompt-templates/').json()), 15)

    def test_ai_providers_key_hidden(self):
        r = self.a.post(API + 'admin/ai-providers/', {'kind': 'openai', 'apiKey': 'sk-very-secret-key-9876', 'model': 'gpt-4o-mini',
                                                      'isActive': True}, format='json')
        self.assertEqual(r.status_code, 201)
        listing = self.a.get(API + 'admin/ai-providers/').json()
        self.assertNotIn('sk-very-secret-key-9876', json.dumps(listing))
        self.assertEqual(listing['providers'][0]['keyHint'], '…9876')
        cfg = client_for(self.teacher).get(API + 'config/').json()
        self.assertTrue(cfg['ai']['configured'])
        self.assertNotIn('secret', json.dumps(cfg))
        second = self.a.post(API + 'admin/ai-providers/', {'kind': 'gemini', 'apiKey': 'g', 'isActive': True}, format='json').json()
        self.assertEqual(AIProvider.objects.filter(is_active=True, task='text').get().pk, second['id'])
        self.assertEqual(self.a.post(API + 'admin/ai-providers/', {'kind': 'openai', 'baseUrl': 'http://evil.example'}, format='json').status_code, 400)
        with mock.patch('apps.virtual_labs.services.ai._request', return_value={'candidates': [{'content': {'parts': [{'text': 'готово'}]}}]}):
            self.assertTrue(self.a.post(f"{API}admin/ai-providers/{second['id']}/test/").json()['ok'])

    def test_backup(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, override_settings(VIRTUAL_LABS={'BACKUP_DIR': tmp, 'BACKUP_KEEP': 2}):
            for _ in range(3):
                r = self.a.post(API + 'admin/backups/')
                self.assertEqual(r.status_code, 201, r.content)
            import os
            self.assertEqual(len(os.listdir(tmp)), 2)
            self.assertEqual(len(self.a.get(API + 'admin/backups/').json()['backups']), 3)

    def test_ai_log(self):
        AIRequest.objects.create(user=self.teacher, kind='generate', provider='openai', model='m')
        self.assertEqual(self.a.get(API + 'admin/ai-log/').json()[0]['user'], 'teacher9')
