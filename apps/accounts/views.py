import hashlib
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_protect
from django.utils.decorators import method_decorator
from django.utils.http import urlsafe_base64_decode
from django.utils import timezone
from rest_framework import status
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.generics import GenericAPIView
from rest_framework.exceptions import Throttled, ValidationError
from rest_framework import serializers
from .serializers import RegisterSerializer, UserSerializer, LoginSerializer, PasswordSerializer, ProfileUpdateSerializer
from .serializers import PasswordResetRequestSerializer, PasswordResetConfirmSerializer, AuthMessageSerializer
from .serializers import EmailVerificationSerializer, RegistrationResponseSerializer
from .models import User
from .emails import send_password_reset_email, send_verification_email, EMAIL_VERIFICATION_SALT
from common.auth import EmailNotVerified

def rate_limit(request, scope, limit=10, by_email=True):
    email = request.data.get('email', '') if by_email and hasattr(request.data, 'get') else ''
    identity = request.META.get('REMOTE_ADDR', '') + ':' + str(email).lower()
    key = 'auth:' + scope + ':' + hashlib.sha256(identity.encode()).hexdigest()
    if cache.add(key, 1, timeout=300):
        return
    if cache.incr(key) > limit:
        raise Throttled(wait=300)

class CsrfSerializer(serializers.Serializer):
    csrfToken = serializers.CharField()

@method_decorator(ensure_csrf_cookie, name='dispatch')
class CsrfView(GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = CsrfSerializer
    def get(self, request):
        return Response({'csrfToken': get_token(request)})

@method_decorator(csrf_protect, name='dispatch')
class RegisterView(GenericAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    @extend_schema(responses={201: RegistrationResponseSerializer})
    def post(self, request):
        rate_limit(request, 'register', 5)
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        send_verification_email(user)
        return Response({**UserSerializer(user).data, 'verification_required': True,
            'detail': 'Подтвердите email по ссылке из письма, чтобы войти в аккаунт.'}, status=status.HTTP_201_CREATED)

@method_decorator(csrf_protect, name='dispatch')
class LoginView(GenericAPIView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rate_limit(request, 'login')
        user = authenticate(request, email=serializer.validated_data['email'].lower(), password=serializer.validated_data['password'])
        if not user:
            raise ValidationError({'credentials': 'Неверный email или пароль'})
        if user.email_verification_required and not user.email_verified_at:
            raise EmailNotVerified()
        login(request, user)
        return Response(UserSerializer(user).data)

@method_decorator(csrf_protect, name='dispatch')
class LogoutView(GenericAPIView):
    serializer_class = UserSerializer
    permission_classes = [AllowAny]
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)

class ProfileView(GenericAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    def get(self, request):
        return Response(UserSerializer(request.user).data)
    @extend_schema(request=ProfileUpdateSerializer, responses=UserSerializer)
    def patch(self, request):
        serializer = ProfileUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data)

class PasswordView(GenericAPIView):
    serializer_class = PasswordSerializer
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data['current_password']):
            raise ValidationError({'current_password': 'Неверный пароль'})
        password = serializer.validated_data['new_password']
        validate_password(password, user)
        user.set_password(password)
        user.save(update_fields=['password'])
        update_session_auth_hash(request, user)
        return Response(status=status.HTTP_204_NO_CONTENT)


def limit_account_emails(request, email, scope):
    # Separate IP and mailbox budgets prevent bypassing limits by rotating email/IP pairs.
    for kind, identity, limit in [('ip', request.META.get('REMOTE_ADDR', ''), 20), ('email', email.lower(), 3)]:
        key = 'auth:' + scope + ':' + kind + ':' + hashlib.sha256(identity.encode()).hexdigest()
        if cache.add(key, 1, timeout=300):
            continue
        try:
            count = cache.incr(key)
        except ValueError:  # The cache key can expire between add() and incr().
            cache.add(key, 1, timeout=300)
            continue
        if count > limit:
            raise Throttled(wait=300)


@method_decorator(csrf_protect, name='dispatch')
class PasswordResetRequestView(GenericAPIView):
    serializer_class = PasswordResetRequestSerializer
    permission_classes = [AllowAny]

    @extend_schema(responses=AuthMessageSerializer)
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email'].lower()
        limit_account_emails(request, email, 'password-reset')
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user and user.has_usable_password():
            send_password_reset_email(user)
        # Identical responses for unknown, archived and disabled accounts.
        return Response({'detail': 'Если аккаунт с таким email существует, мы отправили письмо со ссылкой для сброса пароля.'})


@method_decorator(csrf_protect, name='dispatch')
class PasswordResetConfirmView(GenericAPIView):
    serializer_class = PasswordResetConfirmSerializer
    permission_classes = [AllowAny]

    @extend_schema(responses={204: None})
    def post(self, request):
        rate_limit(request, 'password-reset-confirm', 20, by_email=False)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            try:
                uid = urlsafe_base64_decode(data['uid']).decode()
                user = User.objects.select_for_update().get(pk=uid, is_active=True)
            except (User.DoesNotExist, ValueError, TypeError, OverflowError, UnicodeDecodeError):
                user = None
            if not user or not user.has_usable_password() or not default_token_generator.check_token(user, data['token']):
                raise ValidationError({'token': 'Ссылка недействительна или срок её действия истёк. Запросите новое письмо.'})
            try:
                validate_password(data['new_password'], user)
            except DjangoValidationError as error:
                raise ValidationError({'new_password': error.messages})
            user.set_password(data['new_password'])
            user.save(update_fields=['password'])
        # Password hashes invalidate every existing session and this reset token.
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_protect, name='dispatch')
class EmailVerificationView(GenericAPIView):
    serializer_class = EmailVerificationSerializer
    permission_classes = [AllowAny]

    @extend_schema(responses=AuthMessageSerializer)
    def post(self, request):
        rate_limit(request, 'verify-email', 20, by_email=False)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = signing.loads(serializer.validated_data['token'], salt=EMAIL_VERIFICATION_SALT,
                                    max_age=settings.EMAIL_VERIFICATION_TIMEOUT)
        except signing.BadSignature:
            raise ValidationError({'token': 'Ссылка недействительна или срок её действия истёк. Запросите новое письмо.'})
        with transaction.atomic():
            user = User.objects.select_for_update().filter(pk=payload['user_id'], email=payload['email'],
                                                          is_active=True, email_verification_required=True).first()
            if not user:
                raise ValidationError({'token': 'Ссылка недействительна или срок её действия истёк. Запросите новое письмо.'})
            if not user.email_verified_at:
                user.email_verified_at = timezone.now()
                user.save(update_fields=['email_verified_at'])
        return Response({'detail': 'Email подтверждён. Теперь вы можете войти в аккаунт.'})


@method_decorator(csrf_protect, name='dispatch')
class EmailVerificationResendView(GenericAPIView):
    serializer_class = PasswordResetRequestSerializer
    permission_classes = [AllowAny]

    @extend_schema(responses=AuthMessageSerializer)
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email'].lower()
        limit_account_emails(request, email, 'verify-email-resend')
        user = User.objects.filter(email__iexact=email, is_active=True, email_verification_required=True,
                                   email_verified_at__isnull=True).first()
        if user:
            send_verification_email(user)
        return Response({'detail': 'Если email ожидает подтверждения, мы отправили новое письмо.'})
