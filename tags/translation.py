from modeltranslation.translator import register, TranslationOptions
from . import models


@register(models.Tag)
class TagTranslationOptions(TranslationOptions):
    fields = ('tag',)
