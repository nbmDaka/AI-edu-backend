"""Explicit test profile; never use this module to serve the application."""

from .settings import *  # noqa: F403

# Tests still exercise password verification without production hashing costs.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
