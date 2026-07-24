import logging
from urllib.parse import urlencode
from uuid import UUID

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.http import content_disposition_header
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from breeding.models import Animal
from discounts.models import Promotion
from discounts.services import (
    PromotionQuote,
    PromotionUnavailable,
    quote_promotion,
)

from .availability import ensure_dog_is_available
from .exceptions import PaymentError, ReservationUnavailable
from .forms import PreReservationCheckoutForm, ReservationCheckoutForm
from .models import (
    AnimalSaleCase,
    AnimalSale,
    Charge,
    CustomerCredit,
    ERPDocument,
    Payment,
    PreReservation,
    PreReservationTerms,
    Reservation,
    ReservationTerms,
)
from .services.erp import ensure_erp_pdf_and_email, process_erp_document
from .services.payment import (
    cancel_customer_pre_reservation,
    fulfill_checkout_session,
    initialize_checkout,
    prepare_failed_checkout_retry,
    process_stripe_webhook,
)
from .services.reservation import (
    create_pending_reservation,
    reopen_failed_reservation,
    start_reservation_payment,
)
from .stripe_gateway import construct_webhook_event


logger = logging.getLogger(__name__)
RETRYABLE_PRE_RESERVATION_STATUSES = (
    PreReservation.Status.PAYMENT_FAILED,
    PreReservation.Status.EXPIRED,
)


@login_required
def reservation_checkout(request, *, target_type: str, target_id: int):
    """Create or retry the dog pre-reservation payment."""
    if target_type != PreReservation.TargetType.DOG:
        messages.error(
            request,
            _(
                'Litters cannot be pre-reserved. Subscribe to birth updates '
                'and choose an individual dog after publication.'
            ),
        )
        return redirect('breeding:litter_detail', target_id)

    terms = PreReservationTerms.objects.current()
    if terms is None:
        messages.error(
            request,
            _('Pre-reservation terms are not currently available.'),
        )
        return redirect('breeding:dog_detail', target_id)

    dog = get_object_or_404(
        Animal.objects.select_related('breed'),
        pk=target_id,
    )
    retry_source = _get_retry_source(
        request=request,
        target_id=target_id,
    )
    try:
        ensure_dog_is_available(
            dog,
            exclude_sale_case_id=(
                retry_source.sale_case_id if retry_source else None
            ),
        )
    except ReservationUnavailable as exc:
        messages.error(request, str(exc))
        return redirect('breeding:dog_detail', target_id)

    checkout_initial = _checkout_initial(
        request.user,
        retry_source=retry_source,
    )
    checkout_subtotal = _pre_reservation_checkout_subtotal(
        dog=dog,
        retry_source=retry_source,
    )
    form, promotion_quote, promotion_error, preview_requested = (
        _pre_reservation_checkout_form(
            request=request,
            terms=terms,
            initial=checkout_initial,
            dog=dog,
            subtotal=checkout_subtotal,
            retry_source=retry_source,
        )
    )
    if _is_checkout_submission(request, preview_requested, form):
        response = _submit_pre_reservation_checkout(
            request=request,
            form=form,
            target_id=target_id,
            retry_source=retry_source,
        )
        if response is not None:
            return response

    return render(
        request,
        'buy_a_dog/pre_reserve.html',
        {
            'item': dog,
            'item_type': PreReservation.TargetType.DOG,
            'form': form,
            'terms': terms,
            'promotion_quote': promotion_quote,
            'promotion_error': promotion_error,
            'checkout_subtotal': checkout_subtotal,
            'checkout_adjustment': (
                retry_source.charge.adjustment_amount
                if retry_source
                else 0
            ),
            'checkout_amount': _pre_reservation_preview_amount(
                retry_source=retry_source,
                promotion_quote=promotion_quote,
                subtotal=checkout_subtotal,
            ),
        },
    )


