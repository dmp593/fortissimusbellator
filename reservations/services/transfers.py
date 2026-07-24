import decimal
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from breeding.models import Animal
from reservations.availability import dog_inventory_unavailability_reason
from reservations.exceptions import PaymentError, ReservationUnavailable
from reservations.models import (
    AnimalSaleCase,
    AnimalWorkflowTransfer,
    Charge,
    CustomerCredit,
    Payment,
    PaymentRefund,
    PreReservation,
    PreReservationTerms,
    Reservation,
    ReservationTerms,
    WorkflowClosure,
)
from reservations.services.closures import record_workflow_closure
from reservations.services.ledger import (
    allocate_customer_credit,
    create_charge,
    money,
    refresh_charge_status,
    record_manual_payment,
    void_charge,
)
from reservations.services.notifications import notify_workflow_transferred


logger = logging.getLogger(__name__)


@transaction.atomic
def transfer_animal_workflow(
    *,
    source_case_id,
    target_animal_id,
    transferred_amount,
    refund_amount,
    retained_amount,
    reason,
    created_by,
    target_charge_amount=None,
    provider_loss_acknowledged=False,
    difference_payment_provider=Payment.Provider.STRIPE,
    payment_reference='',
    payment_note='',
    terms_accepted_in_person=False,
):
    source_case = _lock_transfer_source(
        source_case_id=source_case_id,
        target_animal_id=target_animal_id,
    )
    target_animal = _lock_transfer_target_animal(
        source_case=source_case,
        target_animal_id=target_animal_id,
    )
    source_stage = _source_stage_for_case(source_case)
    source_charges = _source_charges(source_case, source_stage)
    available_value, transfer_value, refund_value, retained_value = (
        _validated_transfer_split(
            source_charges=source_charges,
            transferred_amount=transferred_amount,
            refund_amount=refund_amount,
            retained_amount=retained_amount,
        )
    )
    target_case, target_stage, target_charge, applied_credit = (
        _create_transfer_destination(
            source_case=source_case,
            target_animal=target_animal,
            source_stage=source_stage,
            transfer_value=transfer_value,
            target_charge_amount=target_charge_amount,
            created_by=created_by,
            terms_accepted_in_person=terms_accepted_in_person,
        )
    )
    workflow_transfer, refunds, credit_ids = _record_transfer_history(
        source_case=source_case,
        target_case=target_case,
        source_stage=source_stage,
        source_charges=source_charges,
        available_value=available_value,
        transfer_value=transfer_value,
        refund_value=refund_value,
        retained_value=retained_value,
        reason=reason,
        created_by=created_by,
        provider_loss_acknowledged=provider_loss_acknowledged,
    )
    _finish_transfer_destination(
        source_case=source_case,
        source_stage=source_stage,
        target_stage=target_stage,
        target_charge=target_charge,
        applied_credit=applied_credit,
        credit_ids=credit_ids,
        workflow_transfer=workflow_transfer,
        reason=reason,
        created_by=created_by,
        difference_payment_provider=difference_payment_provider,
        payment_reference=payment_reference,
        payment_note=payment_note,
    )
    return workflow_transfer, target_stage, refunds


def _lock_transfer_source(*, source_case_id, target_animal_id):
    source_case = (
        AnimalSaleCase.objects.select_for_update()
        .select_related('animal', 'user')
        .get(pk=source_case_id)
    )
    if source_case.status not in {
        AnimalSaleCase.Status.PRE_RESERVATION,
        AnimalSaleCase.Status.RESERVATION,
    }:
        raise ReservationUnavailable(
            _('Only an active pre-reservation or reservation can be transferred.')
        )
    if source_case.animal_id == target_animal_id:
        raise ReservationUnavailable(_('Choose a different target dog.'))
    if Payment.objects.filter(
        charge__sale_case=source_case,
        status__in=(Payment.Status.INITIALIZING, Payment.Status.PENDING),
    ).exists():
        raise PaymentError(
            _(
                'Close or reconcile active online payments before '
                'transferring this process.'
            )
        )
    return source_case


