from django.core.cache import cache
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.education.models import LearningTrack
from .models import User


@override_settings(
    SECURE_SSL_REDIRECT=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class AuthSessionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.track = LearningTrack.objects.create(title='IT', is_published=True)
        cls.user = User.objects.create_user('auth@example.test', 'StrongPass321!', learning_track=cls.track)

    def setUp(self):
        cache.clear()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.get('/api/v1/csrf/')

    def post(self, path, data=None):
        return self.client.post('/api/v1/' + path, data, format='json',
            HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)

    def login(self, password='StrongPass321!'):
        return self.post('auth/login/', {'email': self.user.email.upper(), 'password': password})

    def test_login_logout_and_relogin_with_rotated_csrf(self):
        old_token = self.client.cookies['csrftoken'].value
        self.assertEqual(self.login().status_code, 200)
        self.assertNotEqual(self.client.cookies['csrftoken'].value, old_token)
        self.assertEqual(self.client.get('/api/v1/auth/me/').data['id'], self.user.pk)
        self.assertEqual(self.client.post('/api/v1/auth/logout/', HTTP_X_CSRFTOKEN=old_token).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 200)
        self.assertEqual(self.post('auth/logout/').status_code, 204)
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 403)
        self.assertEqual(self.login().status_code, 200)

    def test_logout_is_idempotent_after_session_ends(self):
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.post('auth/logout/').status_code, 204)
        self.assertEqual(self.post('auth/logout/').status_code, 204)
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 403)

    def test_logout_without_csrf_is_rejected_for_anonymous_and_authenticated_users(self):
        self.assertEqual(self.client.post('/api/v1/auth/logout/').status_code, 403)
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.post('/api/v1/auth/logout/').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 200)

    def test_logout_works_after_account_is_deactivated(self):
        self.assertEqual(self.login().status_code, 200)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 403)
        self.assertEqual(self.post('auth/logout/').status_code, 204)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.login().status_code, 400)

    def test_password_whitespace_is_preserved_on_registration_login_and_change(self):
        password = '  NewStrongWord842!  '
        response = self.post('auth/register/', {'email': 'spaces@example.test', 'password': password,
            'learning_track': self.track.pk})
        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(pk=response.data['id'])
        self.assertTrue(user.check_password(password))
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 403)
        token = mail.outbox[-1].body.split('/#/verify-email/')[1].split()[0]
        self.assertEqual(self.post('auth/verify-email/', {'token': token}).status_code, 200)
        self.assertEqual(self.post('auth/logout/').status_code, 204)
        self.assertEqual(self.post('auth/login/', {'email': user.email, 'password': password.strip()}).status_code, 400)
        self.assertEqual(self.post('auth/login/', {'email': user.email, 'password': password}).status_code, 200)
        new_password = '  ChangedStrongWord963!  '
        self.assertEqual(self.post('auth/password/', {'current_password': password, 'new_password': new_password}).status_code, 204)
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 200)
        self.assertEqual(self.post('auth/logout/').status_code, 204)
        self.assertEqual(self.post('auth/login/', {'email': user.email, 'password': new_password}).status_code, 200)


@override_settings(
    DEBUG=False,
    ALLOWED_HOSTS=['aiedu.com.kz', 'localhost', '127.0.0.1', 'backend'],
    CSRF_TRUSTED_ORIGINS=['https://aiedu.com.kz'],
    SECURE_SSL_REDIRECT=True,
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class ProductionDomainAuthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('domain@example.test', 'StrongPass321!')

    def setUp(self):
        cache.clear()
        self.client = APIClient(enforce_csrf_checks=True)
        self.headers = {'HTTP_HOST': 'aiedu.com.kz', 'HTTP_X_FORWARDED_PROTO': 'https'}

    def test_https_proxy_supports_csrf_login_and_session_on_production_domain(self):
        response = self.client.get('/api/v1/csrf/', **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.cookies['csrftoken']['secure'])
        response = self.client.post('/api/v1/auth/login/',
            {'email': self.user.email, 'password': 'StrongPass321!'}, format='json',
            HTTP_ORIGIN='https://aiedu.com.kz',
            HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value, **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.cookies['sessionid']['secure'])
        self.assertEqual(self.client.get('/api/v1/auth/me/', **self.headers).data['id'], self.user.pk)

    def test_unknown_domain_is_rejected(self):
        response = self.client.get('/api/v1/csrf/', HTTP_HOST='untrusted.example', HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(response.status_code, 400)

    def test_untrusted_origin_is_rejected_even_with_valid_csrf_token(self):
        self.client.get('/api/v1/csrf/', **self.headers)
        response = self.client.post('/api/v1/auth/login/',
            {'email': self.user.email, 'password': 'StrongPass321!'}, format='json',
            HTTP_ORIGIN='https://untrusted.example',
            HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value, **self.headers)
        self.assertEqual(response.status_code, 403)
