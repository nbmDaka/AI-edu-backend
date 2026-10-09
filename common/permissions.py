from rest_framework.permissions import BasePermission

def is_admin(user):
    return user.is_authenticated and user.is_active and (user.is_superuser or user.role == 'ADMIN')

class IsPlatformAdmin(BasePermission):
    def has_permission(self, request, view):
        return is_admin(request.user)
