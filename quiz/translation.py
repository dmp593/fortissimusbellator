from modeltranslation.translator import register, TranslationOptions
from . import models


@register(models.Question)
class QuestionTranslationOptions(TranslationOptions):
    fields = ('text',)


@register(models.Answer)
class AnswerTranslationOptions(TranslationOptions):
    fields = ('text',)
