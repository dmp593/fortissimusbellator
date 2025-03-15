from modeltranslation.translator import register, TranslationOptions
from . import models


@register(models.AnimalKind)
class AnimalKindTranslationOptions(TranslationOptions):
    fields = ('name',)


@register(models.Breed)
class AnimalBreedTranslationOptions(TranslationOptions):
    fields = ('name', 'description',)


@register(models.Certification)
class CertificationTranslationOptions(TranslationOptions):
    fields = ('description',)


@register(models.Animal)
class AnimalTranslationOptions(TranslationOptions):
    fields = ('description',)


@register(models.Litter)
class LitterTranslationOptions(TranslationOptions): 
    fields = ('description', )