def _pre_reservation_checkout_form(
    *,
    request,
    terms,
    initial,
    dog,
    subtotal,
    retry_source,
):
    preview_requested = _promotion_preview_requested(request)
    promotion_quote = None
    promotion_error = ''
    if preview_requested:
        initial = _initial_from_post(
            PreReservationCheckoutForm,
            request.POST,
            defaults=initial,
            checkbox_fields={'accept_non_refundable'},
        )
        form = PreReservationCheckoutForm(terms=terms, initial=initial)
        promotion_quote, promotion_error = _promotion_preview(
            code=request.POST.get('promotion_code', ''),
            target=dog,
            user=request.user,
            subtotal=subtotal,
            purchase_stage=Promotion.PurchaseStage.PRE_RESERVATION,
            purchase=retry_source,
        )
    else:
        form = PreReservationCheckoutForm(
            request.POST or None,
            terms=terms,
            initial=initial,
        )
        if request.method == 'GET' and initial.get('promotion_code'):
            promotion_quote, promotion_error = _promotion_preview(
                code=initial['promotion_code'],
                target=dog,
                user=request.user,
                subtotal=subtotal,
                purchase_stage=Promotion.PurchaseStage.PRE_RESERVATION,
                purchase=retry_source,
            )
    return form, promotion_quote, promotion_error, preview_requested


def _promotion_preview_requested(request) -> bool:
    return (
        request.method == 'POST'
        and request.POST.get('action') == 'apply_promotion'
    )


def _is_checkout_submission(request, preview_requested, form) -> bool:
    return (
        request.method == 'POST'
        and not preview_requested
        and form.is_valid()
    )


def _submit_pre_reservation_checkout(
    *,
    request,
    form,
    target_id,
    retry_source,
):
    try:
        pre_reservation = _create_or_reopen_pre_reservation(
            request=request,
            form=form,
            target_id=target_id,
            retry_source=retry_source,
        )
    except (PaymentError, ReservationUnavailable) as exc:
        form.add_error(None, str(exc))
        return None

    if pre_reservation.total_amount == 0:
        messages.success(
            request,
            _(
                'Your pre-reservation is paid and awaiting breeder '
                'review.'
            ),
        )
        return redirect(
            'reservations:pre_reservation_confirmation',
            public_id=pre_reservation.public_id,
        )
    return _initialize_checkout_redirect(request, pre_reservation)


def _create_or_reopen_pre_reservation(
    *,
    request,
    form,
    target_id,
    retry_source,
):
    if retry_source is None:
        return create_pending_reservation(
            user=request.user,
            target_type=PreReservation.TargetType.DOG,
            target_id=target_id,
            checkout_data=form.cleaned_data,
            language_code=request.LANGUAGE_CODE,
        )
    if retry_source.payment.status == Payment.Status.FAILED:
        prepare_failed_checkout_retry(retry_source)
    return reopen_failed_reservation(
        reservation_id=retry_source.pk,
        user=request.user,
        target_type=PreReservation.TargetType.DOG,
        target_id=target_id,
        checkout_data=form.cleaned_data,
        language_code=request.LANGUAGE_CODE,
    )


