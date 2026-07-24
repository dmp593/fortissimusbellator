"""
WSGI config for fortissimusbellator project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fortissimusbellator.settings')


def _build_application():
    django_application = get_wsgi_application()

    from chat.runtime import get_chat_runtime

    get_chat_runtime().warm_up()
    return django_application


application = _build_application()