def _lock_transfer_target_animal(*, source_case, target_animal_id):
    animal_ids = sorted((source_case.animal_id, target_animal_id))
    locked_animals = {
        animal.pk: animal
        for animal in (
            Animal.objects.select_for_update()
            .select_related('breed')
            .filter(pk__in=animal_ids)
            .order_by('pk')
        )
    }
    target_animal = locked_animals.get(target_animal_id)
    if target_animal is None:
        raise ReservationUnavailable(_('The target dog does not exist.'))
    unavailable_reason = dog_inventory_unavailability_reason(target_animal)
    if unavailable_reason:
        raise ReservationUnavailable(unavailable_reason)
    return target_animal


def _source_stage_for_case(source_case):
    return (
        Charge.Stage.RESERVATION
        if source_case.status == AnimalSaleCase.Status.RESERVATION
        else Charge.Stage.PRE_RESERVATION
    )


def _validated_transfer_split(
    *,
    source_charges,
    transferred_amount,
    refund_amount,
    retained_amount,
):
    available_value = money(
        sum(
            (charge.settled_amount for charge in source_charges),
            decimal.Decimal('0.00'),
        )
    )
    transfer_value = money(transferred_amount)
    refund_value = money(refund_amount)
    retained_value = money(retained_amount)
    if min(transfer_value, refund_value, retained_value) < 0:
        raise PaymentError(_('Transfer split values cannot be negative.'))
    if transfer_value + refund_value + retained_value != available_value:
        raise PaymentError(
            _(
                'Transferred, refunded, and retained values must equal the '
                'available process value.'
            )
        )
    return available_value, transfer_value, refund_value, retained_value


def _create_transfer_destination(
    *,
    source_case,
    target_animal,
    source_stage,
    transfer_value,
    target_charge_amount,
    created_by,
    terms_accepted_in_person,
):
    try:
        with transaction.atomic():
            target_case = _create_target_case(
                source_case=source_case,
                target_animal=target_animal,
                source_stage=source_stage,
                created_by=created_by,
            )
            target_stage, target_charge, applied_credit = _create_target_stage(
                source_case=source_case,
                target_case=target_case,
                target_animal=target_animal,
                source_stage=source_stage,
                transfer_value=transfer_value,
                target_charge_amount=target_charge_amount,
                created_by=created_by,
                terms_accepted_in_person=terms_accepted_in_person,
            )
    except IntegrityError as exc:
        raise ReservationUnavailable(
            _('The target dog is already held by another process.')
        ) from exc
    return target_case, target_stage, target_charge, applied_credit


def _record_transfer_history(
    *,
    source_case,
    target_case,
    source_stage,
    source_charges,
    available_value,
    transfer_value,
    refund_value,
    retained_value,
    reason,
    created_by,
    provider_loss_acknowledged,
):
    source_credit_ids = list(
        CustomerCredit.objects.filter(
            allocations__charge__in=source_charges,
            allocations__reversed_at__isnull=True,
        ).values_list('pk', flat=True)
    )
    workflow_transfer = AnimalWorkflowTransfer.objects.create(
        source_case=source_case,
        target_case=target_case,
        source_stage=source_stage,
        target_stage=source_stage,
        available_value_amount=available_value,
        transferred_amount=transfer_value,
        refund_amount=refund_value,
        retained_amount=retained_value,
        reason=reason.strip(),
        created_by=created_by,
    )
    closure, refunds = record_workflow_closure(
        sale_case=source_case,
        stage=source_stage,
        kind=WorkflowClosure.Kind.TRANSFERRED,
        reason=reason,
        refund_amount=refund_value,
        credit_amount=transfer_value,
        created_by=created_by,
        provider_loss_acknowledged=provider_loss_acknowledged,
    )
    if refunds:
        PaymentRefund.objects.filter(
            pk__in=[payment_refund.pk for payment_refund in refunds],
        ).update(transfer=workflow_transfer)
    closure.credits.update(source_transfer=workflow_transfer)

    candidate_credit_ids = list(
        dict.fromkeys(
            source_credit_ids
            + list(closure.credits.values_list('pk', flat=True))
        )
    )
    return workflow_transfer, refunds, candidate_credit_ids


