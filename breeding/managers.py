from django.db import models
from fortissimusbellator.managers import Manager


class GetByNameManager(Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class AnimalsForSaleManager(GetByNameManager):
    def get_queryset(self):
        return super().get_queryset().filter(
            active=True, for_sale=True
        ).order_by(
            models.Case(
                models.When(sold_at__isnull=True, then=models.Value(0)),
                default=models.Value(1)
            ),
            'order'
        )


class AnimalKindManager(Manager):
    def get_by_natural_key(self, name: str) -> 'AnimalKind':
        return self.get(name=name)


class BreedManager(Manager):
    def get_by_natural_key(
        self, name: str, parent: str | None = None, kind: str | None = None
    ) -> 'Breed':
        if not kind:
            return self.get(name=name)

        return self.get(name=name, parent__name=parent, kind__name=kind)


class SpecificBreedManager(BreedManager):
    def get_queryset(self):
        queryset = super().get_queryset()
        parent_breeds = queryset.filter(parent__isnull=False).values_list('parent', flat=True)
        return queryset.exclude(id__in=parent_breeds)


class CertificationManager(Manager):
    def get_by_natural_key(self, code: str, parent: str | None = None) -> 'Certification':        
        return self.get(code=code, parent__code=parent)
