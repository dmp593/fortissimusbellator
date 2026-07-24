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
    AnimalSale,
    AnimalSaleCase,
    Charge,
    CustomerCredit,
    Payment,
    PreReservation,
    PreReservationTerms,
    Reservation,
    ReservationTerms,
)
from reservations.services.ledger import (
    allocate_customer_credit,
    create_charge,
    money,
    record_manual_payment,
    refresh_charge_status,
)
from reservations.services.reservation import (
    accept_staff_created_pre_reservation,
)


logger = logging.getLogger(__name__)


def _customer_values(*, user, customer_data):
    customer_data = customer_data or {}
    return {
        'customer_name': (
            customer_data.get('customer_name')
            or (user.get_full_name() if user else '')
            or (user.username if user else '')
        ).strip(),
        'customer_email': (
            customer_data.get('customer_email')
            or (user.email if user else '')
        ).strip(),
        'customer_phone': customer_data.get('customer_phone', '').strip(),
        'customer_tax_number': customer_data.get(
            'customer_tax_number',
            '',
        ).strip(),
        'billing_address': customer_data.get('billing_address', '').strip(),
        'billing_postcode': customer_data.get(
            'billing_postcode',
            '',
        ).strip(),
        'billing_city': customer_data.get('billing_city', '').strip(),
        'billing_country': customer_data.get(
            'billing_country',
            'PT',
        ).strip().upper(),
        'language_code': customer_data.get('language_code', 'en'),
    }


def _sale_case_values(*, animal, user, customer_data, created_by, status):
    price = (
        money(animal.current_price_in_euros)
        if animal.current_price_in_euros is not None
        else None
    )
    deposit_percentage = money(animal.reservation_deposit_percentage)
    deposit_amount = (
        money(price * deposit_percentage / decimal.Decimal('100'))
        if price is not None
        else None
    )
    return {
        'user': user,
        'animal': animal,
        'origin': AnimalSaleCase.Origin.ADMIN,
        'status': status,
        'target_name': animal.name,
        'target_breed': animal.breed.name,
        'target_birth_date': animal.birth_date,
        'animal_price_amount': price,
        'reservation_deposit_percentage': deposit_percentage,
        'reservation_deposit_amount': deposit_amount,
        'currency': 'EUR',
        'created_by': created_by,
        **_customer_values(user=user, customer_data=customer_data),
    }


def _lock_available_animal(animal_id):
    try:
        animal = (
            Animal.objects.select_for_update()
            .select_related('breed')
            .get(pk=animal_id)
        )
    except Animal.DoesNotExist as exc:
        raise ReservationUnavailable(_('This dog is no longer available.')) from exc
    reason = dog_inventory_unavailability_reason(animal)
    if reason:
        raise ReservationUnavailable(reason)
    return animal


def _terms_snapshot(*, terms_model, staff_recorded):
    if not staff_recorded:
        return None, None
    terms = terms_model.objects.current()
    if terms is None:
        raise ReservationUnavailable(_('Published terms are required.'))
    return terms, timezone.now()


def _validate_complimentary_balance(
    *,
    payment_provider,
    amount_due,
    note='',
):
    balance = money(amount_due)
    if (
        payment_provider == Payment.Provider.COMPLIMENTARY
        and balance != 0
    ):
        raise PaymentError(
            _(
                'A complimentary settlement cannot leave an amount due. '
                'Enter zero, apply enough customer credit, or adjust the '
                'charge first.'
            )
        )
    if (
        payment_provider
        in {
            Payment.Provider.COMPLIMENTARY,
            Payment.Provider.OTHER,
        }
        and not str(note or '').strip()
    ):
        raise PaymentError(
            _(
                'Explain this settlement method so the commercial decision '
                'remains auditable.'
            )
        )


def _record_complimentary_payment(
    *,
    charge,
    recorded_by,
    note,
    purchase=None,
):
    if charge.amount_due != 0:
        raise PaymentError(
            _('A complimentary payment requires a zero outstanding balance.')
        )
    purchase_fields = {}
    if (
        isinstance(purchase, PreReservation)
        and not Payment.objects.filter(pre_reservation=purchase).exists()
    ):
        purchase_fields['pre_reservation'] = purchase
    elif (
        isinstance(purchase, Reservation)
        and not Payment.objects.filter(animal_reservation=purchase).exists()
    ):
        purchase_fields['animal_reservation'] = purchase
    payment = Payment.objects.create(
        charge=charge,
        provider=Payment.Provider.COMPLIMENTARY,
        status=Payment.Status.PAID,
        amount=decimal.Decimal('0.00'),
        currency=charge.currency,
        paid_at=timezone.now(),
        recorded_by=recorded_by,
        note=str(note or '').strip(),
        **purchase_fields,
    )
    refresh_charge_status(charge.pk)
    return payment


