import re
from dataclasses import dataclass

import phonenumbers
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


DEFAULT_CALLING_CODE = '+351'
E164_MAX_LENGTH = 16

_CALLING_CODE_PATTERN = re.compile(r'^\+[1-9]\d{0,2}$')
_NATIONAL_NUMBER_PATTERN = re.compile(r'^[\d\s().-]+$')


@dataclass(frozen=True)
class PhoneParts:
    calling_code: str
    national_number: str


def normalize_international_phone(
    calling_code: str,
    national_number: str,
) -> str:
    calling_code = (calling_code or '').strip()
    national_number = (national_number or '').strip()

    if not _CALLING_CODE_PATTERN.fullmatch(calling_code):
        raise ValidationError(
            _('Enter a valid country calling code, for example +351.')
        )
    if not national_number or not _NATIONAL_NUMBER_PATTERN.fullmatch(
        national_number
    ):
        raise ValidationError(_('Enter a valid phone number.'))

    try:
        parsed_number = phonenumbers.parse(
            f'{calling_code}{national_number}',
            None,
        )
    except phonenumbers.NumberParseException as exc:
        raise ValidationError(_('Enter a valid phone number.')) from exc

    submitted_calling_code = calling_code.removeprefix('+')
    if (
        str(parsed_number.country_code) != submitted_calling_code
        or not phonenumbers.is_possible_number(parsed_number)
        or not phonenumbers.is_valid_number(parsed_number)
    ):
        raise ValidationError(
            _('Enter a valid phone number for this country calling code.')
        )

    return phonenumbers.format_number(
        parsed_number,
        phonenumbers.PhoneNumberFormat.E164,
    )


def split_international_phone(value: str) -> PhoneParts:
    value = (value or '').strip()
    if not value:
        return PhoneParts(DEFAULT_CALLING_CODE, '')

    if value.startswith('+'):
        try:
            parsed_number = phonenumbers.parse(value, None)
        except phonenumbers.NumberParseException:
            pass
        else:
            if parsed_number.country_code:
                return PhoneParts(
                    f'+{parsed_number.country_code}',
                    phonenumbers.national_significant_number(parsed_number),
                )

    return PhoneParts(DEFAULT_CALLING_CODE, value)


def validate_international_phone(value: str) -> None:
    parts = split_international_phone(value)
    normalized_value = normalize_international_phone(
        parts.calling_code,
        parts.national_number,
    )
    if normalized_value != value:
        raise ValidationError(
            _('Enter the phone number in international format.')
        )