def _initialize_checkout_redirect(request, purchase):
    success_url, cancel_url = _checkout_urls(request, purchase)
    try:
        checkout_url = initialize_checkout(
            purchase=purchase,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except PaymentError as exc:
        messages.error(request, str(exc))
        return redirect('reservations:dashboard')
    return redirect(checkout_url)


@require_GET
def pre_reservation_terms(request):
    terms = PreReservationTerms.objects.current()
    if terms is None:
        raise Http404
    return render(
        request,
        'reservations/pre_reservation_terms.html',
        {'terms': terms},
    )


@require_GET
def reservation_terms(request):
    terms = ReservationTerms.objects.current()
    if terms is None:
        raise Http404
    return render(
        request,
        'reservations/reservation_terms.html',
        {'terms': terms},
    )


@login_required
def dashboard(request):
    document_queryset = ERPDocument.objects.defer('pdf_data').order_by(
        '-created_at',
    )
    payment_queryset = (
        Payment.objects.select_related('charge')
        .prefetch_related(
            'refunds',
            Prefetch(
                'erp_documents',
                queryset=document_queryset,
            ),
        )
        .order_by('created_at', 'pk')
    )
    charge_queryset = (
        Charge.objects.select_related('promotion')
        .prefetch_related(
            'adjustments',
            'credit_allocations__credit',
            Prefetch('payments', queryset=payment_queryset),
            Prefetch(
                'erp_documents',
                queryset=document_queryset,
            ),
        )
        .order_by('created_at', 'pk')
    )
    sale_cases = list(
        AnimalSaleCase.objects.filter(user=request.user)
        .annotate(
            animal_has_completed_sale=Exists(
                AnimalSale.objects.filter(
                    sale_case__animal_id=OuterRef('animal_id'),
                    voided_at__isnull=True,
                )
            )
        )
        .select_related(
            'animal',
            'pre_reservation',
            'reservation',
            'sale',
            'incoming_transfer__source_case',
        )
        .prefetch_related(
            'animal__files',
            Prefetch('charges', queryset=charge_queryset),
            'closures',
            'outgoing_transfers__target_case',
        )
    )
    customer_credits = list(
        CustomerCredit.objects.filter(user=request.user)
        .select_related(
            'source_sale_case',
            'source_transfer__source_case',
            'source_transfer__target_case',
        )
        .prefetch_related('allocations')
        .order_by('-created_at', '-pk')
    )
    active = []
    history = []
    for sale_case in sale_cases:
        _attach_dashboard_workflow(sale_case)
        destination = (
            active
            if sale_case.is_active
            else history
        )
        destination.append(sale_case)
    return render(
        request,
        'reservations/dashboard.html',
        {
            'active_reservations': active,
            'reservation_history': history,
            'customer_credits': customer_credits,
            'toconline_enabled': settings.TOCONLINE_ENABLED,
        },
    )


@login_required
@require_POST
def retry_pre_reservation_payment(request, public_id):
    pre_reservation = get_object_or_404(
        PreReservation.objects.select_related('payment'),
        public_id=public_id,
        user=request.user,
    )
    if (
        pre_reservation.status == PreReservation.Status.PENDING_PAYMENT
        and pre_reservation.terms_acceptance_source
        == PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER
        and pre_reservation.animal_id
    ):
        query = urlencode({'retry': pre_reservation.public_id})
        checkout_url = reverse(
            'breeding:pre_reserve_dog',
            args=[pre_reservation.animal_id],
        )
        return redirect(f'{checkout_url}?{query}')
    if pre_reservation.status in RETRYABLE_PRE_RESERVATION_STATUSES:
        if (
            pre_reservation.payment.status != Payment.Status.FAILED
            or pre_reservation.animal is None
        ):
            messages.error(request, _('This payment cannot be retried.'))
            return redirect('reservations:dashboard')
        try:
            ensure_dog_is_available(pre_reservation.animal)
        except ReservationUnavailable as exc:
            messages.error(request, str(exc))
            return redirect('reservations:dashboard')
        query = urlencode({'retry': pre_reservation.public_id})
        checkout_url = reverse(
            'breeding:pre_reserve_dog',
            args=[pre_reservation.animal_id],
        )
        return redirect(f'{checkout_url}?{query}')

    if pre_reservation.status != PreReservation.Status.PENDING_PAYMENT:
        messages.error(request, _('This payment cannot be retried.'))
        return redirect('reservations:dashboard')

    success_url, cancel_url = _checkout_urls(request, pre_reservation)
    try:
        checkout_url = initialize_checkout(
            purchase=pre_reservation,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except PaymentError as exc:
        messages.error(request, str(exc))
        return redirect('reservations:dashboard')
    return redirect(checkout_url)


@login_required
def reservation_deposit_checkout(request, public_id):
    reservation = get_object_or_404(
        Reservation.objects.select_related(
            'pre_reservation',
            'pre_reservation__animal',
            'sale_case',
            'sale_case__animal',
            'charge',
            'payment',
            'promotion',
        ).prefetch_related(
            'pre_reservation__animal__files',
            'sale_case__animal__files',
        ),
        Q(sale_case__user=request.user)
        | Q(pre_reservation__user=request.user),
        public_id=public_id,
    )
    terms = ReservationTerms.objects.current()
    if terms is None:
        messages.error(
            request,
            _('Reservation terms are not currently available.'),
        )
        return redirect('reservations:dashboard')
    if reservation.status not in {
        Reservation.Status.OFFERED,
        Reservation.Status.PENDING_PAYMENT,
        Reservation.Status.PAYMENT_FAILED,
    }:
        messages.error(
            request,
            _('This reservation offer can no longer be paid.'),
        )
        return redirect('reservations:dashboard')

    form, promotion_quote, promotion_error, preview_requested = (
        _reservation_checkout_form(
            request=request,
            terms=terms,
            reservation=reservation,
        )
    )
    if _is_checkout_submission(request, preview_requested, form):
        response = _submit_reservation_checkout(
            request=request,
            form=form,
            reservation=reservation,
        )
        if response is not None:
            return response

    return render(
        request,
        'reservations/reservation_checkout.html',
        {
            'reservation': reservation,
            'pre_reservation': reservation.pre_reservation,
            'sale_case': reservation.workflow,
            'terms': terms,
            'form': form,
            'promotion_quote': promotion_quote,
            'promotion_error': promotion_error,
            'checkout_adjustment': reservation.charge.adjustment_amount,
            'checkout_amount': _reservation_preview_amount(
                reservation=reservation,
                promotion_quote=promotion_quote,
            ),
        },
    )


def _reservation_checkout_form(*, request, terms, reservation):
    initial = {'promotion_code': reservation.promotion_code}
    preview_requested = _promotion_preview_requested(request)
    promotion_quote = None
    promotion_error = ''
    if preview_requested:
        initial = _initial_from_post(
            ReservationCheckoutForm,
            request.POST,
            defaults=initial,
            checkbox_fields={'accept_terms'},
        )
        form = ReservationCheckoutForm(terms=terms, initial=initial)
        promotion_quote, promotion_error = _reservation_promotion_preview(
            reservation=reservation,
            code=request.POST.get('promotion_code', ''),
            user=request.user,
        )
    else:
        form = ReservationCheckoutForm(
            request.POST or None,
            terms=terms,
            initial=initial,
        )
        if request.method == 'GET' and reservation.promotion_code:
            promotion_quote, promotion_error = (
                _reservation_promotion_preview(
                    reservation=reservation,
                    code=reservation.promotion_code,
                    user=request.user,
                    use_locked_snapshot=(
                        reservation.status
                        == Reservation.Status.PENDING_PAYMENT
                    ),
                )
            )
    return form, promotion_quote, promotion_error, preview_requested


def _submit_reservation_checkout(*, request, form, reservation):
    try:
        if reservation.status == Reservation.Status.PAYMENT_FAILED:
            prepare_failed_checkout_retry(reservation)
        reservation = start_reservation_payment(
            reservation_id=reservation.pk,
            user=request.user,
            accepted_terms=form.cleaned_data['terms'],
            promotion_code=form.cleaned_data['promotion_code'],
        )
    except (PaymentError, ReservationUnavailable) as exc:
        form.add_error(None, str(exc))
        return None

    if reservation.payment_amount == 0:
        messages.success(request, _('Your reservation is confirmed.'))
        return redirect(
            'reservations:reservation_confirmation',
            public_id=reservation.public_id,
        )
    return _initialize_checkout_redirect(request, reservation)


@login_required
@require_POST
def retry_reservation_payment(request, public_id):
    reservation = get_object_or_404(
        Reservation,
        Q(sale_case__user=request.user)
        | Q(pre_reservation__user=request.user),
        public_id=public_id,
    )
    if reservation.status not in {
        Reservation.Status.OFFERED,
        Reservation.Status.PENDING_PAYMENT,
        Reservation.Status.PAYMENT_FAILED,
    }:
        messages.error(request, _('This payment cannot be retried.'))
        return redirect('reservations:dashboard')
    return redirect(
        'reservations:reservation_checkout',
        public_id=reservation.public_id,
    )


@login_required
@require_GET
def payment_success(request):
    session_id = request.GET.get('session_id', '')
    if not session_id:
        messages.error(request, _('Payment confirmation is missing.'))
        return redirect('reservations:dashboard')
    try:
        purchase = fulfill_checkout_session(session_id)
    except (PaymentError, stripe.StripeError) as exc:
        logger.warning('Payment success verification failed: %s', exc)
        messages.error(
            request,
            _(
                'We could not verify the payment yet. '
                'Please check your dashboard.'
            ),
        )
        return redirect('reservations:dashboard')
    pre_reservation = (
        purchase
        if isinstance(purchase, PreReservation)
        else purchase.pre_reservation
    )
    sale_case = purchase.sale_case
    owner_id = (
        sale_case.user_id
        if sale_case
        else (pre_reservation.user_id if pre_reservation else None)
    )
    if owner_id != request.user.id:
        raise Http404

    sale_document = purchase.charge.erp_documents.filter(
        kind=ERPDocument.Kind.SALE,
    ).first()
    if sale_document and settings.TOCONLINE_ENABLED:
        sale_document = process_erp_document(
            sale_document.pk,
            trigger='success_page',
        )
    return render(
        request,
        'reservations/payment_success.html',
        {
            'purchase': purchase,
            'pre_reservation': pre_reservation,
            'sale_case': sale_case,
            'reservation': (
                purchase if isinstance(purchase, Reservation) else None
            ),
            'document': sale_document,
            'toconline_enabled': settings.TOCONLINE_ENABLED,
        },
    )


@login_required
@require_GET
def pre_reservation_confirmation(request, public_id):
    pre_reservation = get_object_or_404(
        PreReservation.objects.select_related('animal').prefetch_related(
            'animal__files',
        ),
        public_id=public_id,
        user=request.user,
    )
    return render(
        request,
        'reservations/payment_success.html',
        {
            'purchase': pre_reservation,
            'pre_reservation': pre_reservation,
            'sale_case': pre_reservation.sale_case,
            'reservation': None,
            'document': None,
            'toconline_enabled': settings.TOCONLINE_ENABLED,
        },
    )


@login_required
@require_GET
def reservation_confirmation(request, public_id):
    reservation = get_object_or_404(
        Reservation.objects.select_related(
            'pre_reservation',
            'pre_reservation__animal',
            'sale_case',
            'sale_case__animal',
        ).prefetch_related(
            'pre_reservation__animal__files',
            'sale_case__animal__files',
        ),
        Q(sale_case__user=request.user)
        | Q(pre_reservation__user=request.user),
        public_id=public_id,
    )
    return render(
        request,
        'reservations/payment_success.html',
        {
            'purchase': reservation,
            'pre_reservation': reservation.pre_reservation,
            'sale_case': reservation.workflow,
            'reservation': reservation,
            'document': None,
            'toconline_enabled': settings.TOCONLINE_ENABLED,
        },
    )


@login_required
@require_GET
def payment_cancelled(request, public_id):
    pre_reservation = get_object_or_404(
        PreReservation.objects.select_related('payment'),
        public_id=public_id,
        user=request.user,
    )
    return render(
        request,
        'reservations/payment_cancelled.html',
        {
            'purchase': pre_reservation,
            'pre_reservation': pre_reservation,
            'sale_case': pre_reservation.sale_case,
            'reservation': None,
        },
    )


@login_required
@require_GET
def reservation_payment_cancelled(request, public_id):
    reservation = get_object_or_404(
        Reservation.objects.select_related(
            'pre_reservation',
            'sale_case',
            'payment',
        ),
        Q(sale_case__user=request.user)
        | Q(pre_reservation__user=request.user),
        public_id=public_id,
    )
    return render(
        request,
        'reservations/payment_cancelled.html',
        {
            'purchase': reservation,
            'pre_reservation': reservation.pre_reservation,
            'sale_case': reservation.workflow,
            'reservation': reservation,
        },
    )


@login_required
def cancel_pre_reservation(request, public_id):
    pre_reservation = get_object_or_404(
        PreReservation.objects.select_related('payment', 'reservation'),
        public_id=public_id,
        user=request.user,
    )
    if not pre_reservation.can_user_cancel:
        messages.error(
            request,
            _('This pre-reservation can no longer be cancelled.'),
        )
        return redirect('reservations:dashboard')
    if request.method != 'POST':
        return render(
            request,
            'reservations/cancel_confirmation.html',
            {
                'pre_reservation': pre_reservation,
                'reservation': None,
            },
        )
    try:
        cancel_customer_pre_reservation(
            pre_reservation=pre_reservation,
            user=request.user,
        )
    except (ReservationUnavailable, PaymentError, stripe.StripeError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            _(
                'Your pre-reservation was cancelled. No refund is created '
                'automatically.'
            ),
        )
    return redirect('reservations:dashboard')


@login_required
@require_POST
def retry_pdf(request, document_id):
    document = get_object_or_404(
        ERPDocument,
        Q(charge__sale_case__user=request.user)
        | Q(payment__pre_reservation__user=request.user)
        | Q(
            payment__animal_reservation__pre_reservation__user=request.user,
        ),
        pk=document_id,
        status=ERPDocument.Status.INTEGRATED,
    )
    document = ensure_erp_pdf_and_email(
        document.pk,
        triggered_by=request.user,
    )
    if document.pdf_status == ERPDocument.PDFStatus.AVAILABLE:
        messages.success(request, _('Your fiscal document is ready.'))
    else:
        messages.error(
            request,
            _(
                'The fiscal document is temporarily unavailable. '
                'Please try again.'
            ),
        )
    return redirect('reservations:dashboard')


@login_required
@require_GET
def download_document(request, document_id):
    documents = ERPDocument.objects.select_related(
        'charge__sale_case',
        'payment__pre_reservation',
        'payment__animal_reservation__pre_reservation',
    )
    if not request.user.has_perm('reservations.view_erpdocument'):
        documents = documents.filter(
            Q(charge__sale_case__user=request.user)
            | Q(payment__pre_reservation__user=request.user)
            | Q(
                payment__animal_reservation__pre_reservation__user=(
                    request.user
                ),
            )
        )
    document = get_object_or_404(
        documents,
        pk=document_id,
        pdf_status=ERPDocument.PDFStatus.AVAILABLE,
        pdf_data__isnull=False,
    )
    response = HttpResponse(
        bytes(document.pdf_data),
        content_type='application/pdf',
    )
    response['Content-Disposition'] = content_disposition_header(
        True,
        document.pdf_filename,
    )
    patch_cache_control(response, private=True, no_store=True)
    return response


@csrf_exempt
@require_POST
def stripe_webhook(request):
    try:
        event = construct_webhook_event(
            request.body,
            request.headers.get('Stripe-Signature', ''),
        )
        process_stripe_webhook(event)
    except (ValueError, PaymentError, stripe.SignatureVerificationError) as exc:
        logger.warning('Rejected Stripe webhook: %s', exc)
        return HttpResponse(status=400)
    except Exception:
        logger.exception('Stripe webhook processing failed')
        return HttpResponse(status=500)
    return HttpResponse(status=200)


def _checkout_initial(user, *, retry_source=None):
    if retry_source is not None:
        return {
            'full_name': retry_source.customer_name,
            'email': retry_source.customer_email,
            'phone': retry_source.customer_phone,
            'tax_number': retry_source.customer_tax_number,
            'billing_address': retry_source.billing_address,
            'billing_postcode': retry_source.billing_postcode,
            'billing_city': retry_source.billing_city,
            'billing_country': retry_source.billing_country,
            'promotion_code': retry_source.promotion_code,
        }

    profile = getattr(user, 'profile', None)
    address = None
    if profile:
        address = (
            profile.addresses.filter(kind='billing', is_default=True).first()
            or profile.addresses.filter(kind='billing').first()
        )
    return {
        'full_name': user.get_full_name() or user.username,
        'email': user.email,
        'phone': getattr(profile, 'phone', ''),
        'tax_number': getattr(profile, 'fiscal_number', '') or '',
        'billing_address': (
            ' '.join(
                part
                for part in (
                    getattr(address, 'street_line1', ''),
                    getattr(address, 'street_line2', ''),
                )
                if part
            )
            if address
            else ''
        ),
        'billing_postcode': getattr(address, 'postal_code', ''),
        'billing_city': getattr(address, 'city', ''),
        'billing_country': str(getattr(address, 'country', '') or 'PT'),
    }


def _initial_from_post(
    form_class,
    post_data,
    *,
    defaults,
    checkbox_fields,
):
    initial = dict(defaults)
    for field_name in form_class.base_fields:
        if field_name in checkbox_fields:
            initial[field_name] = field_name in post_data
        elif field_name in post_data:
            initial[field_name] = post_data.get(field_name)
    return initial


def _promotion_preview(
    *,
    code,
    target,
    user,
    subtotal,
    purchase_stage,
    purchase=None,
):
    if not Promotion.normalize_code(code):
        return None, _('Enter a promotion code.')
    try:
        quote = quote_promotion(
            code=code,
            target=target,
            user=user,
            fee=subtotal,
            purchase_stage=purchase_stage,
            purchase=purchase,
        )
    except PromotionUnavailable as exc:
        return None, str(exc)
    return quote, ''


def _reservation_promotion_preview(
    *,
    reservation,
    code,
    user,
    use_locked_snapshot=False,
):
    normalized_code = Promotion.normalize_code(code)
    try:
        payment = reservation.payment
    except Payment.DoesNotExist:
        payment = None
    price_is_locked = payment and payment.status in {
        Payment.Status.INITIALIZING,
        Payment.Status.PENDING,
    }
    if price_is_locked and normalized_code != reservation.promotion_code:
        return None, _(
            'The amount is locked for the current payment attempt. '
            'Continue with the existing promotion.'
        )
    if (
        (price_is_locked or use_locked_snapshot)
        and reservation.promotion_code
        and normalized_code == reservation.promotion_code
    ):
        return PromotionQuote(
            reservation.promotion,
            reservation.amount_before_discount,
            reservation.discount_amount,
        ), ''
    return _promotion_preview(
        code=normalized_code,
        target=reservation.animal,
        user=user,
        subtotal=reservation.amount_before_discount,
        purchase_stage=Promotion.PurchaseStage.RESERVATION,
        purchase=reservation,
    )


def _checkout_urls(request, purchase):
    success_base = request.build_absolute_uri(
        reverse('reservations:payment_success'),
    )
    success_url = f'{success_base}?session_id={{CHECKOUT_SESSION_ID}}'
    if isinstance(purchase, PreReservation):
        cancel_name = 'reservations:payment_cancelled'
    else:
        cancel_name = 'reservations:reservation_payment_cancelled'
    cancel_url = request.build_absolute_uri(
        reverse(
            cancel_name,
            kwargs={'public_id': purchase.public_id},
        )
    )
    return success_url, cancel_url


def _get_retry_source(*, request, target_id: int):
    retry_public_id = request.GET.get('retry')
    if not retry_public_id:
        return None
    try:
        retry_public_id = UUID(retry_public_id)
    except (TypeError, ValueError):
        return None
    return (
        PreReservation.objects.filter(
            public_id=retry_public_id,
            user=request.user,
            target_type=PreReservation.TargetType.DOG,
            animal_id=target_id,
        )
        .filter(
            Q(
                status__in=RETRYABLE_PRE_RESERVATION_STATUSES,
                payment__status=Payment.Status.FAILED,
            )
            | Q(
                status=PreReservation.Status.PENDING_PAYMENT,
                terms_acceptance_source=(
                    PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER
                ),
                payment__status=Payment.Status.INITIALIZING,
                payment__stripe_checkout_session_id__isnull=True,
                sale_case__origin__in=(
                    AnimalSaleCase.Origin.ADMIN,
                    AnimalSaleCase.Origin.TRANSFER,
                ),
            )
        )
        .select_related('payment', 'sale_case')
        .first()
    )


def _attach_dashboard_workflow(sale_case):
    if sale_case.animal_id:
        sale_case.animal.has_completed_sale = (
            sale_case.animal_has_completed_sale
        )
    try:
        pre_reservation = sale_case.pre_reservation
    except PreReservation.DoesNotExist:
        pre_reservation = None
    try:
        reservation = sale_case.reservation
    except Reservation.DoesNotExist:
        reservation = None
    try:
        sale = sale_case.sale
    except AnimalSale.DoesNotExist:
        sale = None
    sale_case.pre_reservation_stage = pre_reservation
    sale_case.reservation_stage = reservation
    sale_case.final_sale_stage = sale
    sale_case.has_completed_sale = bool(
        sale is not None and sale.voided_at is None
    )
    charges = {charge.stage: charge for charge in sale_case.charges.all()}
    sale_case.pre_reservation_charge = charges.get(
        Charge.Stage.PRE_RESERVATION,
    )
    sale_case.reservation_charge = charges.get(Charge.Stage.RESERVATION)
    sale_case.final_sale_charge = charges.get(Charge.Stage.SALE)
    sale_case.has_customer_fiscal_documents = any(
        (
            document.status == ERPDocument.Status.INTEGRATED
            or document.pdf_status == ERPDocument.PDFStatus.AVAILABLE
        )
        for charge in sale_case.charges.all()
        for document in charge.erp_documents.all()
    )


def _pre_reservation_checkout_subtotal(*, dog, retry_source):
    if (
        retry_source
        and retry_source.sale_case.origin
        in {
            AnimalSaleCase.Origin.ADMIN,
            AnimalSaleCase.Origin.TRANSFER,
        }
    ):
        return retry_source.charge.subtotal_amount
    return dog.pre_reservation_fee


def _pre_reservation_preview_amount(
    *,
    retry_source,
    promotion_quote,
    subtotal,
):
    if retry_source is None:
        return (
            promotion_quote.total_amount
            if promotion_quote
            else subtotal
        )
    if promotion_quote:
        return max(
            promotion_quote.total_amount
            + retry_source.charge.adjustment_amount
            - retry_source.charge.credit_amount,
            0,
        )
    return retry_source.charge.amount_due


def _reservation_preview_amount(*, reservation, promotion_quote):
    if promotion_quote:
        return max(
            promotion_quote.total_amount
            + reservation.charge.adjustment_amount,
            0,
        )
    return reservation.charge.amount_due
