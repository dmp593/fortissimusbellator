from modeltranslation.translator import TranslationOptions, register

from reservations.models import PreReservationTerms


@register(PreReservationTerms)
class PreReservationTermsTranslationOptions(TranslationOptions):
    fields = ('description',)
