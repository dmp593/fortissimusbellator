from django.db import models


class Manager(models.Manager):
    filters: dict

    def __init__(self, **kwargs):
        super().__init__()
        self.filters = kwargs

    def get_queryset(self):
        return super().get_queryset().filter(**self.filters)


class AnimalKindManager(Manager):
    def get_by_natural_key(self, name: str) -> 'AnimalKind':
        return self.get(name=name)


class BreedManager(Manager):
    def get_by_natural_key(self, name: str, parent: str | None = None, kind: str | None = None) -> 'Breed':        
        if not kind:
            return self.get(name=name)

        return self.get(name=name, parent__name=parent, kind__name=kind)


class CertificationManager(Manager):
    def get_by_natural_key(self, code: str, parent: str | None = None) -> 'Certification':        
        return self.get(code=code, parent__code=parent)
