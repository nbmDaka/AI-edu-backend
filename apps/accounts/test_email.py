import json
import time
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import mail, signing
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from common.email import ResendEmailBackend, EmailDeliveryError
from .emails import EMAIL_VERIFICATION_SALT
from .models import User


@override_settings(SECURE_SSL_REDIRECT=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PUBLIC_SITE_URL='https://aiedu.com.kz', PASSWORD_RESET_TIMEOUT=3600,
                   EMAIL_VERIFICATION_TIMEOUT=86400,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AccountEmailTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.get('/api/v1/csrf/')
        self.user = User.objects.create_user('existing@example.test', 'OldStrongWord842!')

    def post(self, path, data, **headers):
        return self.client.post('/api/v1/auth/' + path, data, format='json',
            HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value, **headers)

    def register(self):
        response = self.post('register/', {'email': 'NEW@example.test', 'password': 'NewStrongWord842!',
                                           'first_name': '<script>alert(1)</script>'})
        self.assertEqual(response.status_code, 201, response.data)
        return User.objects.get(pk=response.data['id'])

    def verification_token(self):
        return mail.outbox[-1].body.split('/#/verify-email/')[1].split()[0]

    def reset_data(self, user=None):
        user = user or self.user
        return {'uid': urlsafe_base64_encode(force_bytes(user.pk)),
                'token': default_token_generator.make_token(user), 'new_password': '  ChangedStrongWord963!  '}

    def test_registration_sends_branded_verification_without_starting_a_session(self):
        user = self.register()
        self.assertTrue(user.email_verification_required)
        self.assertIsNone(user.email_verified_at)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['new@example.test'])
        self.assertEqual(message.from_email, 'AI Edu <noreply@aiedu.com.kz>')
        self.assertNotIn('NewStrongWord842!', message.body)
        self.assertIn('https://aiedu.com.kz/#/verify-email/', message.body)
        self.assertIn('&lt;script&gt;', message.alternatives[0].content)
        self.assertNotIn('<script>', message.alternatives[0].content)
        blocked = self.post('login/', {'email': user.email, 'password': 'NewStrongWord842!'})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.data['error']['code'], 'email_not_verified')
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 403)

    def test_verification_enables_login_and_is_idempotent_without_automatic_login(self):
        user = self.register()
        token = self.verification_token()
        for _ in range(2):
            self.assertEqual(self.post('verify-email/', {'token': token}).status_code, 200)
        user.refresh_from_db()
        self.assertIsNotNone(user.email_verified_at)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.post('login/', {'email': user.email, 'password': 'NewStrongWord842!'}).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 200)

    def test_existing_accounts_and_administrator_created_accounts_keep_access(self):
        self.assertFalse(self.user.email_verification_required)
        self.assertEqual(self.post('login/', {'email': self.user.email, 'password': 'OldStrongWord842!'}).status_code, 200)

    def test_unverified_sessions_cannot_bypass_the_login_gate(self):
        user = self.register()
        self.client.force_login(user)
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 403)

    def test_verification_rejects_tampered_expired_and_wrong_purpose_tokens(self):
        user = self.register()
        token = self.verification_token()
        for invalid in [token + 'x', default_token_generator.make_token(user), 'invalid']:
            self.assertEqual(self.post('verify-email/', {'token': invalid}).status_code, 400)
        future = time.time() + 86401
        with patch('django.core.signing.time.time', return_value=future):
            self.assertEqual(self.post('verify-email/', {'token': token}).status_code, 400)
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)

    def test_verification_does_not_restore_disabled_or_archived_accounts(self):
        user = self.register()
        token = self.verification_token()
        User.objects.filter(pk=user.pk).update(is_active=False)
        self.assertEqual(self.post('verify-email/', {'token': token}).status_code, 400)
        User.objects.filter(pk=user.pk).update(is_active=True)
        user.delete()
        self.assertEqual(self.post('verify-email/', {'token': token}).status_code, 400)

    def test_resend_is_generic_and_limited_by_mailbox_across_different_ips(self):
        user = self.register()
        for index in range(3):
            response = self.post('verify-email/resend/', {'email': user.email}, REMOTE_ADDR=f'10.0.0.{index}')
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post('verify-email/resend/', {'email': user.email}, REMOTE_ADDR='10.0.0.9').status_code, 429)
        before = len(mail.outbox)
        missing = self.post('verify-email/resend/', {'email': 'missing@example.test'})
        existing = self.post('verify-email/resend/', {'email': self.user.email})
        self.assertEqual(missing.data, existing.data)
        self.assertEqual(len(mail.outbox), before)

    def test_reset_email_uses_a_trusted_link_and_does_not_expose_account_existence(self):
        known = self.post('password-reset/', {'email': self.user.email.upper()})
        self.assertEqual(known.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn('https://aiedu.com.kz/#/reset-password/', message.body)
        self.assertNotIn('OldStrongWord842!', message.body)
        uid, token = message.body.split('/#/reset-password/')[1].split()[0].split('/')
        self.assertEqual(uid, urlsafe_base64_encode(force_bytes(self.user.pk)))
        self.assertTrue(default_token_generator.check_token(self.user, token))
        unknown = self.post('password-reset/', {'email': 'missing@example.test'})
        self.assertEqual(known.data, unknown.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_reset_ignores_inactive_archived_and_unusable_password_accounts(self):
        for email, state in [('disabled@example.test', 'disabled'), ('archived@example.test', 'archived'), ('unusable@example.test', 'unusable')]:
            user = User.objects.create_user(email, 'OldStrongWord842!')
            if state == 'disabled':
                user.is_active = False; user.save(update_fields=['is_active'])
            elif state == 'archived':
                user.delete()
            else:
                user.set_unusable_password(); user.save(update_fields=['password'])
            self.assertEqual(self.post('password-reset/', {'email': email}).status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_preserves_whitespace_invalidates_sessions_and_cannot_be_replayed(self):
        other = APIClient()
        other.force_login(self.user)
        data = self.reset_data()
        self.assertEqual(self.post('password-reset/confirm/', data).status_code, 204)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(data['new_password']))
        self.assertEqual(other.get('/api/v1/auth/me/').status_code, 403)
        self.assertEqual(self.post('password-reset/confirm/', data).status_code, 400)
        self.assertEqual(self.post('login/', {'email': self.user.email, 'password': data['new_password']}).status_code, 200)

    def test_reset_rejects_weak_expired_tampered_and_invalid_uid_tokens(self):
        data = self.reset_data()
        for invalid in [{**data, 'new_password': '123'}, {**data, 'token': 'wrong'}, {**data, 'uid': 'invalid'},
                        {**data, 'uid': urlsafe_base64_encode(b'9' * 80)}]:
            self.assertEqual(self.post('password-reset/confirm/', invalid).status_code, 400)
        with patch.object(default_token_generator, '_now', return_value=datetime.now() + timedelta(seconds=3601)):
            self.assertEqual(self.post('password-reset/confirm/', data).status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldStrongWord842!'))

    def test_password_reset_does_not_bypass_email_verification(self):
        user = self.register()
        data = self.reset_data(user)
        self.assertEqual(self.post('password-reset/confirm/', data).status_code, 204)
        self.assertEqual(self.post('login/', {'email': user.email, 'password': data['new_password']}).status_code, 403)

    def test_email_endpoints_require_csrf(self):
        for path, payload in [('verify-email/', {'token': 'token'}), ('verify-email/resend/', {'email': self.user.email}),
                              ('password-reset/', {'email': self.user.email}), ('password-reset/confirm/', self.reset_data())]:
            self.assertEqual(self.client.post('/api/v1/auth/' + path, payload, format='json').status_code, 403)

    def test_reset_request_limits_both_mailbox_and_ip(self):
        for _ in range(3):
            self.assertEqual(self.post('password-reset/', {'email': self.user.email}).status_code, 200)
        self.assertEqual(self.post('password-reset/', {'email': self.user.email}).status_code, 429)
        cache.clear()
        for index in range(20):
            self.assertEqual(self.post('password-reset/', {'email': f'unknown{index}@example.test'}).status_code, 200)
        self.assertEqual(self.post('password-reset/', {'email': 'last@example.test'}).status_code, 429)

    @override_settings(EMAIL_BACKEND='common.email.ResendEmailBackend', RESEND_API_KEY='')
    def test_delivery_failure_does_not_lose_registration_or_leak_known_accounts(self):
        with self.assertLogs('apps.accounts.emails', level='ERROR') as logs:
            user = self.register()
            known = self.post('password-reset/', {'email': self.user.email})
        unknown = self.post('password-reset/', {'email': 'missing@example.test'})
        self.assertEqual(known.data, unknown.data)
        self.assertTrue(user.email_verification_required)
        self.assertNotIn(user.email, '\n'.join(logs.output))


@override_settings(RESEND_API_KEY='re_test_key', EMAIL_TIMEOUT=10)
class ResendBackendTests(SimpleTestCase):
    def message(self):
        message = EmailMultiAlternatives('Тема', 'Текст', settings.DEFAULT_FROM_EMAIL, ['student@example.test'],
                                          headers={'Idempotency-Key': 'test/email-1'})
        message.attach_alternative('<p>Текст</p>', 'text/html')
        return message

    @patch('common.email.urlopen')
    def test_sends_authenticated_https_request_with_plain_text_and_html(self, send):
        send.return_value.__enter__.return_value = BytesIO(b'{"id":"email-1"}')
        self.assertEqual(ResendEmailBackend().send_messages([self.message()]), 1)
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://api.resend.com/emails')
        self.assertEqual(request.get_header('Authorization'), 'Bearer re_test_key')
        self.assertEqual(request.get_header('Idempotency-key'), 'test/email-1')
        self.assertEqual(payload['from'], 'AI Edu <noreply@aiedu.com.kz>')
        self.assertEqual(payload['text'], 'Текст')
        self.assertEqual(payload['html'], '<p>Текст</p>')
        self.assertEqual(send.call_args.kwargs['timeout'], 10)

    @patch('common.email.urlopen')
    def test_provider_errors_are_sanitized_and_fail_silently_is_respected(self, send):
        send.side_effect = HTTPError('https://api.resend.com/emails', 429, 'sensitive provider message', {}, None)
        with self.assertRaisesMessage(EmailDeliveryError, 'HTTP 429'):
            ResendEmailBackend().send_messages([self.message()])
        self.assertEqual(ResendEmailBackend(fail_silently=True).send_messages([self.message()]), 0)
        send.side_effect = URLError('private detail')
        with self.assertRaisesMessage(EmailDeliveryError, 'Resend email request failed'):
            ResendEmailBackend().send_messages([self.message()])

    @patch('common.email.urlopen')
    def test_missing_credentials_never_call_the_provider(self, send):
        with override_settings(RESEND_API_KEY=''):
            self.assertEqual(ResendEmailBackend(fail_silently=True).send_messages([self.message()]), 0)
        send.assert_not_called()
