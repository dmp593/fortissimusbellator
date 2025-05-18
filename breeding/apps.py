from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BreedingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'breeding'
    verbose_name = _('breeding')
