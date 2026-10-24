"""Django email transport using Resend's HTTPS API."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend


class EmailDeliveryError(RuntimeError):
    pass


class ResendEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages or []:
            if not message.recipients():
                continue
            try:
                self._send(message)
            except (EmailDeliveryError, ImproperlyConfigured):
                if not self.fail_silently:
                    raise
            else:
                sent += 1
        return sent

    def _send(self, message):
        if not settings.RESEND_API_KEY:
            raise ImproperlyConfigured('RESEND_API_KEY is required to send account emails')
        payload = {'from': message.from_email, 'to': message.to, 'subject': message.subject}
        if message.content_subtype == 'html':
            payload['html'] = message.body
        else:
            payload['text'] = message.body
        for alternative in getattr(message, 'alternatives', []):
            if alternative.mimetype == 'text/html':
                payload['html'] = alternative.content
        for field in ('cc', 'bcc', 'reply_to'):
            if getattr(message, field):
                payload[field] = getattr(message, field)
        headers = {'Authorization': f'Bearer {settings.RESEND_API_KEY}',
                   'Content-Type': 'application/json', 'User-Agent': 'AI-Edu/1.0'}
        if key := message.extra_headers.get('Idempotency-Key'):
            headers['Idempotency-Key'] = key
        request = Request('https://api.resend.com/emails', data=json.dumps(payload).encode('utf-8'),
                          headers=headers, method='POST')
        try:
            with urlopen(request, timeout=settings.EMAIL_TIMEOUT) as response:
                result = json.load(response)
        except HTTPError as error:
            code = error.code
            error.close()
            # Provider response bodies can contain recipient data; never expose or log them.
            raise EmailDeliveryError(f'Resend rejected the email (HTTP {code})') from None
        except (URLError, OSError, ValueError):
            raise EmailDeliveryError('Resend email request failed') from None
        if not isinstance(result, dict) or not result.get('id'):
            raise EmailDeliveryError('Resend did not return an email ID')

