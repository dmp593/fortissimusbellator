from modeltranslation.translator import register, TranslationOptions
from .models import FrequentlyAskedQuestion


@register(FrequentlyAskedQuestion)
class FrequentlyAskedQuestionOptions(TranslationOptions):
    fields = ('question', 'answer')
