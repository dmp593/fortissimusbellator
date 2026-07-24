"""
ASGI config for fortissimusbellator project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fortissimusbellator.settings')


def _build_application():
    django_application = get_asgi_application()

    from chat.runtime import get_chat_runtime

    get_chat_runtime().warm_up()
    return django_application


application = _build_application()