@transaction.atomic
def record_staff_terms_acceptance(charge_id):
    charge = Charge.objects.select_for_update().get(pk=charge_id)
    purchase = charge.purchase
    now = timezone.now()
    if isinstance(purchase, PreReservation):
        if (
            purchase.terms_acceptance_source
            != PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER
        ):
            return purchase
        terms = PreReservationTerms.objects.current()
        if terms is None:
            raise ReservationUnavailable(
                _('Published pre-reservation terms are required.')
            )
        purchase.terms = terms
        purchase.terms_acceptance_source = (
            PreReservation.TermsAcceptanceSource.STAFF_RECORDED
        )
        purchase.non_refundable_accepted_at = now
        purchase.save(
            update_fields=[
                'terms',
                'terms_acceptance_source',
                'non_refundable_accepted_at',
                'updated_at',
            ],
        )
        return purchase
    if isinstance(purchase, Reservation):
        if (
            purchase.terms_acceptance_source
            != Reservation.TermsAcceptanceSource.PENDING_CUSTOMER
        ):
            return purchase
        terms = ReservationTerms.objects.current()
        if terms is None:
            raise ReservationUnavailable(
                _('Published reservation terms are required.')
            )
        purchase.terms = terms
        purchase.terms_acceptance_source = (
            Reservation.TermsAcceptanceSource.STAFF_RECORDED
        )
        purchase.terms_accepted_at = now
        purchase.save(
            update_fields=[
                'terms',
                'terms_acceptance_source',
                'terms_accepted_at',
                'updated_at',
            ],
        )
    return purchase


@transaction.atomic
def create_admin_pre_reservation(
    *,
    animal_id,
    user,
    customer_data,
    fee_amount,
    payment_provider,
    created_by,
    terms_accepted_in_person=False,
    payment_reference='',
    payment_note='',
) -> PreReservation:
    if payment_provider == Payment.Provider.STRIPE and user is None:
        raise ReservationUnavailable(
            _('Online payment requires a registered customer.')
        )
    animal = _lock_available_animal(animal_id)
    fee = money(fee_amount)
    if fee < 0:
        raise PaymentError(_('The pre-reservation amount cannot be negative.'))
    if animal.current_price_in_euros is None:
        raise ReservationUnavailable(
            _(
                'A pre-reservation requires a published dog price so the '
                'future reservation deposit can be calculated. Create a '
                'direct reservation instead and enter the agreed amount.'
            )
        )
    _validate_complimentary_balance(
        payment_provider=payment_provider,
        amount_due=fee,
        note=payment_note,
    )
    terms, accepted_at = _terms_snapshot(
        terms_model=PreReservationTerms,
        staff_recorded=terms_accepted_in_person,
    )
    if payment_provider != Payment.Provider.STRIPE and terms is None:
        raise ReservationUnavailable(
            _(
                'Record the customer terms acceptance before recording an '
                'offline pre-reservation payment.'
            )
        )
    if (
        payment_provider != Payment.Provider.STRIPE
        and ReservationTerms.objects.current() is None
    ):
        raise ReservationUnavailable(
            _(
                'Published reservation terms are required because a paid '
                'staff pre-reservation is accepted automatically.'
            )
        )

    try:
        with transaction.atomic():
            sale_case = AnimalSaleCase.objects.create(
                **_sale_case_values(
                    animal=animal,
                    user=user,
                    customer_data=customer_data,
                    created_by=created_by,
                    status=AnimalSaleCase.Status.PRE_RESERVATION,
                ),
            )
            charge = create_charge(
                sale_case=sale_case,
                stage=Charge.Stage.PRE_RESERVATION,
                subtotal_amount=fee,
                currency=sale_case.currency,
                created_by=created_by,
            )
            pre_reservation = PreReservation.objects.create(
                sale_case=sale_case,
                charge=charge,
                user=user,
                target_type=PreReservation.TargetType.DOG,
                animal=animal,
                target_name=sale_case.target_name,
                target_breed=sale_case.target_breed,
                target_birth_date=sale_case.target_birth_date,
                customer_name=sale_case.customer_name,
                customer_email=sale_case.customer_email,
                customer_phone=sale_case.customer_phone,
                customer_tax_number=sale_case.customer_tax_number,
                billing_address=sale_case.billing_address,
                billing_postcode=sale_case.billing_postcode,
                billing_city=sale_case.billing_city,
                billing_country=sale_case.billing_country,
                language_code=sale_case.language_code,
                fee_amount=fee,
                total_amount=fee,
                animal_price_amount=sale_case.animal_price_amount,
                reservation_deposit_percentage=(
                    sale_case.reservation_deposit_percentage
                ),
                reservation_deposit_amount=(
                    sale_case.reservation_deposit_amount
                ),
                terms=terms,
                terms_acceptance_source=(
                    PreReservation.TermsAcceptanceSource.STAFF_RECORDED
                    if terms
                    else PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER
                ),
                non_refundable_accepted_at=accepted_at,
                status=PreReservation.Status.PENDING_PAYMENT,
                confirmed_at=None,
            )
    except IntegrityError as exc:
        raise ReservationUnavailable(
            _('This dog is already held by another process.')
        ) from exc

    if payment_provider == Payment.Provider.STRIPE:
        Payment.objects.create(
            charge=charge,
            pre_reservation=pre_reservation,
            provider=Payment.Provider.STRIPE,
            status=Payment.Status.INITIALIZING,
            amount=fee,
            currency=sale_case.currency,
            recorded_by=created_by,
            note=payment_note.strip(),
        )
        _schedule_payment_request(
            purchase=pre_reservation,
            notification='pre_reservation',
        )
    elif fee > 0:
        record_manual_payment(
            charge_id=charge.pk,
            amount=fee,
            provider=payment_provider,
            recorded_by=created_by,
            external_reference=payment_reference,
            note=payment_note,
            purchase=pre_reservation,
        )
    else:
        _record_complimentary_payment(
            charge=charge,
            purchase=pre_reservation,
            recorded_by=created_by,
            note=payment_note.strip(),
        )
    if payment_provider != Payment.Provider.STRIPE:
        synchronize_paid_charge(
            charge.pk,
            admin_user=created_by,
        )
        pre_reservation.refresh_from_db()
    return pre_reservation


