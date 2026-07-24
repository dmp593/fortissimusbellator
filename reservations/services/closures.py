import decimal

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from reservations.exceptions import PaymentError
from reservations.models import (
    Charge,
    CreditAllocation,
    Payment,
    PaymentRefund,
    WorkflowClosure,
)
from reservations.services.ledger import (
    issue_customer_credit,
    money,
    reverse_credit_allocation,
)
from reservations.services.payment import request_refund


@transaction.atomic
def record_workflow_closure(
    *,
    sale_case,
    stage,
    kind,
    reason,
    refund_amount,
    credit_amount,
    created_by,
    provider_loss_acknowledged=False,
    issue_credit_record=True,
):
    charges = _closure_charges(sale_case=sale_case, stage=stage)
    _ensure_no_pending_refunds(charges)

    available_value = money(
        sum(
            (charge.settled_amount for charge in charges),
            decimal.Decimal('0.00'),
        )
    )
    refund = money(refund_amount or decimal.Decimal('0.00'))
    credit = money(credit_amount or decimal.Decimal('0.00'))
    if refund < 0 or credit < 0:
        raise PaymentError(_('Refund and credit amounts cannot be negative.'))
    if refund + credit > available_value:
        raise PaymentError(
            _(
                'Refund and credit cannot exceed the value currently '
                'available in this process.'
            )
        )

    active_allocations = list(
        CreditAllocation.objects.select_for_update()
        .filter(
            charge__in=charges,
            reversed_at__isnull=True,
        )
        .select_related('credit', 'charge')
    )
    returned_credit = sum(
        (allocation.amount for allocation in active_allocations),
        decimal.Decimal('0.00'),
    )
    if credit < returned_credit:
        raise PaymentError(
            _(
                'At least the customer credit already used in this process '
                'must return to customer credit.'
            )
        )

    refundable_payments = _refundable_payments(charges)
    refundable_total = sum(
        (payment.refundable_amount for payment in refundable_payments),
        decimal.Decimal('0.00'),
    )
    if refund > refundable_total:
        raise PaymentError(
            _('The requested refund exceeds refundable real payments.')
        )

    retained = available_value - refund - credit
    closure = WorkflowClosure.objects.create(
        sale_case=sale_case,
        stage=stage,
        kind=kind,
        paid_value_amount=available_value,
        refund_amount=refund,
        credit_amount=credit,
        retained_amount=retained,
        reason=reason.strip(),
        created_by=created_by,
    )

    for allocation in active_allocations:
        reverse_credit_allocation(
            allocation_id=allocation.pk,
            reversed_by=created_by,
            reason=_('Returned because the animal workflow was closed.'),
        )

    additional_credit = credit - returned_credit
    if additional_credit > 0 and issue_credit_record:
        issue_customer_credit(
            user=sale_case.user,
            amount=additional_credit,
            currency=sale_case.currency,
            reason=reason,
            created_by=created_by,
            source_sale_case=sale_case,
            source_closure=closure,
        )

    refunds = _create_refunds(
        payments=refundable_payments,
        amount=refund,
        reason=reason,
        requested_by=created_by,
        closure=closure,
        provider_loss_acknowledged=provider_loss_acknowledged,
    )
    return closure, refunds


def calculate_refund_amount(
    *,
    sale_case,
    stage,
    calculation_type,
    fixed_amount=None,
    target_percentage=None,
):
    charges = _closure_charges(sale_case=sale_case, stage=stage)
    payments = _refundable_payments(charges)
    current_refundable = sum(
        (payment.refundable_amount for payment in payments),
        decimal.Decimal('0.00'),
    )
    if calculation_type == 'none':
        return decimal.Decimal('0.00')
    if calculation_type == PaymentRefund.CalculationType.FIXED:
        if fixed_amount is None:
            raise PaymentError(_('Enter the refund amount.'))
        amount = money(fixed_amount)
    elif (
        calculation_type
        == PaymentRefund.CalculationType.TARGET_PERCENTAGE
    ):
        if target_percentage is None:
            raise PaymentError(_('Enter the target refund percentage.'))
        percentage = money(target_percentage)
        if percentage <= 0 or percentage > 100:
            raise PaymentError(
                _('The target refund percentage must be between 0 and 100.')
            )
        original_paid = sum(
            (payment.amount for payment in payments),
            decimal.Decimal('0.00'),
        )
        already_refunded = sum(
            (payment.committed_refund_amount for payment in payments),
            decimal.Decimal('0.00'),
        )
        amount = money(
            original_paid * percentage / decimal.Decimal('100')
        ) - already_refunded
    elif calculation_type == PaymentRefund.CalculationType.FULL_REMAINING:
        amount = current_refundable
    else:
        raise PaymentError(_('Choose a valid refund calculation.'))
    if amount < 0 or amount > current_refundable:
        raise PaymentError(
            _('The refund cannot exceed the uncommitted payment amount.')
        )
    return amount


def _closure_charges(*, sale_case, stage):
    stages = {
        Charge.Stage.PRE_RESERVATION: (Charge.Stage.PRE_RESERVATION,),
        Charge.Stage.RESERVATION: (
            Charge.Stage.PRE_RESERVATION,
            Charge.Stage.RESERVATION,
        ),
        Charge.Stage.SALE: (
            Charge.Stage.PRE_RESERVATION,
            Charge.Stage.RESERVATION,
            Charge.Stage.SALE,
        ),
    }[stage]
    return list(
        Charge.objects.select_for_update()
        .filter(sale_case=sale_case, stage__in=stages)
        .order_by('-created_at', '-pk')
    )


def _ensure_no_pending_refunds(charges):
    if PaymentRefund.objects.filter(
        payment__charge__in=charges,
        status__in=(
            PaymentRefund.Status.PENDING,
            PaymentRefund.Status.PROCESSING,
        ),
    ).exists():
        raise PaymentError(
            _('Wait for pending refunds before closing this process.')
        )


def _refundable_payments(charges):
    return list(
        Payment.objects.select_for_update()
        .filter(
            charge__in=charges,
            status__in=(
                Payment.Status.PAID,
                Payment.Status.PARTIALLY_REFUNDED,
            ),
        )
        .exclude(provider=Payment.Provider.COMPLIMENTARY)
        .order_by('-charge__created_at', '-created_at', '-pk')
    )


def _create_refunds(
    *,
    payments,
    amount,
    reason,
    requested_by,
    closure,
    provider_loss_acknowledged,
):
    remaining = amount
    refunds = []
    for payment in payments:
        if remaining <= 0:
            break
        refund_amount = min(remaining, payment.refundable_amount)
        if refund_amount <= 0:
            continue
        refunds.append(
            request_refund(
                payment_id=payment.pk,
                calculation_type=PaymentRefund.CalculationType.FIXED,
                fixed_amount=refund_amount,
                reason=reason,
                requested_by=requested_by,
                provider_loss_acknowledged=provider_loss_acknowledged,
                closure=closure,
            )
        )
        remaining -= refund_amount
    if remaining > 0:
        raise PaymentError(
            _('The requested refund could not be allocated to payments.')
        )
    return refunds
