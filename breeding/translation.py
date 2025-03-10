from modeltranslation.translator import register, TranslationOptions
from .models import AnimalKind, Breed, Animal, Certification


@register(AnimalKind)
class AnimalKindTranslationOptions(TranslationOptions):
    fields = ('name',)


@register(Breed)
class AnimalBreedTranslationOptions(TranslationOptions):
    fields = ('name', 'description',)


@register(Animal)
class AnimalTranslationOptions(TranslationOptions):
    fields = ('description',)


@register(Certification)
class CertificationTranslationOptions(TranslationOptions):
    fields = ('description',)