@transaction.atomic
def create_admin_reservation(
    *,
    animal_id,
    user,
    customer_data,
    deposit_amount,
    payment_provider,
    created_by,
    terms_accepted_in_person=False,
    offer_hours=None,
    credit=None,
    credit_amount=decimal.Decimal('0.00'),
    payment_reference='',
    payment_note='',
) -> Reservation:
    deposit, applied_credit = _validate_admin_reservation_amounts(
        user=user,
        deposit_amount=deposit_amount,
        credit=credit,
        credit_amount=credit_amount,
        payment_provider=payment_provider,
        payment_note=payment_note,
    )
    animal = _lock_available_animal(animal_id)
    terms, accepted_at, offer_expires_at = (
        _admin_reservation_terms_and_expiry(
            animal=animal,
            payment_provider=payment_provider,
            terms_accepted_in_person=terms_accepted_in_person,
            offer_hours=offer_hours,
        )
    )
    charge, reservation = _create_admin_reservation_records(
        animal=animal,
        user=user,
        customer_data=customer_data,
        deposit=deposit,
        applied_credit=applied_credit,
        payment_provider=payment_provider,
        created_by=created_by,
        terms=terms,
        accepted_at=accepted_at,
        offer_expires_at=offer_expires_at,
    )
    _settle_admin_reservation(
        reservation=reservation,
        charge=charge,
        credit=credit,
        applied_credit=applied_credit,
        payment_provider=payment_provider,
        payment_reference=payment_reference,
        payment_note=payment_note,
        created_by=created_by,
    )
    reservation.refresh_from_db()
    return reservation


def _validate_admin_reservation_amounts(
    *,
    user,
    deposit_amount,
    credit,
    credit_amount,
    payment_provider,
    payment_note,
):
    if payment_provider == Payment.Provider.STRIPE and user is None:
        raise ReservationUnavailable(
            _('Online payment requires a registered customer.')
        )
    deposit = money(deposit_amount)
    if deposit < 0:
        raise PaymentError(_('The reservation amount cannot be negative.'))
    applied_credit = money(credit_amount)
    if applied_credit < 0 or applied_credit > deposit:
        raise PaymentError(
            _('Customer credit must be between zero and the reservation amount.')
        )
    if applied_credit and credit is None:
        raise PaymentError(_('Choose the customer credit to apply.'))
    if credit and user and credit.user_id != user.pk:
        raise PaymentError(_('The selected credit belongs to another customer.'))
    _validate_complimentary_balance(
        payment_provider=payment_provider,
        amount_due=deposit - applied_credit,
        note=payment_note,
    )
    return deposit, applied_credit