def _finish_transfer_destination(
    *,
    source_case,
    source_stage,
    target_stage,
    target_charge,
    applied_credit,
    credit_ids,
    workflow_transfer,
    reason,
    created_by,
    difference_payment_provider,
    payment_reference,
    payment_note,
):
    _allocate_transfer_credits(
        credit_ids=credit_ids,
        charge=target_charge,
        amount=applied_credit,
        created_by=created_by,
        reason=reason,
    )
    _finish_target_stage(
        target_stage,
        target_charge,
        created_by,
        difference_payment_provider=difference_payment_provider,
        payment_reference=payment_reference,
        payment_note=payment_note,
    )
    target_charge.refresh_from_db()
    if target_charge.amount_due == 0:
        transaction.on_commit(
            lambda charge_id=target_charge.pk: (
                _initialize_target_erp_document(charge_id)
            )
        )
    _close_source_stage(source_case, source_stage, reason)
    transaction.on_commit(
        lambda pk=workflow_transfer.pk: notify_workflow_transferred(
            AnimalWorkflowTransfer.objects.select_related(
                'source_case',
                'target_case',
            ).get(pk=pk),
        )
    )


def _initialize_target_erp_document(charge_id):
    from reservations.models import ERPIntegrationAttempt
    from reservations.services.erp import (
        ensure_sale_erp_document,
        process_erp_document,
    )

    try:
        document = ensure_sale_erp_document(
            Charge.objects.get(pk=charge_id),
        )
        if document is not None:
            process_erp_document(
                document.pk,
                trigger=ERPIntegrationAttempt.Trigger.AUTOMATIC,
            )
    except Exception:
        logger.exception(
            'Unable to initialize ERP processing for a transferred workflow',
            extra={'charge_id': charge_id},
        )


def _source_charges(sale_case, source_stage):
    stages = [Charge.Stage.PRE_RESERVATION]
    if source_stage == Charge.Stage.RESERVATION:
        stages.append(Charge.Stage.RESERVATION)
    return list(
        Charge.objects.select_for_update()
        .filter(sale_case=sale_case, stage__in=stages)
        .order_by('created_at', 'pk')
    )


def _create_target_case(*, source_case, target_animal, source_stage, created_by):
    current_price = target_animal.current_price_in_euros
    price = money(current_price) if current_price is not None else None
    deposit_percentage = money(
        target_animal.reservation_deposit_percentage,
    )
    deposit_amount = (
        money(price * deposit_percentage / decimal.Decimal('100'))
        if price is not None
        else None
    )
    return AnimalSaleCase.objects.create(
        user=source_case.user,
        animal=target_animal,
        origin=AnimalSaleCase.Origin.TRANSFER,
        status=(
            AnimalSaleCase.Status.RESERVATION
            if source_stage == Charge.Stage.RESERVATION
            else AnimalSaleCase.Status.PRE_RESERVATION
        ),
        target_name=target_animal.name,
        target_breed=target_animal.breed.name,
        target_birth_date=target_animal.birth_date,
        animal_price_amount=price,
        reservation_deposit_percentage=deposit_percentage,
        reservation_deposit_amount=deposit_amount,
        customer_name=source_case.customer_name,
        customer_email=source_case.customer_email,
        customer_phone=source_case.customer_phone,
        customer_tax_number=source_case.customer_tax_number,
        billing_address=source_case.billing_address,
        billing_postcode=source_case.billing_postcode,
        billing_city=source_case.billing_city,
        billing_country=source_case.billing_country,
        language_code=source_case.language_code,
        currency=source_case.currency,
        created_by=created_by,
    )


def _pre_reservation_terms_for_transfer(*, source_case, staff_recorded):
    source = source_case.pre_reservation
    if (
        source.terms_id
        and source.non_refundable_accepted_at
        and source.terms_acceptance_source
        != PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER
    ):
        return (
            source.terms,
            source.terms_acceptance_source,
            source.non_refundable_accepted_at,
        )
    if staff_recorded:
        terms = PreReservationTerms.objects.current()
        if terms is None:
            raise ReservationUnavailable(
                _('Published pre-reservation terms are required.')
            )
        return (
            terms,
            PreReservation.TermsAcceptanceSource.STAFF_RECORDED,
            timezone.now(),
        )
    return (
        None,
        PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER,
        None,
    )


