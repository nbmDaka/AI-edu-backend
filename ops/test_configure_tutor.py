import os
import stat
import tempfile
import unittest
from pathlib import Path

from configure_tutor import configure, read_values


class ConfigureTutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.original = ('# Keep server settings\nDATABASE_PASSWORD=not-a-real-password\n'
                         'AI_BASE_URL=https://api.openai.com/v1\nAI_API_KEY=old\n')
        (self.root / '.env').write_text(self.original, encoding='utf-8')
        self.key = 'sk-test-' + 'x' * 40

    def test_writes_matching_service_keys_and_preserves_backend_data(self):
        configure(self.root, self.key)
        backend, text = read_values(self.root / '.env')
        gateway, _ = read_values(self.root / '.env.ai-tutor')
        self.assertEqual(backend['DATABASE_PASSWORD'], 'not-a-real-password')
        self.assertIn('# Keep server settings', text)
        self.assertEqual(backend['AI_API_KEY'], self.key)
        self.assertEqual(gateway['OPENAI_API_KEY'], self.key)
        self.assertEqual(backend['AI_SERVICE_API_KEY'], gateway['API_SHARED_KEY'])
        self.assertEqual(backend['AI_SERVICE_URL'], 'http://ai-tutor:3001')
        self.assertEqual((self.root / '.env.before-tutor').read_text(), self.original)
        if os.name != 'nt':
            for name in ('.env', '.env.ai-tutor', '.env.before-tutor'):
                self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600)

    def test_rotation_keeps_service_identity_custom_limits_and_original_backup(self):
        configure(self.root, self.key)
        gateway = self.root / '.env.ai-tutor'
        gateway.write_text(gateway.read_text().replace('AI_RPM_LIMIT=30', 'AI_RPM_LIMIT=12'))
        before, _ = read_values(gateway)
        configure(self.root, 'sk-new-' + 'y' * 40)
        after, _ = read_values(gateway)
        self.assertEqual(after['API_SHARED_KEY'], before['API_SHARED_KEY'])
        self.assertEqual(after['AI_RPM_LIMIT'], '12')
        self.assertEqual(after['OPENAI_API_KEY'], 'sk-new-' + 'y' * 40)
        self.assertEqual((self.root / '.env.before-tutor').read_text(), self.original)

    def test_missing_key_does_not_change_server_files(self):
        for key in ('', 'replace-with-key', 'sk-value\nINJECTED=true'):
            with self.assertRaises(ValueError):
                configure(self.root, key)
        self.assertEqual((self.root / '.env').read_text(), self.original)
        self.assertFalse((self.root / '.env.ai-tutor').exists())

    def test_other_provider_key_is_not_overwritten(self):
        env = self.root / '.env'
        env.write_text(self.original.replace('https://api.openai.com/v1', 'https://provider.test/v1'))
        configure(self.root, self.key)
        self.assertEqual(read_values(env)[0]['AI_API_KEY'], 'old')

    @unittest.skipIf(os.name == 'nt', 'Windows requires extra privileges for symlinks')
    def test_symlink_is_rejected_before_writing_anything(self):
        target = self.root / 'other.env'
        target.write_text('private=yes\n')
        (self.root / '.env.ai-tutor').symlink_to(target)
        with self.assertRaises(ValueError):
            configure(self.root, self.key)
        self.assertEqual(target.read_text(), 'private=yes\n')
        self.assertEqual((self.root / '.env').read_text(), self.original)
