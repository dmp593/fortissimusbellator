from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from breeding.models import Animal, Litter

from .models import AnimalSaleCase, PreReservation


@receiver(pre_delete, sender=Animal)
def preserve_deleted_animal_history(sender, instance, **kwargs):
    deleted_at = timezone.now()
    PreReservation.objects.filter(animal=instance).update(
        target_deleted_at=deleted_at,
    )
    AnimalSaleCase.objects.filter(animal=instance).update(
        target_deleted_at=deleted_at,
    )


@receiver(pre_delete, sender=Litter)
def preserve_deleted_litter_history(sender, instance, **kwargs):
    PreReservation.objects.filter(litter=instance).update(
        target_deleted_at=timezone.now()
    )
