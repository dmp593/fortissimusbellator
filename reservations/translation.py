from modeltranslation.translator import TranslationOptions, register

from reservations.models import PreReservationTerms, ReservationTerms


@register(PreReservationTerms)
class PreReservationTermsTranslationOptions(TranslationOptions):
    fields = ('description',)


@register(ReservationTerms)
class ReservationTermsTranslationOptions(TranslationOptions):
    fields = ('description',)
