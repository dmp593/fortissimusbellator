from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext as _
from django.utils.translation import override

from fortissimusbellator.emails import (
    BrandedEmailContent,
    EmailAction,
    EmailDetail,
    absolute_reverse,
    absolute_url,
    format_email_date,
    format_email_money,
)
from reservations.models import Charge, PreReservation, Reservation


def pre_reservation_payment_requested_email(
    pre_reservation,
    *,
    internal=False,
    language_code=None,
):
    workflow = _workflow(pre_reservation)
    language_code = language_code or workflow.language_code
    with override(language_code):
        details = [
            EmailDetail(
                _('Amount due'),
                format_email_money(
                    pre_reservation.charge.amount_due,
                    pre_reservation.currency,
                ),
                highlight=True,
            ),
            EmailDetail(_('Reference'), str(pre_reservation.public_id)),
        ]
        details = _with_customer_details(workflow, details, internal)
        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Complete your pre-reservation: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Complete your pre-reservation'),
            preheader=_(
                'The breeder created a pre-reservation for you to review and '
                'pay online.'
            ),
            eyebrow=_('Pre-reservation'),
            status_label=_('Awaiting payment'),
            tone='warning',
            intro=(
                _(
                    'Staff created a pre-reservation and the customer must '
                    'review the terms and complete payment.'
                )
                if internal
                else _(
                    'The breeder created a pre-reservation for %(name)s. '
                    'Review the current terms and complete the secure online '
                    'payment from your account.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('No duplicate process will be created'),
            notice=_(
                'The dog is already held by this process. Continuing from '
                'the link below uses the same pre-reservation and records '
                'your terms acceptance.'
            ),
            primary_action=(
                _admin_action(workflow, language_code)
                if internal
                else _pre_reservation_checkout_action(
                    workflow,
                    pre_reservation,
                    language_code,
                )
            ),
            secondary_action=EmailAction(
                _('Read the pre-reservation terms'),
                absolute_reverse(
                    'pre_reservation_terms',
                    language_code=language_code,
                ),
            ),
            reference=str(pre_reservation.public_id),
        )


def reservation_payment_requested_email(
    reservation,
    *,
    internal=False,
    language_code=None,
):
    workflow = _workflow(reservation)
    language_code = language_code or workflow.language_code
    with override(language_code):
        details = [
            EmailDetail(
                _('Amount due'),
                format_email_money(
                    reservation.charge.amount_due,
                    reservation.currency,
                ),
                highlight=True,
            ),
            EmailDetail(
                _('Offer valid until'),
                format_email_date(reservation.offer_expires_at),
            ),
            EmailDetail(_('Reference'), str(reservation.public_id)),
        ]
        details = _with_customer_details(workflow, details, internal)
        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Complete your reservation: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Complete your reservation'),
            preheader=_(
                'The breeder created a reservation offer for you to review '
                'and complete online.'
            ),
            eyebrow=_('Reservation'),
            status_label=_('Action required'),
            tone='warning',
            intro=(
                _(
                    'Staff created a direct reservation offer and the '
                    'customer must complete it before the deadline.'
                )
                if internal
                else _(
                    'The breeder created a reservation offer for %(name)s. '
                    'Review the terms and complete it before the deadline.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('Complete before the deadline'),
            notice=_(
                'The dog remains pre-reserved while this offer is active. '
                'The public status changes to Reserved only after the '
                'reservation is fully settled.'
            ),
            primary_action=(
                _admin_action(workflow, language_code)
                if internal
                else _reservation_checkout_action(
                    workflow,
                    reservation,
                    language_code,
                )
            ),
            secondary_action=EmailAction(
                _('Read the reservation terms'),
                absolute_reverse(
                    'reservation_terms',
                    language_code=language_code,
                ),
            ),
            reference=str(reservation.public_id),
        )


def pre_reservation_paid_email(
    pre_reservation,
    *,
    internal=False,
    language_code=None,
):
    workflow = _workflow(pre_reservation)
    language_code = language_code or workflow.language_code
    with override(language_code):
        details = [
            EmailDetail(
                _('Amount paid'),
                format_email_money(
                    pre_reservation.total_amount,
                    pre_reservation.currency,
                ),
                highlight=True,
            ),
        ]
        if pre_reservation.promotion_code:
            details.append(
                EmailDetail(
                    _('Promotion'),
                    (
                        f'{pre_reservation.promotion_code} · '
                        f'-{format_email_money(
                            pre_reservation.discount_amount,
                            pre_reservation.currency,
                        )}'
                    ),
                    highlight=True,
                )
            )
        details.append(
            EmailDetail(_('Reference'), str(pre_reservation.public_id))
        )
        details = _with_customer_details(workflow, details, internal)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Pre-reservation payment received: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Pre-reservation payment received'),
            preheader=_(
                'Your payment was received and the breeder will now review '
                'your request.'
            ),
            eyebrow=_('Pre-reservation'),
            status_label=_('Awaiting breeder review'),
            tone='warning',
            intro=(
                _(
                    'A paid pre-reservation is ready for breeder review.'
                )
                if internal
                else _(
                    'We received your payment for %(name)s. Your '
                    'pre-reservation is now waiting for the breeder\'s '
                    'personal review.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=(
                _('Next action')
                if internal
                else _('What happens next?')
            ),
            notice=(
                _(
                    'Review the customer and record the decision in the '
                    'administration area. There is no automatic deadline for '
                    'this review.'
                )
                if internal
                else _(
                    'The breeder may contact you before making a decision. '
                    'There is no automatic deadline while this review is '
                    'pending. The pre-reservation fee is non-refundable by '
                    'nature unless the breeder expressly approves an '
                    'exception.'
                )
            ),
            primary_action=_primary_action(workflow, internal),
            secondary_action=EmailAction(
                _('Read the pre-reservation terms'),
                absolute_reverse(
                    'pre_reservation_terms',
                    language_code=language_code,
                ),
            ),
            reference=str(pre_reservation.public_id),
        )


