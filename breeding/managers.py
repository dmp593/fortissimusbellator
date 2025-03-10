from django.db import models


class AnimalKindManager(models.Manager):
    def get_by_natural_key(self, name: str) -> 'AnimalKind':
        return self.get(name=name)


class BreedManager(models.Manager):
    def get_by_natural_key(self, name: str, parent: str | None = None, kind: str | None = None) -> 'Breed':        
        if not kind:
            return self.get(name=name)

        return self.get(name=name, parent__name=parent, kind__name=kind)


class CertificationManager(models.Manager):
    def get_by_natural_key(self, code: str, parent: str | None = None) -> 'Certification':        
        return self.get(code=code, parent__code=parent)

