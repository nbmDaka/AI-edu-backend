import io
import time
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from .avatar import TutorAvatarConfigView, TutorAvatarModelView, avatar_allowed, private_gateway_url


@override_settings(AI_AVATAR_ENABLED=True, AI_AVATAR_SERVICE_URL='http://aiedu-avatar-gateway:5185',
    AI_SERVICE_API_KEY='test-key', AI_AVATAR_ALLOWED_USERS=('teacher@example.test',),
    AI_AVATAR_EXPIRES_AT=4102444800, AI_AVATAR_MODEL_KEY='platform-assets/avatar/test.glb', AI_AVATAR_MODEL_VERSION='a' * 64)
class AvatarAccessTests(SimpleTestCase):
    def request(self, view, authenticated=True, version='a' * 64):
        request = APIRequestFactory().get('/?v=' + version)
        if authenticated:
            force_authenticate(request, user=SimpleNamespace(pk=1, email='teacher@example.test', is_authenticated=True))
        return view.as_view()(request)

    def test_only_exact_private_gateway_is_accepted(self):
        self.assertEqual(private_gateway_url(), 'http://aiedu-avatar-gateway:5185')
        for url in ('https://external.example:5185', 'http://127.0.0.1:5185', 'http://aiedu-avatar-gateway:80',
                    'http://user:pass@aiedu-avatar-gateway:5185', 'http://aiedu-avatar-gateway:5185/other', 'http://[invalid'):
            with override_settings(AI_AVATAR_SERVICE_URL=url):
                self.assertIsNone(private_gateway_url())

    def test_expiry_and_allowlist_fail_closed(self):
        user = SimpleNamespace(pk=1, email='teacher@example.test', is_authenticated=True)
        self.assertTrue(avatar_allowed(user))
        for settings in ({'AI_AVATAR_EXPIRES_AT': int(time.time()) - 1}, {'AI_AVATAR_ALLOWED_USERS': ()}, {'AI_AVATAR_ENABLED': False}):
            with override_settings(**settings):
                self.assertFalse(avatar_allowed(user))

    @patch('apps.tutor.avatar.default_storage')
    def test_assets_require_authentication(self, storage):
        self.assertIn(self.request(TutorAvatarConfigView, False).status_code, (401, 403))
        self.assertIn(self.request(TutorAvatarModelView, False).status_code, (401, 403))
        storage.open.assert_not_called()

    @patch('apps.tutor.avatar.default_storage')
    def test_configuration_contains_no_private_key_or_upstream_url(self, storage):
        storage.exists.return_value = True
        response = self.request(TutorAvatarConfigView)
        self.assertTrue(response.data['enabled'])
        self.assertNotIn('aiedu-avatar-gateway', str(response.data))
        self.assertNotIn('test-key', str(response.data))
        self.assertEqual(response.data['voice'], 'cedar')

    @patch('apps.tutor.avatar.default_storage')
    def test_model_is_size_bounded_and_versioned(self, storage):
        storage.size.return_value = 200000
        storage.open.return_value = io.BytesIO(b'glTF' + bytes(199996))
        response = self.request(TutorAvatarModelView)
        self.assertEqual(response.status_code, 200)
        self.assertIn('private', response['Cache-Control'])
        response.close()
        self.assertEqual(self.request(TutorAvatarModelView, version='b' * 64).status_code, 404)
        storage.size.return_value = 20 * 1024 * 1024
        self.assertEqual(self.request(TutorAvatarModelView).status_code, 404)