def _admin_reservation_terms_and_expiry(
    *,
    animal,
    payment_provider,
    terms_accepted_in_person,
    offer_hours,
):
    terms, accepted_at = _terms_snapshot(
        terms_model=ReservationTerms,
        staff_recorded=terms_accepted_in_person,
    )
    if payment_provider != Payment.Provider.STRIPE and terms is None:
        raise ReservationUnavailable(
            _(
                'Record the customer terms acceptance before recording an '
                'offline reservation payment.'
            )
        )
    validity_hours = offer_hours or animal.reservation_offer_hours
    if validity_hours < 1 or validity_hours > 168:
        raise ReservationUnavailable(
            _('Reservation validity must be between 1 and 168 hours.')
        )
    offer_expires_at = (
        timezone.now() + timedelta(hours=validity_hours)
        if payment_provider == Payment.Provider.STRIPE
        else None
    )
    return terms, accepted_at, offer_expires_at


def _create_admin_reservation_records(
    *,
    animal,
    user,
    customer_data,
    deposit,
    applied_credit,
    payment_provider,
    created_by,
    terms,
    accepted_at,
    offer_expires_at,
):
    try:
        with transaction.atomic():
            sale_case = AnimalSaleCase.objects.create(
                **_sale_case_values(
                    animal=animal,
                    user=user,
                    customer_data=customer_data,
                    created_by=created_by,
                    status=AnimalSaleCase.Status.RESERVATION,
                ),
            )
            charge = create_charge(
                sale_case=sale_case,
                stage=Charge.Stage.RESERVATION,
                subtotal_amount=deposit,
                currency=sale_case.currency,
                due_at=offer_expires_at,
                created_by=created_by,
            )
            reservation = Reservation(
                sale_case=sale_case,
                charge=charge,
                status=Reservation.Status.OFFERED,
                customer_credit_amount=applied_credit,
                deposit_target_amount=deposit,
                payment_amount=deposit - applied_credit,
                currency=sale_case.currency,
                offer_expires_at=offer_expires_at,
                terms=terms,
                terms_accepted_at=accepted_at,
                terms_acceptance_source=(
                    Reservation.TermsAcceptanceSource.STAFF_RECORDED
                    if terms
                    else Reservation.TermsAcceptanceSource.PENDING_CUSTOMER
                ),
                confirmed_at=None,
            )
            reservation.full_clean()
            reservation.save()
    except IntegrityError as exc:
        raise ReservationUnavailable(
            _('This dog is already held by another process.')
        ) from exc
    return charge, reservation


def _settle_admin_reservation(
    *,
    reservation,
    charge,
    credit,
    applied_credit,
    payment_provider,
    payment_reference,
    payment_note,
    created_by,
):
    if applied_credit:
        allocate_customer_credit(
            credit_id=credit.pk,
            charge_id=charge.pk,
            amount=applied_credit,
            created_by=created_by,
            reason=_('Applied to a direct reservation by staff.'),
        )
    charge = refresh_charge_status(charge.pk)
    amount_due = charge.amount_due
    if payment_provider == Payment.Provider.STRIPE:
        _schedule_payment_request(
            purchase=reservation,
            notification='reservation',
        )
        return
    if amount_due > 0:
        record_manual_payment(
            charge_id=charge.pk,
            amount=amount_due,
            provider=payment_provider,
            recorded_by=created_by,
            external_reference=payment_reference,
            note=payment_note,
            purchase=reservation,
        )
    elif payment_provider == Payment.Provider.COMPLIMENTARY:
        _record_complimentary_payment(
            charge=charge,
            purchase=reservation,
            recorded_by=created_by,
            note=payment_note,
        )
    synchronize_paid_charge(
        charge.pk,
        admin_user=created_by,
    )