def pre_reservation_accepted_email(
    pre_reservation,
    *,
    internal=False,
    language_code=None,
):
    workflow = _workflow(pre_reservation)
    reservation = pre_reservation.reservation
    language_code = language_code or workflow.language_code
    with override(language_code):
        details = [
            EmailDetail(
                _('Reservation deposit target'),
                format_email_money(
                    reservation.deposit_target_amount,
                    reservation.currency,
                ),
            ),
            EmailDetail(
                _('Pre-reservation credit'),
                format_email_money(
                    reservation.pre_reservation_credit_amount,
                    reservation.currency,
                ),
            ),
        ]
        if reservation.customer_credit_amount:
            details.append(
                EmailDetail(
                    _('Customer credit'),
                    format_email_money(
                        reservation.customer_credit_amount,
                        reservation.currency,
                    ),
                )
            )
        if reservation.discount_amount:
            details.append(
                EmailDetail(
                    _('Reservation discount'),
                    (
                        f'-{format_email_money(
                            reservation.discount_amount,
                            reservation.currency,
                        )}'
                    ),
                    highlight=True,
                )
            )
        details.extend(
            [
                EmailDetail(
                    _('Amount still payable'),
                    format_email_money(
                        reservation.payment_amount,
                        reservation.currency,
                    ),
                    highlight=True,
                ),
                EmailDetail(
                    _('Offer valid until'),
                    format_email_date(reservation.offer_expires_at),
                ),
                EmailDetail(_('Reference'), str(reservation.public_id)),
            ]
        )
        details = _with_customer_details(workflow, details, internal)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Pre-reservation accepted: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Your pre-reservation was accepted'),
            preheader=_(
                'Complete the reservation deposit before the indicated '
                'deadline.'
            ),
            eyebrow=_('Reservation offer'),
            status_label=_('Action required'),
            tone='warning',
            intro=(
                _(
                    'The pre-reservation was accepted and the reservation '
                    'offer is now available to the customer.'
                )
                if internal
                else _(
                    'The breeder accepted your pre-reservation for %(name)s. '
                    'You can now complete the reservation deposit.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('Complete before the deadline'),
            notice=_(
                'The offer expires automatically at the date shown above. '
                'If payment is not completed in time, both the reservation '
                'offer and the pre-reservation expire and the dog becomes '
                'available again.'
            ),
            primary_action=(
                _admin_action(workflow, language_code)
                if internal
                else _reservation_checkout_action(
                    workflow,
                    reservation,
                    language_code,
                )
            ),
            secondary_action=EmailAction(
                _('Read the reservation terms'),
                absolute_reverse(
                    'reservation_terms',
                    language_code=language_code,
                ),
            ),
            reference=str(reservation.public_id),
        )


def reservation_confirmed_email(
    reservation,
    *,
    internal=False,
    language_code=None,
):
    workflow = _workflow(reservation)
    language_code = language_code or workflow.language_code
    with override(language_code):
        details = [
            EmailDetail(
                _('Reservation deposit paid now'),
                format_email_money(
                    reservation.payment_amount,
                    reservation.currency,
                ),
                highlight=True,
            ),
            EmailDetail(
                _('Total credited toward the dog'),
                format_email_money(
                    reservation.deposit_target_amount,
                    reservation.currency,
                ),
            ),
        ]
        payment = _payment(reservation)
        if payment is not None:
            details.append(
                EmailDetail(
                    _('Payment method'),
                    str(payment.get_provider_display()),
                )
            )
        details.append(
            EmailDetail(_('Reference'), str(reservation.public_id))
        )
        details = _with_customer_details(workflow, details, internal)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Reservation confirmed: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Reservation confirmed'),
            preheader=_(
                'The reservation deposit is confirmed and the dog is now '
                'reserved.'
            ),
            eyebrow=_('Reservation'),
            status_label=_('Reserved'),
            tone='success',
            intro=(
                _('The reservation deposit was confirmed.')
                if internal
                else _(
                    'Your reservation for %(name)s is confirmed. The dog is '
                    'now reserved for you.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('Your records'),
            notice=_(
                'Payment history, applied promotions and available fiscal '
                'documents remain accessible from the reservations '
                'dashboard.'
            ),
            primary_action=_primary_action(workflow, internal),
            reference=str(reservation.public_id),
        )


def reservation_cancelled_email(
    reservation,
    *,
    internal=False,
    language_code=None,
):
    workflow = _workflow(reservation)
    language_code = language_code or workflow.language_code
    closure = _latest_closure(workflow, Charge.Stage.RESERVATION)
    with override(language_code):
        details = [
            EmailDetail(_('Reference'), str(reservation.public_id)),
        ]
        if reservation.cancellation_reason:
            details.append(
                EmailDetail(_('Reason'), reservation.cancellation_reason)
            )
        details.extend(_closure_details(closure, workflow.currency))
        details = _with_customer_details(workflow, details, internal)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Reservation cancelled: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Reservation cancelled'),
            preheader=_(
                'The reservation was cancelled and the financial decision '
                'is recorded in the history.'
            ),
            eyebrow=_('Reservation'),
            status_label=_('Cancelled by staff'),
            tone='danger',
            intro=(
                _('The reservation was cancelled by the breeder.')
                if internal
                else _(
                    'Our team cancelled your reservation for %(name)s.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('Financial outcome'),
            notice=_closure_notice(closure),
            primary_action=_primary_action(workflow, internal),
            reference=str(reservation.public_id),
        )


def pre_reservation_closed_email(
    pre_reservation,
    *,
    rejected,
    cancelled_by_staff,
    internal=False,
    language_code=None,
):
    workflow = _workflow(pre_reservation)
    language_code = language_code or workflow.language_code
    closure = _latest_closure(workflow, Charge.Stage.PRE_RESERVATION)
    with override(language_code):
        details = [
            EmailDetail(_('Reference'), str(pre_reservation.public_id)),
        ]
        if pre_reservation.cancellation_reason:
            details.append(
                EmailDetail(
                    _('Reason'),
                    pre_reservation.cancellation_reason,
                )
            )
        details.extend(_closure_details(closure, workflow.currency))
        details = _with_customer_details(workflow, details, internal)

        title = (
            _('Pre-reservation not accepted')
            if rejected
            else _('Pre-reservation cancelled')
        )
        return _content(
            workflow=workflow,
            internal=internal,
            subject=(
                _('Pre-reservation not accepted: %(name)s')
                if rejected
                else _('Pre-reservation cancelled: %(name)s')
            )
            % {'name': workflow.target_name},
            title=title,
            preheader=_(
                'The pre-reservation process was closed and its financial '
                'outcome is recorded.'
            ),
            eyebrow=_('Pre-reservation'),
            status_label=(
                _('Not accepted')
                if rejected
                else (
                    _('Cancelled by staff')
                    if cancelled_by_staff
                    else _('Cancelled by customer')
                )
            ),
            tone='danger' if rejected or cancelled_by_staff else 'neutral',
            intro=_pre_reservation_closed_intro(
                workflow,
                rejected=rejected,
                cancelled_by_staff=cancelled_by_staff,
                internal=internal,
            ),
            details=details,
            notice_title=_('Financial outcome'),
            notice=_closure_notice(closure),
            primary_action=_primary_action(workflow, internal),
            reference=str(pre_reservation.public_id),
        )


def late_payment_refund_email(
    purchase,
    *,
    internal=False,
    language_code=None,
):
    workflow = _workflow(purchase)
    language_code = language_code or workflow.language_code
    payment = _payment(purchase)
    with override(language_code):
        details = []
        if payment is not None:
            details.extend(
                [
                    EmailDetail(
                        _('Amount received'),
                        format_email_money(
                            payment.amount,
                            payment.currency,
                        ),
                    ),
                    EmailDetail(
                        _('Payment method'),
                        str(payment.get_provider_display()),
                    ),
                ]
            )
        details.append(
            EmailDetail(_('Reference'), str(purchase.public_id))
        )
        details = _with_customer_details(workflow, details, internal)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Payment received after process closure: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Late payment received'),
            preheader=_(
                'A full safety refund was queued because the process had '
                'already closed.'
            ),
            eyebrow=_('Payment protection'),
            status_label=_('Refund queued'),
            tone='warning',
            intro=(
                _(
                    'A payment arrived after the commercial process had '
                    'already closed. A full safety refund was queued.'
                )
                if internal
                else _(
                    'We received a payment after your process for %(name)s '
                    'had already closed. To protect you, we immediately '
                    'queued a full refund.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('No action is required'),
            notice=_(
                'We will send another email when the refund is confirmed. '
                'Bank processing times may delay when the amount appears in '
                'the original payment method.'
            ),
            primary_action=_primary_action(workflow, internal),
            reference=str(purchase.public_id),
        )


def refund_succeeded_email(
    payment_refund,
    *,
    internal=False,
    language_code=None,
):
    payment = payment_refund.payment
    purchase = payment.purchase
    workflow = _workflow(purchase)
    language_code = language_code or workflow.language_code
    with override(language_code):
        details = [
            EmailDetail(
                _('Refund amount'),
                format_email_money(
                    payment_refund.amount,
                    payment.currency,
                ),
                highlight=True,
            ),
            EmailDetail(
                _('Original payment method'),
                str(payment.get_provider_display()),
            ),
            EmailDetail(
                _('Refund reference'),
                str(payment_refund.public_id),
            ),
        ]
        if payment_refund.reason:
            details.append(
                EmailDetail(_('Reason'), payment_refund.reason)
            )
        details = _with_customer_details(workflow, details, internal)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Refund completed: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Refund completed'),
            preheader=_(
                'The approved refund was completed successfully.'
            ),
            eyebrow=_('Refund'),
            status_label=_('Completed'),
            tone='success',
            intro=(
                _('The approved refund was completed successfully.')
                if internal
                else _(
                    'The approved refund for %(name)s was completed '
                    'successfully.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('When will the amount arrive?'),
            notice=_(
                'The refund has left our system. The bank or card issuer may '
                'take several business days to show it in the original '
                'payment method.'
            ),
            primary_action=_primary_action(workflow, internal),
            reference=str(payment_refund.public_id),
        )


def workflow_transferred_email(
    workflow_transfer,
    *,
    internal=False,
    language_code=None,
):
    source = workflow_transfer.source_case
    target = workflow_transfer.target_case
    language_code = language_code or source.language_code
    with override(language_code):
        details = [
            EmailDetail(_('Previous dog'), source.target_name),
            EmailDetail(_('New dog'), target.target_name),
            EmailDetail(
                _('Transferred value'),
                format_email_money(
                    workflow_transfer.transferred_amount,
                    source.currency,
                ),
                highlight=True,
            ),
            EmailDetail(
                _('Refund'),
                format_email_money(
                    workflow_transfer.refund_amount,
                    source.currency,
                ),
            ),
            EmailDetail(
                _('Retained'),
                format_email_money(
                    workflow_transfer.retained_amount,
                    source.currency,
                ),
            ),
        ]
        target_charge = _target_charge(target, workflow_transfer.target_stage)
        if target_charge is not None:
            details.append(
                EmailDetail(
                    _('Outstanding'),
                    format_email_money(
                        target_charge.amount_due,
                        target_charge.currency,
                    ),
                    highlight=target_charge.amount_due > 0,
                )
            )
        if workflow_transfer.reason:
            details.append(
                EmailDetail(_('Reason'), workflow_transfer.reason)
            )
        details.append(
            EmailDetail(_('Reference'), str(workflow_transfer.public_id))
        )
        details = _with_customer_details(target, details, internal)

        return _content(
            workflow=target,
            internal=internal,
            subject=_('Your process was transferred to %(name)s') % {
                'name': target.target_name,
            },
            title=_('Process transferred'),
            preheader=_(
                'Your commercial process and the agreed value were moved to '
                'another dog.'
            ),
            eyebrow=_('Change of dog'),
            status_label=_('Transferred'),
            tone='info',
            intro=(
                _(
                    'The commercial process was transferred from '
                    '%(source)s to %(target)s.'
                )
                if internal
                else _(
                    'As agreed with the breeder, your process was transferred '
                    'from %(source)s to %(target)s.'
                )
            )
            % {
                'source': source.target_name,
                'target': target.target_name,
            },
            details=details,
            notice_title=_('Review the updated process'),
            notice=_(
                'The new process shows the value already transferred, any '
                'refund or retained amount, and any difference still payable.'
            ),
            primary_action=_primary_action(target, internal),
            reference=str(workflow_transfer.public_id),
        )


def animal_sale_completed_email(
    animal_sale,
    *,
    internal=False,
    language_code=None,
):
    workflow = animal_sale.sale_case
    language_code = language_code or workflow.language_code
    with override(language_code):
        details = [
            EmailDetail(
                _('Final agreed price'),
                format_email_money(
                    animal_sale.final_price,
                    workflow.currency,
                ),
                highlight=True,
            ),
            EmailDetail(
                _('Sold at'),
                format_email_date(animal_sale.sold_at),
            ),
            EmailDetail(_('Reference'), str(animal_sale.public_id)),
        ]
        details = _with_customer_details(workflow, details, internal)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Sale completed: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Sale completed'),
            preheader=_(
                'The sale is complete and remains recorded in your history.'
            ),
            eyebrow=_('Final sale'),
            status_label=_('Sold'),
            tone='success',
            intro=(
                _('The animal sale was marked as completed.')
                if internal
                else _(
                    'The sale of %(name)s is complete. Thank you for trusting '
                    'Fortissimus Bellator.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('Your complete history'),
            notice=_(
                'Payments, credits, refunds and available fiscal documents '
                'remain accessible from your reservations dashboard.'
            ),
            primary_action=_primary_action(workflow, internal),
            reference=str(animal_sale.public_id),
        )


def animal_sale_cancelled_email(
    animal_sale,
    *,
    internal=False,
    language_code=None,
):
    workflow = animal_sale.sale_case
    language_code = language_code or workflow.language_code
    closure = _latest_closure(workflow, Charge.Stage.SALE)
    with override(language_code):
        details = [
            EmailDetail(
                _('Sold at'),
                format_email_date(animal_sale.sold_at),
            ),
            EmailDetail(_('Reference'), str(animal_sale.public_id)),
        ]
        if animal_sale.final_price is not None:
            details.insert(
                0,
                EmailDetail(
                    _('Final agreed price'),
                    format_email_money(
                        animal_sale.final_price,
                        workflow.currency,
                    ),
                ),
            )
        if animal_sale.void_reason:
            details.append(
                EmailDetail(_('Reason'), animal_sale.void_reason),
            )
        details.extend(_closure_details(closure, workflow.currency))
        details = _with_customer_details(workflow, details, internal)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Sale cancelled: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Sale cancelled'),
            preheader=_(
                'The sale was cancelled and its financial outcome remains '
                'recorded in the history.'
            ),
            eyebrow=_('Final sale'),
            status_label=_('Cancelled by staff'),
            tone='danger',
            intro=(
                _('The animal sale was cancelled by staff.')
                if internal
                else _(
                    'Our team cancelled the sale of %(name)s.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('Financial outcome'),
            notice=_closure_notice(closure),
            primary_action=_primary_action(workflow, internal),
            reference=str(animal_sale.public_id),
        )


def fiscal_document_email(
    document,
    *,
    internal=False,
    language_code=None,
):
    purchase = document.purchase
    workflow = _workflow(purchase)
    language_code = language_code or workflow.language_code
    with override(language_code):
        details = [
            EmailDetail(
                _('Document type'),
                str(document.get_kind_display()),
            ),
            EmailDetail(
                _('Document amount'),
                format_email_money(document.amount, document.currency),
                highlight=True,
            ),
            EmailDetail(
                _('Document reference'),
                document.external_reference,
            ),
        ]
        if document.erp_document_number:
            details.append(
                EmailDetail(
                    _('Document number'),
                    document.erp_document_number,
                )
            )
        details = _with_customer_details(workflow, details, internal)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Fiscal document for %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Your fiscal document'),
            preheader=_(
                'The fiscal document is attached to this email as a PDF.'
            ),
            eyebrow=_('Fiscal documentation'),
            status_label=_('PDF attached'),
            tone='success',
            intro=(
                _('The requested fiscal document is attached as a PDF.')
                if internal
                else _(
                    'Your fiscal document for %(name)s is attached to this '
                    'email as a PDF.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('Keep this document'),
            notice=_(
                'You can download the document again from your reservations '
                'dashboard whenever it remains available.'
            ),
            primary_action=_primary_action(workflow, internal),
            reference=document.external_reference,
            footer_note=_(
                'For your security, confirm that the attached PDF and the '
                'reference above match your transaction.'
            ),
        )


def payment_failed_email(
    purchase,
    *,
    expired,
    internal=False,
    language_code=None,
):
    workflow = _workflow(purchase)
    language_code = language_code or workflow.language_code
    payment = _payment(purchase)
    is_reservation = isinstance(purchase, Reservation)
    with override(language_code):
        details = [
            EmailDetail(
                _('Attempted amount'),
                format_email_money(
                    payment.amount if payment else purchase.payment_amount,
                    (
                        payment.currency
                        if payment
                        else purchase.currency
                    ),
                ),
            ),
            EmailDetail(_('Reference'), str(purchase.public_id)),
        ]
        if is_reservation and purchase.offer_expires_at:
            details.append(
                EmailDetail(
                    _('Offer valid until'),
                    format_email_date(purchase.offer_expires_at),
                )
            )
        details = _with_customer_details(workflow, details, internal)

        if is_reservation:
            customer_notice = _(
                'The dog remains held for you until the reservation offer '
                'deadline. You can safely start a new payment attempt from '
                'the checkout page.'
            )
            customer_action = _reservation_checkout_action(
                workflow,
                purchase,
                language_code,
            )
        else:
            customer_notice = _(
                'The temporary hold was released. You may try again from the '
                'dog page or your history if the dog is still available.'
            )
            customer_action = _primary_action(workflow, False)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Payment not completed: %(name)s') % {
                'name': workflow.target_name,
            },
            title=(
                _('Payment link expired')
                if expired
                else _('Payment was not completed')
            ),
            preheader=_(
                'No successful charge was confirmed for this payment '
                'attempt.'
            ),
            eyebrow=_('Payment'),
            status_label=_('Payment failed') if not expired else _('Expired'),
            tone='danger',
            intro=(
                _(
                    'A payment attempt ended without a confirmed charge.'
                )
                if internal
                else _(
                    'We could not confirm a successful payment for %(name)s. '
                    'No completed charge was recorded for this attempt.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('What you can do'),
            notice=(
                _(
                    'Review the process and payment attempt in the '
                    'administration area.'
                )
                if internal
                else customer_notice
            ),
            primary_action=(
                _admin_action(workflow, language_code)
                if internal
                else customer_action
            ),
            reference=str(purchase.public_id),
        )


def reservation_offer_expired_email(
    reservation,
    *,
    internal=False,
    language_code=None,
):
    workflow = _workflow(reservation)
    language_code = language_code or workflow.language_code
    with override(language_code):
        details = [
            EmailDetail(
                _('Offer expired at'),
                format_email_date(reservation.offer_expires_at),
            ),
            EmailDetail(_('Reference'), str(reservation.public_id)),
        ]
        details = _with_customer_details(workflow, details, internal)

        return _content(
            workflow=workflow,
            internal=internal,
            subject=_('Reservation offer expired: %(name)s') % {
                'name': workflow.target_name,
            },
            title=_('Reservation offer expired'),
            preheader=_(
                'The reservation deposit was not completed before the '
                'deadline.'
            ),
            eyebrow=_('Reservation'),
            status_label=_('Expired'),
            tone='danger',
            intro=(
                _(
                    'The reservation offer expired before payment was '
                    'completed.'
                )
                if internal
                else _(
                    'The deadline to reserve %(name)s passed before the '
                    'deposit was completed.'
                )
                % {'name': workflow.target_name}
            ),
            details=details,
            notice_title=_('The dog is available again'),
            notice=_(
                'The reservation offer and its pre-reservation were closed. '
                'To proceed again, a new pre-reservation process must be '
                'started if the dog is still available.'
            ),
            primary_action=_primary_action(workflow, internal),
            reference=str(reservation.public_id),
        )


def erp_needs_attention_email(document, *, language_code=None):
    purchase = document.purchase
    workflow = _workflow(purchase)
    language_code = language_code or settings.LANGUAGE_CODE
    with override(language_code):
        details = [
            EmailDetail(
                _('Document reference'),
                document.external_reference,
            ),
            EmailDetail(
                _('Document type'),
                str(document.get_kind_display()),
            ),
            EmailDetail(
                _('Document amount'),
                format_email_money(document.amount, document.currency),
            ),
            EmailDetail(
                _('Attempts'),
                str(document.attempt_count),
            ),
            EmailDetail(
                _('Last error'),
                (document.last_error or _('Unknown error'))[:500],
            ),
        ]
        details = _with_customer_details(workflow, details, True)
        return _content(
            workflow=workflow,
            internal=True,
            subject=_('ERP integration needs attention: %(reference)s') % {
                'reference': document.external_reference,
            },
            title=_('ERP integration needs attention'),
            preheader=_(
                'A paid transaction still needs fiscal integration.'
            ),
            eyebrow=_('Accounting integration'),
            status_label=_('Requires attention'),
            tone='danger',
            intro=_(
                'A settled transaction could not be integrated with the ERP '
                'after the configured automatic attempts.'
            ),
            details=details,
            notice_title=_('Manual review required'),
            notice=_(
                'Check whether the document already exists in TOConline '
                'before retrying, especially when the creation result is '
                'uncertain.'
            ),
            primary_action=EmailAction(
                _('Open ERP document in administration'),
                absolute_reverse(
                    'admin:reservations_erpdocument_change',
                    args=[document.pk],
                    language_code=language_code,
                ),
            ),
            reference=document.external_reference,
        )


def _content(
    *,
    workflow,
    internal,
    subject,
    title,
    preheader,
    eyebrow,
    status_label,
    tone,
    intro,
    details,
    notice_title,
    notice,
    primary_action,
    reference,
    secondary_action=None,
    footer_note='',
):
    target_url = _target_url(workflow)
    return BrandedEmailContent(
        subject=subject,
        title=title,
        preheader=preheader,
        eyebrow=eyebrow,
        intro=intro,
        recipient_name='' if internal else workflow.customer_name,
        status_label=status_label,
        tone=tone,
        details=tuple(details),
        notice_title=notice_title,
        notice=notice,
        primary_action=primary_action,
        secondary_action=secondary_action,
        reference=reference,
        target_name=workflow.target_name,
        target_breed=workflow.target_breed,
        target_image_url=_target_image_url(workflow),
        target_url=target_url,
        footer_note=footer_note,
        internal=internal,
    )


def _workflow(purchase):
    if isinstance(purchase, PreReservation):
        return purchase.sale_case or purchase
    workflow = getattr(purchase, 'workflow', None)
    if workflow is not None:
        return workflow
    sale_case = getattr(purchase, 'sale_case', None)
    if sale_case is not None:
        return sale_case
    charge = getattr(purchase, 'charge', None)
    if charge is not None:
        return charge.sale_case
    raise ValueError('The purchase has no sale workflow.')


def _payment(purchase):
    try:
        return purchase.payment
    except (AttributeError, ObjectDoesNotExist):
        return None


def _latest_closure(workflow, stage):
    if not getattr(workflow, 'pk', None):
        return None
    return workflow.closures.filter(stage=stage).order_by(
        '-created_at',
        '-pk',
    ).first()


def _closure_details(closure, currency):
    if closure is None:
        return []
    return [
        EmailDetail(
            _('Refund'),
            format_email_money(closure.refund_amount, currency),
            highlight=closure.refund_amount > 0,
        ),
        EmailDetail(
            _('Customer credit'),
            format_email_money(closure.credit_amount, currency),
            highlight=closure.credit_amount > 0,
        ),
        EmailDetail(
            _('Retained'),
            format_email_money(closure.retained_amount, currency),
        ),
    ]


def _closure_notice(closure):
    if closure is None:
        return _(
            'Any approved refund or customer credit will appear separately '
            'in the reservations history.'
        )
    messages = []
    if closure.refund_amount:
        messages.append(
            _(
                'The approved refund is processed separately and may take '
                'several business days to reach the original payment method.'
            )
        )
    if closure.credit_amount:
        messages.append(
            _(
                'The customer credit is available for a future commercial '
                'process with Fortissimus Bellator.'
            )
        )
    if closure.retained_amount:
        messages.append(
            _(
                'The retained amount is recorded in the process history and '
                'is not scheduled for refund.'
            )
        )
    return ' '.join(messages) or _(
        'No paid value required a refund, customer credit or retention.'
    )


def _pre_reservation_closed_intro(
    workflow,
    *,
    rejected,
    cancelled_by_staff,
    internal,
):
    if internal:
        if rejected:
            return _('The breeder did not accept the pre-reservation.')
        if cancelled_by_staff:
            return _('The breeder cancelled the pre-reservation.')
        return _('The customer cancelled the pre-reservation.')
    if rejected:
        return _(
            'The breeder did not accept your pre-reservation for %(name)s.'
        ) % {'name': workflow.target_name}
    if cancelled_by_staff:
        return _(
            'Our team cancelled your pre-reservation for %(name)s.'
        ) % {'name': workflow.target_name}
    return _(
        'Your pre-reservation for %(name)s was cancelled at your request.'
    ) % {'name': workflow.target_name}


def _with_customer_details(workflow, details, internal):
    if not internal:
        return details
    customer_details = [
        EmailDetail(_('Customer'), workflow.customer_name or '-'),
        EmailDetail(_('Customer email'), workflow.customer_email or '-'),
    ]
    if workflow.customer_phone:
        customer_details.append(
            EmailDetail(_('Customer phone'), workflow.customer_phone)
        )
    return customer_details + list(details)


def _primary_action(workflow, internal):
    language_code = workflow.language_code
    if internal:
        return _admin_action(workflow, language_code)
    if getattr(workflow, 'user_id', None):
        return EmailAction(
            _('Open my reservations'),
            absolute_reverse(
                'reservations:dashboard',
                language_code=language_code,
            ),
        )
    return EmailAction(
        _('Contact us'),
        absolute_reverse('contact_us', language_code=language_code),
    )


def _admin_action(workflow, language_code):
    return EmailAction(
        _('Open in administration'),
        absolute_reverse(
            'admin:reservations_animalsalecase_change',
            args=[workflow.pk],
            language_code=language_code,
        ),
    )


def _reservation_checkout_action(workflow, reservation, language_code):
    if getattr(workflow, 'user_id', None):
        return EmailAction(
            _('Continue to reservation deposit'),
            absolute_reverse(
                'reservations:reservation_checkout',
                args=[reservation.public_id],
                language_code=language_code,
            ),
        )
    return EmailAction(
        _('Contact us'),
        absolute_reverse('contact_us', language_code=language_code),
    )


def _pre_reservation_checkout_action(
    workflow,
    pre_reservation,
    language_code,
):
    if getattr(workflow, 'user_id', None) and workflow.animal_id:
        checkout_url = absolute_reverse(
            'breeding:pre_reserve_dog',
            args=[workflow.animal_id],
            language_code=language_code,
        )
        query = urlencode({'retry': pre_reservation.public_id})
        return EmailAction(
            _('Complete pre-reservation payment'),
            f'{checkout_url}?{query}',
        )
    return EmailAction(
        _('Contact us'),
        absolute_reverse('contact_us', language_code=language_code),
    )


def _target_url(workflow):
    if not getattr(workflow, 'target_is_public', False):
        return ''
    animal_id = getattr(workflow, 'animal_id', None)
    if not animal_id:
        return ''
    return absolute_reverse(
        'breeding:dog_detail',
        args=[animal_id],
        language_code=workflow.language_code,
    )


def _target_image_url(workflow):
    animal = getattr(workflow, 'animal', None)
    if animal is None:
        return ''
    cover = animal.cover
    if cover is None:
        return ''
    try:
        return absolute_url(cover.file.url)
    except (ValueError, AttributeError):
        return ''


def _target_charge(target_case, target_stage):
    return target_case.charges.filter(stage=target_stage).first()