def _reservation_terms_for_transfer(*, source_case, staff_recorded):
    source = source_case.reservation
    if (
        source.terms_id
        and source.terms_accepted_at
        and source.terms_acceptance_source
        != Reservation.TermsAcceptanceSource.PENDING_CUSTOMER
    ):
        return (
            source.terms,
            source.terms_acceptance_source,
            source.terms_accepted_at,
        )
    if staff_recorded:
        terms = ReservationTerms.objects.current()
        if terms is None:
            raise ReservationUnavailable(
                _('Published reservation terms are required.')
            )
        return (
            terms,
            Reservation.TermsAcceptanceSource.STAFF_RECORDED,
            timezone.now(),
        )
    return (
        None,
        Reservation.TermsAcceptanceSource.PENDING_CUSTOMER,
        None,
    )


def _create_target_stage(
    *,
    source_case,
    target_case,
    target_animal,
    source_stage,
    transfer_value,
    target_charge_amount,
    created_by,
    terms_accepted_in_person,
):
    if source_stage == Charge.Stage.PRE_RESERVATION:
        subtotal = money(
            target_charge_amount
            if target_charge_amount is not None
            else target_animal.pre_reservation_fee
        )
        terms, terms_source, terms_accepted_at = (
            _pre_reservation_terms_for_transfer(
                source_case=source_case,
                staff_recorded=terms_accepted_in_person,
            )
        )
        charge = create_charge(
            sale_case=target_case,
            stage=Charge.Stage.PRE_RESERVATION,
            subtotal_amount=subtotal,
            currency=target_case.currency,
            created_by=created_by,
        )
        applied_credit = min(transfer_value, charge.total_amount)
        stage = PreReservation.objects.create(
            sale_case=target_case,
            charge=charge,
            user=target_case.user,
            target_type=PreReservation.TargetType.DOG,
            animal=target_animal,
            target_name=target_case.target_name,
            target_breed=target_case.target_breed,
            target_birth_date=target_case.target_birth_date,
            customer_name=target_case.customer_name,
            customer_email=target_case.customer_email,
            customer_phone=target_case.customer_phone,
            customer_tax_number=target_case.customer_tax_number,
            billing_address=target_case.billing_address,
            billing_postcode=target_case.billing_postcode,
            billing_city=target_case.billing_city,
            billing_country=target_case.billing_country,
            language_code=target_case.language_code,
            fee_amount=subtotal,
            total_amount=subtotal,
            currency=target_case.currency,
            animal_price_amount=target_case.animal_price_amount,
            reservation_deposit_percentage=(
                target_case.reservation_deposit_percentage
            ),
            reservation_deposit_amount=(
                target_case.reservation_deposit_amount
            ),
            terms=terms,
            terms_acceptance_source=terms_source,
            non_refundable_accepted_at=terms_accepted_at,
            status=PreReservation.Status.PENDING_PAYMENT,
        )
        return stage, charge, applied_credit

    subtotal = (
        money(target_charge_amount)
        if target_charge_amount is not None
        else target_case.reservation_deposit_amount
    )
    if subtotal is None:
        raise PaymentError(
            _(
                'Enter a reservation amount because the target dog has no '
                'published price.'
            )
        )
    terms, terms_source, terms_accepted_at = _reservation_terms_for_transfer(
        source_case=source_case,
        staff_recorded=terms_accepted_in_person,
    )
    charge = create_charge(
        sale_case=target_case,
        stage=Charge.Stage.RESERVATION,
        subtotal_amount=subtotal,
        currency=target_case.currency,
        due_at=timezone.now()
        + timedelta(hours=target_animal.reservation_offer_hours),
        created_by=created_by,
    )
    applied_credit = min(transfer_value, charge.total_amount)
    stage = Reservation(
        sale_case=target_case,
        charge=charge,
        status=Reservation.Status.OFFERED,
        customer_credit_amount=applied_credit,
        deposit_target_amount=subtotal,
        payment_amount=subtotal - applied_credit,
        currency=target_case.currency,
        offer_expires_at=charge.due_at,
        terms=terms,
        terms_accepted_at=terms_accepted_at,
        terms_acceptance_source=terms_source,
    )
    stage.full_clean()
    stage.save()
    return stage, charge, applied_credit


