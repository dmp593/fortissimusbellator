from django.db.models.signals import post_save
from django.dispatch import receiver

from breeding.models import Litter
from breeding.services.litter_alerts import queue_birth_announcement


@receiver(post_save, sender=Litter)
def queue_litter_birth_alerts(sender, instance, **kwargs):
    queue_birth_announcement(instance.pk)
