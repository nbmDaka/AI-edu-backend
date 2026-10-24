from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import PermissionDenied


class EmailNotVerified(PermissionDenied):
    default_code = 'email_not_verified'
    default_detail = 'Подтвердите email по ссылке из письма, чтобы войти в аккаунт.'


class VerifiedSessionAuthentication(SessionAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            user, _ = result
            if user.email_verification_required and not user.email_verified_at:
                raise EmailNotVerified()
        return result

class CsrfSessionAuthentication(SessionAuthentication):
    def authenticate(self, request):
        self.enforce_csrf(request)
        return super().authenticate(request)


# Register after authenticators exist: DRF resolves these classes while loading settings.
from drf_spectacular.authentication import SessionScheme  # noqa: E402


class VerifiedSessionScheme(SessionScheme):
    target_class = VerifiedSessionAuthentication
