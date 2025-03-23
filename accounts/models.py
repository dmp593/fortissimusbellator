from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _


class Profile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile',
        related_query_name='profile',
        verbose_name=_('user')
    )

    phone = models.CharField(
        max_length=15,
        verbose_name=_('phone number')
    )

    def __str__(self):
        return f"{self.user.username}"
