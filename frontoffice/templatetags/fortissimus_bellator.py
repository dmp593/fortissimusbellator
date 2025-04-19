from django import template
from django.contrib.staticfiles.storage import staticfiles_storage


register = template.Library()


@register.filter
def static_url(filepath):
    return staticfiles_storage.url(filepath)