@transaction.atomic
def create_admin_sale(
    *,
    animal_id,
    user,
    customer_data,
    final_price,
    payment_provider,
    sold_at,
    created_by,
    credit=None,
    credit_amount=decimal.Decimal('0.00'),
    payment_reference='',
    payment_note='',
    notes='',
) -> AnimalSale:
    animal = _lock_available_animal(animal_id)
    sale_case = AnimalSaleCase.objects.create(
        **_sale_case_values(
            animal=animal,
            user=user,
            customer_data=customer_data,
            created_by=created_by,
            status=AnimalSaleCase.Status.SOLD,
        ),
    )
    return _complete_sale_locked(
        sale_case=sale_case,
        final_price=final_price,
        payment_provider=payment_provider,
        sold_at=sold_at,
        completed_by=created_by,
        credit=credit,
        credit_amount=credit_amount,
        payment_reference=payment_reference,
        payment_note=payment_note,
        notes=notes,
    )


@transaction.atomic
def complete_existing_sale_case(
    *,
    sale_case_id,
    final_price,
    payment_provider,
    sold_at,
    completed_by,
    credit=None,
    credit_amount=decimal.Decimal('0.00'),
    payment_reference='',
    payment_note='',
    notes='',
) -> AnimalSale:
    sale_case = (
        AnimalSaleCase.objects.select_for_update()
        .select_related('animal')
        .get(pk=sale_case_id)
    )
    if sale_case.status not in {
        AnimalSaleCase.Status.PRE_RESERVATION,
        AnimalSaleCase.Status.RESERVATION,
    }:
        raise ReservationUnavailable(
            _('Only an active sale process can be completed.')
        )
    Animal.objects.select_for_update().get(pk=sale_case.animal_id)
    if Payment.objects.filter(
        charge__sale_case=sale_case,
        status__in=(Payment.Status.INITIALIZING, Payment.Status.PENDING),
    ).exists():
        raise PaymentError(
            _('Close or reconcile active online payments before completing the sale.')
        )
    return _complete_sale_locked(
        sale_case=sale_case,
        final_price=final_price,
        payment_provider=payment_provider,
        sold_at=sold_at,
        completed_by=completed_by,
        credit=credit,
        credit_amount=credit_amount,
        payment_reference=payment_reference,
        payment_note=payment_note,
        notes=notes,
    )


def _complete_sale_locked(
    *,
    sale_case,
    final_price,
    payment_provider,
    sold_at,
    completed_by,
    credit,
    credit_amount,
    payment_reference,
    payment_note,
    notes,
):
    price = money(final_price)
    if price < 0:
        raise PaymentError(_('The final sale price cannot be negative.'))
    committed_value = sum(
        (
            charge.settled_amount
            for charge in sale_case.charges.exclude(stage=Charge.Stage.SALE)
        ),
        decimal.Decimal('0.00'),
    )
    if committed_value > price:
        raise PaymentError(
            _(
                'The value already settled exceeds the final sale price. '
                'Resolve the excess as a refund or customer credit first.'
            )
        )
    final_balance = price - committed_value
    applied_credit = money(credit_amount)
    if applied_credit < 0 or applied_credit > final_balance:
        raise PaymentError(
            _('Customer credit cannot exceed the final outstanding balance.')
        )
    if applied_credit and credit is None:
        raise PaymentError(_('Choose the customer credit to apply.'))

    charge = create_charge(
        sale_case=sale_case,
        stage=Charge.Stage.SALE,
        subtotal_amount=final_balance,
        currency=sale_case.currency,
        created_by=completed_by,
    )
    if applied_credit:
        allocate_customer_credit(
            credit_id=credit.pk,
            charge_id=charge.pk,
            amount=applied_credit,
            created_by=completed_by,
            reason=_('Applied to the final animal sale.'),
        )
    charge = refresh_charge_status(charge.pk)
    _validate_complimentary_balance(
        payment_provider=payment_provider,
        amount_due=charge.amount_due,
        note=payment_note,
    )
    if charge.amount_due > 0:
        record_manual_payment(
            charge_id=charge.pk,
            amount=charge.amount_due,
            provider=payment_provider,
            recorded_by=completed_by,
            external_reference=payment_reference,
            note=payment_note,
        )
    elif payment_provider == Payment.Provider.COMPLIMENTARY:
        _record_complimentary_payment(
            charge=charge,
            recorded_by=completed_by,
            note=payment_note,
        )

    sale = AnimalSale.objects.create(
        sale_case=sale_case,
        charge=charge,
        final_price=price,
        sold_at=sold_at,
        notes=notes.strip(),
        completed_by=completed_by,
    )
    sale_case.status = AnimalSaleCase.Status.SOLD
    sale_case.closed_at = timezone.now()
    sale_case.save(
        update_fields=['status', 'closed_at', 'updated_at'],
    )
    _schedule_settled_side_effects(
        charge=charge,
        purchase=sale,
        notification='sale',
    )
    return sale


