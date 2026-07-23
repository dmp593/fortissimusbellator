from django.conf import settings


STRIPE_MINIMUM_CHECKOUT_MINUTES = 30
STRIPE_MAXIMUM_CHECKOUT_MINUTES = 24 * 60


def checkout_duration_minutes() -> int:
    return min(
        STRIPE_MAXIMUM_CHECKOUT_MINUTES,
        max(STRIPE_MINIMUM_CHECKOUT_MINUTES, settings.RESERVATION_CHECKOUT_MINUTES),
    )
