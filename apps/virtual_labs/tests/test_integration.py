"""LTI 1.3 (запуск из LMS) и регистрация работ как практик AI edu."""
import json
import sys
import types
from dataclasses import dataclass
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase

from ..models import LabRole, LtiPlatform, LtiUserLink
from ..permissions import role_of
from ..services import lti
from .helpers import LECTURE, client_for, find_users, make_user

API = '/api/v1/virtual-labs/'


def platform_key():
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update({'kid': 'lms-1', 'alg': 'RS256'})
    return pem, {'keys': [jwk]}


class LtiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pem, cls.jwks = platform_key()

    def setUp(self):
        admin = make_user('platform-admin', 'admin')
        r = client_for(admin).post(API + 'admin/lti/platforms/', {
            'name': 'Moodle', 'issuer': 'https://moodle.example.kz', 'clientId': 'tool-123', 'deploymentIds': '1, 2',
            'authLoginUrl': 'https://moodle.example.kz/mod/lti/auth.php', 'jwksUrl': 'https://moodle.example.kz/mod/lti/certs.php',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.platform = LtiPlatform.objects.get()
        self.assertEqual(self.platform.deployment_ids, ['1', '2'])
        patcher = mock.patch.object(lti, '_platform_keys', return_value=self.jwks)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, client):
        r = client.get(API + 'lti/login/', {'iss': self.platform.issuer, 'login_hint': 'u1', 'client_id': 'tool-123',
                                            'target_link_uri': 'https://tool/'})
        self.assertEqual(r.status_code, 302)
        url = urlsplit(r['Location'])
        self.assertEqual(url.netloc, 'moodle.example.kz')
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.assertEqual((q['response_mode'], q['client_id'], q['scope']), ('form_post', 'tool-123', 'openid'))
        self.assertTrue(q['redirect_uri'].endswith('/lti/launch/'))
        return q['state'], q['nonce']

    def test_launch_creates_user_logs_in_and_opens_work(self):
        c = self.client
        state, nonce = self.login(c)
        token = lti.make_test_token(self.platform, self.pem, 'lms-1', nonce, sub='moodle-77',
                                    roles=['http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor'],
                                    custom={'work': 'vw05'}, email='Teacher@Univ.kz')
        r = c.post(API + 'lti/launch/', {'id_token': token, 'state': state})
        self.assertEqual(r.status_code, 302, r.content)
        self.assertEqual(r['Location'], '/#/virtual-labs/lab/vw05')
        link = LtiUserLink.objects.get()
        self.assertEqual((link.subject, link.context_title), ('moodle-77', 'Курс повышения квалификации'))
        user = link.user
        self.assertEqual(user.email, 'teacher@univ.kz')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(role_of(user), 'tutor')
        self.assertEqual(c.get(API + 'config/').json()['user']['role'], 'tutor')
        users = get_user_model().objects.count()
        # повтор того же state не проходит
        self.assertEqual(c.post(API + 'lti/launch/', {'id_token': token, 'state': state}).status_code, 401)
        # второй запуск того же пользователя не создаёт новую учётную запись
        state, nonce = self.login(c)
        token = lti.make_test_token(self.platform, self.pem, 'lms-1', nonce, sub='moodle-77')
        self.assertEqual(c.post(API + 'lti/launch/', {'id_token': token, 'state': state})['Location'], '/#/virtual-labs')
        self.assertEqual((get_user_model().objects.count(), LtiUserLink.objects.count()), (users, 1))

    def test_bad_tokens_rejected(self):
        state, nonce = self.login(self.client)
        other_pem, _ = platform_key()
        forged = lti.make_test_token(self.platform, other_pem, 'lms-1', nonce)
        r = self.client.post(API + 'lti/launch/', {'id_token': forged, 'state': state})
        self.assertEqual(r.status_code, 401)
        self.assertIn('Подпись', r.content.decode())

        state, nonce = self.login(self.client)
        self.assertEqual(self.client.post(API + 'lti/launch/', {
            'id_token': lti.make_test_token(self.platform, self.pem, 'lms-1', 'wrong-nonce'), 'state': state}).status_code, 401)

        state, nonce = self.login(self.client)
        LtiPlatform.objects.filter(pk=self.platform.pk).update(deployment_ids=['99'])
        self.platform.deployment_ids = ['1']
        self.assertEqual(self.client.post(API + 'lti/launch/', {
            'id_token': lti.make_test_token(self.platform, self.pem, 'lms-1', nonce), 'state': state}).status_code, 401)
        self.assertFalse(LtiUserLink.objects.exists())

    def test_unknown_platform_and_jwks(self):
        r = self.client.get(API + 'lti/login/', {'iss': 'https://unknown.example', 'login_hint': 'x'})
        self.assertEqual(r.status_code, 400)
        keys = self.client.get(API + 'lti/jwks/').json()['keys']
        self.assertEqual(keys[0]['alg'], 'RS256')
        self.assertNotIn('d', keys[0])   # закрытая часть ключа не публикуется

    def test_admin_sees_tool_urls(self):
        admin = find_users(exact='platform-admin').get()
        tool = client_for(admin).get(API + 'admin/lti/platforms/').json()['tool']
        self.assertTrue(tool['loginUrl'].endswith('/api/v1/virtual-labs/lti/login/'))
        self.assertTrue(tool['jwksUrl'].endswith('/lti/jwks/'))
        bad = client_for(admin).post(API + 'admin/lti/platforms/', {'name': 'x', 'issuer': 'i', 'clientId': 'c',
                                                                   'authLoginUrl': 'http://evil.example/a', 'jwksUrl': 'https://a/b'},
                                     format='json')
        self.assertEqual(bad.status_code, 400)


# --- Practice Engine AI edu (подменяем модули apps.activities на минимальные копии их интерфейса) ---

@dataclass(frozen=True)
class Evaluation:
    score: float
    source: str
    completed: bool
    snapshot: list
    data: dict


class FakeRegistry:
    def __init__(self):
        self.plugins = {}

    def register(self, plugin):
        key = (plugin.manifest['type'], plugin.manifest['version'])
        if key in self.plugins:
            raise ValueError(key)
        self.plugins[key] = plugin


class ChecklistPlugin:
    manifest = {'type': 'checklist_practice', 'version': '1.0.0', 'available': True, 'config_schema': {}}

    def evaluate(self, version, data):
        criteria = list(version.criteria.all())
        checks = data['checks']
        return Evaluation(sum(checks) / len(criteria), 'SELF_ASSESSMENT',
                          all(c for row, c in zip(criteria, checks) if row.is_required), [], data)


def make_assignment_plugin(checklist_class):
    class GuidedAssignmentPlugin(checklist_class):
        manifest = {**checklist_class.manifest, 'type': 'guided_assignment'}
    return GuidedAssignmentPlugin()


class FakeVersion:
    def __init__(self, required):
        rows = [types.SimpleNamespace(is_required=r) for r in required]
        self.criteria = types.SimpleNamespace(all=lambda: rows, exists=lambda: bool(rows))


class PracticePluginTests(TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        reg_mod = types.ModuleType('apps.activities.registry')
        reg_mod.ChecklistPlugin, reg_mod.Evaluation, reg_mod.registry = ChecklistPlugin, Evaluation, self.registry
        asg_mod = types.ModuleType('apps.activities.assignment')
        asg_mod.make_assignment_plugin = make_assignment_plugin
        patcher = mock.patch.dict(sys.modules, {'apps.activities': types.ModuleType('apps.activities'),
                                                'apps.activities.registry': reg_mod, 'apps.activities.assignment': asg_mod})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_registers_works_4_to_14(self):
        from ..practice import register_practices
        self.assertEqual(register_practices(), 11)
        types_ = sorted(t for t, _ in self.registry.plugins)
        self.assertEqual(types_[0], 'virtual_lab_vw04')
        self.assertEqual(types_[-1], 'virtual_lab_vw14')
        manifest = self.registry.plugins[('virtual_lab_vw05', '1.0.0')].manifest
        self.assertEqual(manifest['grading'], 'auto_grading')
        self.assertTrue(manifest['available'])
        self.assertIn('virtual_lab', manifest['capabilities'])

    def test_evaluate_is_server_side(self):
        from rest_framework.exceptions import ValidationError
        from ..practice import register_practices
        register_practices()
        plugin = self.registry.plugins[('virtual_lab_vw04', '1.0.0')]
        data = {'work': 'vw04', 'title': 'Лекция', 'text': LECTURE, 'inputs': {'topic': 'Методы активного обучения'},
                'checks': [True, False]}
        result = plugin.evaluate(FakeVersion([True, False]), data)
        self.assertEqual(result.source, 'AUTO_GRADING')
        self.assertTrue(0 < result.score <= 1)
        self.assertEqual(round(result.score * 100), result.data['evaluation']['total'])
        self.assertEqual(len(result.data['evaluation']['criteria']), 10)
        with self.assertRaises(ValidationError):
            plugin.evaluate(FakeVersion([True]), {**data, 'checks': [False]})       # обязательный критерий не отмечен
        with self.assertRaises(ValidationError):
            plugin.evaluate(FakeVersion([]), {**data, 'work': 'vw05'})              # чужая работа
        with self.assertRaises(ValidationError):
            plugin.evaluate(FakeVersion([]), {**data, 'score': 1})                  # балл с клиента не принимается
        self.assertEqual(plugin.evaluate(FakeVersion([]), {**data, 'checks': []}).source, 'AUTO_GRADING')