def _allocate_transfer_credits(
    *,
    credit_ids,
    charge,
    amount,
    created_by,
    reason,
):
    remaining = amount
    credits = CustomerCredit.objects.select_for_update().filter(
        pk__in=credit_ids,
        status=CustomerCredit.Status.ACTIVE,
    ).order_by('created_at', 'pk')
    for credit in credits:
        if remaining <= 0:
            break
        allocation = min(remaining, credit.available_amount, charge.amount_due)
        if allocation <= 0:
            continue
        allocate_customer_credit(
            credit_id=credit.pk,
            charge_id=charge.pk,
            amount=allocation,
            created_by=created_by,
            reason=reason,
        )
        remaining -= allocation
    if remaining > 0:
        raise PaymentError(
            _('The transferred value could not be allocated to the target.')
        )


def _finish_target_stage(
    stage,
    charge,
    created_by,
    *,
    difference_payment_provider,
    payment_reference,
    payment_note,
):
    charge = refresh_charge_status(charge.pk)
    terms_are_pending = (
        stage.terms_acceptance_source
        in {
            PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER,
            Reservation.TermsAcceptanceSource.PENDING_CUSTOMER,
        }
    )
    if (
        charge.amount_due > 0
        and difference_payment_provider != Payment.Provider.STRIPE
        and terms_are_pending
    ):
        raise PaymentError(
            _(
                'Confirm the customer accepted the target-stage terms before '
                'recording an offline difference payment.'
            )
        )
    if charge.amount_due > 0 and (
        difference_payment_provider != Payment.Provider.STRIPE
    ):
        record_manual_payment(
            charge_id=charge.pk,
            amount=charge.amount_due,
            provider=difference_payment_provider,
            recorded_by=created_by,
            external_reference=payment_reference,
            note=payment_note,
            purchase=stage,
        )
        charge = refresh_charge_status(charge.pk)
    if isinstance(stage, PreReservation):
        if charge.amount_due == 0 and not terms_are_pending:
            stage.status = PreReservation.Status.AWAITING_REVIEW
            stage.confirmed_at = timezone.now()
            stage.save(
                update_fields=['status', 'confirmed_at', 'updated_at'],
            )
        else:
            if stage.user_id is None:
                raise PaymentError(
                    _(
                        'Online payment or customer terms acceptance requires '
                        'a registered customer.'
                    )
                )
            Payment.objects.create(
                charge=charge,
                pre_reservation=stage,
                provider=Payment.Provider.STRIPE,
                status=Payment.Status.INITIALIZING,
                amount=charge.amount_due,
                currency=charge.currency,
                recorded_by=created_by,
            )
        return

    if charge.amount_due == 0 and not terms_are_pending:
        stage.status = Reservation.Status.CONFIRMED
        stage.confirmed_at = timezone.now()
        stage.save(
            update_fields=['status', 'confirmed_at', 'updated_at'],
        )
    elif stage.user is None:
        raise PaymentError(
            _(
                'Online payment or customer terms acceptance requires a '
                'registered customer.'
            )
        )


def _close_source_stage(source_case, source_stage, reason):
    now = timezone.now()
    if source_stage == Charge.Stage.PRE_RESERVATION:
        stage = source_case.pre_reservation
        stage.status = PreReservation.Status.TRANSFERRED
        stage.cancelled_at = now
        stage.cancellation_reason = reason.strip()
        stage.save(
            update_fields=[
                'status',
                'cancelled_at',
                'cancellation_reason',
                'updated_at',
            ],
        )
    else:
        stage = source_case.reservation
        stage.status = Reservation.Status.TRANSFERRED
        stage.cancelled_at = now
        stage.cancellation_reason = reason.strip()
        stage.save(
            update_fields=[
                'status',
                'cancelled_at',
                'cancellation_reason',
                'updated_at',
            ],
        )
    for charge in source_case.charges.all():
        void_charge(charge=charge, reason=reason)
    source_case.status = AnimalSaleCase.Status.TRANSFERRED
    source_case.closed_at = now
    source_case.save(
        update_fields=['status', 'closed_at', 'updated_at'],
    )
