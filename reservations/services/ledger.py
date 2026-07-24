import decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from reservations.exceptions import PaymentError
from reservations.models import (
    Charge,
    ChargeAdjustment,
    CreditAllocation,
    CustomerCredit,
    Payment,
)


MONEY = decimal.Decimal('0.01')
MANUAL_PAYMENT_PROVIDERS = {
    Payment.Provider.CASH,
    Payment.Provider.BANK_TRANSFER,
    Payment.Provider.CARD_TERMINAL,
    Payment.Provider.OTHER,
}
SETTLED_PAYMENT_STATUSES = {
    Payment.Status.PAID,
    Payment.Status.PARTIALLY_REFUNDED,
    Payment.Status.REFUNDED,
}


def money(value) -> decimal.Decimal:
    return decimal.Decimal(value).quantize(
        MONEY,
        rounding=decimal.ROUND_HALF_UP,
    )


def create_charge(
    *,
    sale_case,
    stage,
    subtotal_amount,
    currency,
    promotion=None,
    promotion_discount_amount=decimal.Decimal('0.00'),
    promotion_code='',
    promotion_discount_type='',
    promotion_value=None,
    due_at=None,
    created_by=None,
) -> Charge:
    subtotal = money(subtotal_amount)
    discount = money(promotion_discount_amount)
    if subtotal < 0:
        raise PaymentError(_('A charge cannot have a negative subtotal.'))
    if discount < 0 or discount > subtotal:
        raise PaymentError(
            _('A promotion discount must be between zero and the subtotal.')
        )
    return Charge.objects.create(
        sale_case=sale_case,
        stage=stage,
        subtotal_amount=subtotal,
        promotion=promotion,
        promotion_discount_amount=discount,
        promotion_code=promotion_code,
        promotion_discount_type=promotion_discount_type,
        promotion_value=promotion_value,
        currency=currency,
        due_at=due_at,
        created_by=created_by,
    )


@transaction.atomic
def add_charge_adjustment(
    *,
    charge_id,
    amount,
    kind,
    reason,
    created_by,
) -> ChargeAdjustment:
    charge = Charge.objects.select_for_update().get(pk=charge_id)
    adjustment_amount = money(amount)
    if adjustment_amount == 0:
        raise PaymentError(_('An adjustment cannot be zero.'))
    if charge.status == Charge.Status.VOID:
        raise PaymentError(_('A void charge cannot be adjusted.'))

    resulting_total = charge.total_amount + adjustment_amount
    if resulting_total < 0:
        raise PaymentError(_('An adjustment cannot make a charge negative.'))
    if resulting_total < charge.settled_amount:
        raise PaymentError(
            _(
                'This adjustment would reduce the charge below the value '
                'already settled. Resolve the excess as a refund or customer '
                'credit first.'
            )
        )

    adjustment = ChargeAdjustment.objects.create(
        charge=charge,
        kind=kind,
        amount=adjustment_amount,
        reason=reason.strip(),
        created_by=created_by,
    )
    refresh_charge_status(charge.pk)
    return adjustment


@transaction.atomic
def record_manual_payment(
    *,
    charge_id,
    amount,
    provider,
    recorded_by,
    external_reference='',
    note='',
    purchase=None,
) -> Payment:
    if provider not in MANUAL_PAYMENT_PROVIDERS:
        raise PaymentError(_('Choose a valid manual payment method.'))

    charge = Charge.objects.select_for_update().get(pk=charge_id)
    payment_amount = money(amount)
    if payment_amount <= 0:
        raise PaymentError(_('A payment amount must be greater than zero.'))
    if charge.status == Charge.Status.VOID:
        raise PaymentError(_('A void charge cannot receive payments.'))
    if payment_amount > charge.amount_due:
        raise PaymentError(
            _('The payment cannot exceed the outstanding charge amount.')
        )

    legacy_purchase_fields = _available_legacy_purchase_fields(purchase)
    payment = Payment.objects.create(
        charge=charge,
        provider=provider,
        status=Payment.Status.PAID,
        amount=payment_amount,
        currency=charge.currency,
        paid_at=timezone.now(),
        external_reference=external_reference.strip(),
        note=note.strip(),
        recorded_by=recorded_by,
        **legacy_purchase_fields,
    )
    refresh_charge_status(charge.pk)
    return payment