@transaction.atomic
def cancel_animal_sale(
    *,
    animal_sale_id,
    reason,
    refund_amount,
    credit_amount,
    cancelled_by,
    provider_loss_acknowledged=False,
):
    from reservations.models import WorkflowClosure
    from reservations.services.closures import record_workflow_closure

    sale = (
        AnimalSale.objects.select_for_update()
        .select_related('sale_case__animal')
        .get(pk=animal_sale_id)
    )
    if sale.voided_at is not None:
        raise ReservationUnavailable(_('This sale is already cancelled.'))
    if sale.sale_case.status != AnimalSaleCase.Status.SOLD:
        raise ReservationUnavailable(
            _('Only a completed sale can be cancelled.')
        )
    if sale.sale_case.animal_id:
        Animal.objects.select_for_update().get(
            pk=sale.sale_case.animal_id,
        )

    closure, refunds = record_workflow_closure(
        sale_case=sale.sale_case,
        stage=Charge.Stage.SALE,
        kind=WorkflowClosure.Kind.SALE_CANCELLED,
        reason=reason,
        refund_amount=refund_amount,
        credit_amount=credit_amount,
        created_by=cancelled_by,
        provider_loss_acknowledged=provider_loss_acknowledged,
    )
    now = timezone.now()
    sale.voided_at = now
    sale.voided_by = cancelled_by
    sale.void_reason = reason.strip()
    sale.save(
        update_fields=['voided_at', 'voided_by', 'void_reason'],
    )
    try:
        reservation = sale.sale_case.reservation
    except Reservation.DoesNotExist:
        reservation = None
    if reservation is not None:
        reservation.status = Reservation.Status.CANCELLED_BY_ADMIN
        reservation.cancelled_at = now
        reservation.cancelled_by = cancelled_by
        reservation.cancellation_reason = reason.strip()
        reservation.save(
            update_fields=[
                'status',
                'cancelled_at',
                'cancelled_by',
                'cancellation_reason',
                'updated_at',
            ],
        )
    sale.sale_case.status = AnimalSaleCase.Status.CLOSED
    sale.sale_case.closed_at = now
    sale.sale_case.save(
        update_fields=['status', 'closed_at', 'updated_at'],
    )
    transaction.on_commit(
        lambda sale_id=sale.pk: _notify_cancelled_sale(sale_id),
    )
    return sale, closure, refunds


def _notify_cancelled_sale(animal_sale_id):
    from reservations.services.notifications import (
        notify_animal_sale_cancelled,
    )

    notify_animal_sale_cancelled(
        AnimalSale.objects.select_related('sale_case').get(
            pk=animal_sale_id,
        ),
    )


def available_customer_credits(*, user, currency='EUR'):
    if user is None:
        return CustomerCredit.objects.none()
    return CustomerCredit.objects.filter(
        user=user,
        currency=currency,
        status=CustomerCredit.Status.ACTIVE,
    )


def _schedule_payment_request(*, purchase, notification):
    transaction.on_commit(
        lambda purchase_id=purchase.pk, kind=notification: (
            _run_payment_request(
                purchase_id=purchase_id,
                notification=kind,
            )
        )
    )


def _run_payment_request(*, purchase_id, notification):
    from reservations.services.notifications import (
        notify_pre_reservation_payment_requested,
        notify_reservation_payment_requested,
    )

    if notification == 'pre_reservation':
        notify_pre_reservation_payment_requested(
            PreReservation.objects.get(pk=purchase_id),
        )
    else:
        notify_reservation_payment_requested(
            Reservation.objects.select_related(
                'pre_reservation',
                'sale_case',
            ).get(pk=purchase_id),
        )


def _schedule_settled_side_effects(*, charge, purchase, notification):
    transaction.on_commit(
        lambda charge_id=charge.pk, purchase_id=purchase.pk, kind=notification: (
            _run_settled_side_effects(
                charge_id=charge_id,
                purchase_id=purchase_id,
                notification=kind,
            )
        )
    )


