class ReservationError(Exception):
    """Base class for expected pre-reservation workflow errors."""


class ReservationUnavailable(ReservationError):
    pass


class PaymentError(ReservationError):
    pass


class PaymentConfigurationError(PaymentError):
    pass


class PaymentValidationError(PaymentError):
    pass


class ERPIntegrationError(ReservationError):
    pass