@transaction.atomic
def allocate_customer_credit(
    *,
    credit_id,
    charge_id,
    amount,
    created_by,
    reason='',
) -> CreditAllocation:
    credit = CustomerCredit.objects.select_for_update().get(pk=credit_id)
    charge = (
        Charge.objects.select_for_update()
        .select_related('sale_case')
        .get(pk=charge_id)
    )
    allocation_amount = money(amount)
    if allocation_amount <= 0:
        raise PaymentError(_('A credit allocation must be greater than zero.'))
    if credit.status != CustomerCredit.Status.ACTIVE:
        raise PaymentError(_('This customer credit is not active.'))
    if credit.currency != charge.currency:
        raise PaymentError(_('Customer credit currencies must match.'))
    if credit.user_id:
        same_customer = charge.sale_case.user_id == credit.user_id
    else:
        same_customer = bool(
            credit.customer_email
            and charge.sale_case.customer_email
            and credit.customer_email.casefold()
            == charge.sale_case.customer_email.casefold()
        )
    if not same_customer:
        raise PaymentError(
            _('Customer credit can only be used by the same customer.')
        )
    if allocation_amount > credit.available_amount:
        raise PaymentError(
            _('The allocation exceeds the available customer credit.')
        )
    if allocation_amount > charge.amount_due:
        raise PaymentError(
            _('The allocation exceeds the outstanding charge amount.')
        )

    allocation = CreditAllocation.objects.create(
        credit=credit,
        charge=charge,
        amount=allocation_amount,
        created_by=created_by,
        reason=reason.strip(),
    )
    _refresh_credit_status(credit.pk)
    refresh_charge_status(charge.pk)
    return allocation


@transaction.atomic
def reverse_credit_allocation(
    *,
    allocation_id,
    reversed_by,
    reason,
) -> CreditAllocation:
    allocation = (
        CreditAllocation.objects.select_for_update()
        .select_related('credit', 'charge')
        .get(pk=allocation_id)
    )
    if allocation.reversed_at:
        return allocation
    allocation.reversed_at = timezone.now()
    allocation.reversed_by = reversed_by
    allocation.reversal_reason = reason.strip()
    allocation.save(
        update_fields=[
            'reversed_at',
            'reversed_by',
            'reversal_reason',
        ]
    )
    _refresh_credit_status(allocation.credit_id)
    refresh_charge_status(allocation.charge_id)
    return allocation


def issue_customer_credit(
    *,
    user,
    amount,
    currency,
    reason,
    created_by,
    source_sale_case=None,
    source_closure=None,
    source_transfer=None,
) -> CustomerCredit:
    credit_amount = money(amount)
    if credit_amount <= 0:
        raise PaymentError(_('Customer credit must be greater than zero.'))
    customer_name = source_sale_case.customer_name if source_sale_case else ''
    customer_email = (
        source_sale_case.customer_email if source_sale_case else ''
    )
    if user is None and not customer_email:
        raise PaymentError(
            _(
                'An email address is required to track credit for an '
                'unregistered customer.'
            )
        )
    return CustomerCredit.objects.create(
        user=user,
        customer_name=customer_name,
        customer_email=customer_email,
        amount=credit_amount,
        currency=currency,
        reason=reason.strip(),
        created_by=created_by,
        source_sale_case=source_sale_case,
        source_closure=source_closure,
        source_transfer=source_transfer,
    )


def refresh_charge_status(charge_id: int) -> Charge:
    charge = Charge.objects.get(pk=charge_id)
    if charge.status == Charge.Status.VOID:
        return charge
    if charge.amount_due == 0:
        status = Charge.Status.PAID
    elif charge.settled_amount > 0:
        status = Charge.Status.PARTIALLY_PAID
    else:
        status = Charge.Status.OPEN
    if charge.status != status:
        Charge.objects.filter(pk=charge.pk).update(
            status=status,
            updated_at=timezone.now(),
        )
        charge.status = status
    return charge


def void_charge(*, charge, reason):
    if charge.status == Charge.Status.VOID:
        return charge
    if charge.settled_amount > 0:
        return charge
    charge.status = Charge.Status.VOID
    charge.voided_at = timezone.now()
    charge.void_reason = reason.strip()
    charge.save(
        update_fields=[
            'status',
            'voided_at',
            'void_reason',
            'updated_at',
        ]
    )
    return charge


def _available_legacy_purchase_fields(purchase):
    if purchase is None:
        return {}
    if purchase.__class__.__name__ == 'PreReservation':
        if Payment.objects.filter(pre_reservation=purchase).exists():
            return {}
        return {'pre_reservation': purchase}
    if purchase.__class__.__name__ == 'Reservation':
        if Payment.objects.filter(animal_reservation=purchase).exists():
            return {}
        return {'animal_reservation': purchase}
    return {}


def _refresh_credit_status(credit_id):
    credit = CustomerCredit.objects.get(pk=credit_id)
    status = (
        CustomerCredit.Status.EXHAUSTED
        if credit.available_amount == 0
        else CustomerCredit.Status.ACTIVE
    )
    if credit.status != status:
        CustomerCredit.objects.filter(pk=credit.pk).update(
            status=status,
            updated_at=timezone.now(),
        )