def _run_settled_side_effects(*, charge_id, purchase_id, notification):
    from reservations.models import ERPIntegrationAttempt
    from reservations.services.erp import (
        ensure_sale_erp_document,
        process_erp_document,
    )
    from reservations.services.notifications import (
        notify_animal_sale_completed,
        notify_pre_reservation_paid,
        notify_reservation_confirmed,
    )

    charge = Charge.objects.get(pk=charge_id)
    try:
        document = ensure_sale_erp_document(charge)
        if document is not None:
            process_erp_document(
                document.pk,
                trigger=ERPIntegrationAttempt.Trigger.AUTOMATIC,
            )
    except Exception:
        logger.exception(
            'Unable to initialize ERP processing for an admin sale workflow',
            extra={'charge_id': charge_id},
        )

    if notification == 'pre_reservation':
        notify_pre_reservation_paid(
            PreReservation.objects.get(pk=purchase_id),
        )
    elif notification == 'reservation':
        notify_reservation_confirmed(
            Reservation.objects.select_related(
                'pre_reservation',
                'sale_case',
            ).get(pk=purchase_id),
        )
    else:
        notify_animal_sale_completed(
            AnimalSale.objects.select_related('sale_case').get(
                pk=purchase_id,
            ),
        )


@transaction.atomic
def synchronize_paid_charge(charge_id, *, admin_user=None):
    charge = (
        Charge.objects.select_for_update()
        .select_related('sale_case')
        .get(pk=charge_id)
    )
    charge = refresh_charge_status(charge.pk)
    if charge.amount_due > 0:
        return charge.purchase
    now = timezone.now()
    notification = None
    if charge.stage == Charge.Stage.PRE_RESERVATION:
        purchase = charge.pre_reservation_stage
        if purchase.status in {
            PreReservation.Status.PENDING_PAYMENT,
            PreReservation.Status.PAYMENT_FAILED,
            PreReservation.Status.EXPIRED,
        }:
            if (
                purchase.terms_acceptance_source
                == PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER
            ):
                raise ReservationUnavailable(
                    _(
                        'Record the customer acceptance of the current '
                        'pre-reservation terms before settling this stage.'
                    )
                )
            purchase.status = PreReservation.Status.AWAITING_REVIEW
            purchase.confirmed_at = now
            purchase.hold_expires_at = None
            purchase.save(
                update_fields=[
                    'status',
                    'confirmed_at',
                    'hold_expires_at',
                    'updated_at',
                ],
            )
            notification = 'pre_reservation'
    elif charge.stage == Charge.Stage.RESERVATION:
        purchase = charge.reservation_stage
        if purchase.status in {
            Reservation.Status.OFFERED,
            Reservation.Status.PENDING_PAYMENT,
            Reservation.Status.PAYMENT_FAILED,
        }:
            if (
                purchase.terms_acceptance_source
                == Reservation.TermsAcceptanceSource.PENDING_CUSTOMER
            ):
                raise ReservationUnavailable(
                    _(
                        'Record the customer acceptance of the current '
                        'reservation terms before settling this stage.'
                    )
                )
            purchase.status = Reservation.Status.CONFIRMED
            purchase.confirmed_at = now
            purchase.save(
                update_fields=['status', 'confirmed_at', 'updated_at'],
            )
            if purchase.pre_reservation_id:
                PreReservation.objects.filter(
                    pk=purchase.pre_reservation_id,
                ).update(
                    status=PreReservation.Status.CONVERTED_TO_RESERVATION,
                    updated_at=now,
                )
            notification = 'reservation'
    else:
        purchase = getattr(charge, 'sale_stage', None)
    if charge.stage in {
        Charge.Stage.PRE_RESERVATION,
        Charge.Stage.RESERVATION,
    }:
        if charge.stage == Charge.Stage.RESERVATION:
            charge.sale_case.status = AnimalSaleCase.Status.RESERVATION
        charge.sale_case.closed_at = None
        charge.sale_case.save(
            update_fields=['status', 'closed_at', 'updated_at'],
        )
    if notification is not None:
        _schedule_settled_side_effects(
            charge=charge,
            purchase=purchase,
            notification=notification,
        )
    if (
        charge.stage == Charge.Stage.PRE_RESERVATION
        and charge.sale_case.origin == AnimalSaleCase.Origin.ADMIN
    ):
        accept_staff_created_pre_reservation(
            purchase.pk,
            admin_user=admin_user,
        )
        purchase.refresh_from_db()
    return purchase
