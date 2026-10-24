import logging
import uuid

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)


def send_account_email(user, *, title, body, action, url, note='', key=None):
    context = {'title': title, 'name': user.first_name, 'body': body, 'action': action,
               'url': url, 'note': note, 'site_url': settings.PUBLIC_SITE_URL}
    message = EmailMultiAlternatives(
        subject=f'{title} — AI Edu', body=render_to_string('accounts/email.txt', context),
        from_email=settings.DEFAULT_FROM_EMAIL, to=[user.email],
        headers={'Idempotency-Key': key or f'account/{uuid.uuid4()}'},
    )
    message.attach_alternative(render_to_string('accounts/email.html', context), 'text/html')
    try:
        message.send(fail_silently=False)
    except Exception as error:
        # Do not log message bodies, reset links, email addresses or provider credentials.
        logger.error('Account email delivery failed for user_id=%s (%s)', user.pk, type(error).__name__)
        return False
    return True


EMAIL_VERIFICATION_SALT = 'accounts.email-verification'


def send_verification_email(user):
    token = signing.dumps({'user_id': user.pk, 'email': user.email}, salt=EMAIL_VERIFICATION_SALT)
    return send_account_email(user, title='Подтвердите email',
        body='Спасибо за регистрацию в AI Edu! Подтвердите адрес электронной почты, чтобы войти в аккаунт и начать обучение.',
        action='Подтвердить email', url=f'{settings.PUBLIC_SITE_URL}/#/verify-email/{token}',
        note=f'Ссылка действует {settings.EMAIL_VERIFICATION_TIMEOUT // 3600} ч. '
             'Если вы не регистрировались на платформе, просто проигнорируйте это письмо.')


def send_password_reset_email(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return send_account_email(user, title='Восстановление пароля',
        body='Мы получили запрос на сброс пароля вашего аккаунта AI Edu. Нажмите кнопку, чтобы задать новый пароль.',
        action='Задать новый пароль', url=f'{settings.PUBLIC_SITE_URL}/#/reset-password/{uid}/{token}',
        note=f'Ссылка действует {settings.PASSWORD_RESET_TIMEOUT // 60} мин. и может быть использована один раз. '
             'Если вы не запрашивали сброс пароля, просто проигнорируйте это письмо.')

