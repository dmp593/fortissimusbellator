import logging
from urllib.parse import urlencode
from uuid import UUID

import stripe
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.http import content_disposition_header
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from breeding.models import Animal, Litter

from .availability import (
    ensure_dog_is_available,
    ensure_litter_has_capacity,
    litter_reserved_count,
)
from .exceptions import PaymentError, ReservationUnavailable
from .forms import PreReservationCheckoutForm
from .models import ERPDocument, Payment, PreReservation, PreReservationTerms
from .services.erp import ensure_erp_pdf_and_email, process_erp_document
from .services.payment import (
    cancel_customer_reservation,
    fulfill_checkout_session,
    initialize_checkout,
    process_stripe_webhook,
)
from .services.reservation import create_pending_reservation
from .stripe_gateway import construct_webhook_event


logger = logging.getLogger(__name__)

RETRYABLE_RESERVATION_STATUSES = (
    PreReservation.Status.PAYMENT_FAILED,
    PreReservation.Status.EXPIRED,
)


def reservation_checkout(request, *, target_type: str, target_id: int):
    terms = PreReservationTerms.objects.current()
    if terms is None:
        messages.error(
            request,
            _('Pre-reservation terms are not currently available.'),
        )
        return redirect(_target_detail_url(target_type, target_id))

    target = _get_target(target_type, target_id)
    try:
        if target_type == PreReservation.TargetType.DOG:
            ensure_dog_is_available(target)
        else:
            ensure_litter_has_capacity(target, user=request.user)
    except ReservationUnavailable as exc:
        messages.error(request, str(exc))
        return redirect(_target_detail_url(target_type, target_id))

    retry_source = _get_retry_source(
        request=request,
        target_type=target_type,
        target_id=target_id,
    )
    form = PreReservationCheckoutForm(
        request.POST or None,
        terms=terms,
        initial=_checkout_initial(request.user, retry_source=retry_source),
    )
    if request.method == 'POST' and form.is_valid():
        try:
            reservation = create_pending_reservation(
                user=request.user,
                target_type=target_type,
                target_id=target_id,
                checkout_data=form.cleaned_data,
                language_code=request.LANGUAGE_CODE,
            )
        except ReservationUnavailable as exc:
            form.add_error(None, str(exc))
        else:
            if reservation.total_amount == 0:
                messages.success(request, _('Your pre-reservation is confirmed.'))
                return redirect(
                    'reservations:reservation_confirmation',
                    public_id=reservation.public_id,
                )

            success_url, cancel_url = _checkout_urls(request, reservation)
            try:
                checkout_url = initialize_checkout(
                    reservation=reservation,
                    success_url=success_url,
                    cancel_url=cancel_url,
                )
            except PaymentError as exc:
                messages.error(request, str(exc))
                return redirect('reservations:dashboard')
            return redirect(checkout_url)

    context = {
        'item': target,
        'item_type': target_type,
        'form': form,
        'terms': terms,
        'reserved_count': (
            litter_reserved_count(target)
            if target_type == PreReservation.TargetType.LITTER
            else None
        ),
    }
    template = (
        'buy_a_dog/pre_reserve.html'
        if target_type == PreReservation.TargetType.DOG
        else 'upcoming_litters/pre_reserve.html'
    )
    return render(request, template, context)


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


@login_required
def dashboard(request):
    document_queryset = ERPDocument.objects.defer('pdf_data').order_by(
        '-created_at'
    )
    reservations = list(
        PreReservation.objects.filter(user=request.user)
        .select_related('animal', 'litter', 'payment')
        .prefetch_related(
            Prefetch('erp_documents', queryset=document_queryset)
        )
    )
    for reservation in reservations:
        reservation.sale_erp_document = next(
            (
                document
                for document in reservation.erp_documents.all()
                if document.kind == ERPDocument.Kind.SALE
            ),
            None,
        )

    active_statuses = {
        PreReservation.Status.PENDING_PAYMENT,
        PreReservation.Status.CONFIRMED,
    }
    context = {
        'active_reservations': [
            reservation
            for reservation in reservations
            if reservation.status in active_statuses
        ],
        'reservation_history': [
            reservation
            for reservation in reservations
            if reservation.status not in active_statuses
        ],
    }
    return render(request, 'reservations/dashboard.html', context)


@login_required
@require_POST
def retry_payment(request, public_id):
    reservation = get_object_or_404(
        PreReservation.objects.select_related('payment'),
        public_id=public_id,
        user=request.user,
    )

    if reservation.status in RETRYABLE_RESERVATION_STATUSES:
        if reservation.payment.status != Payment.Status.FAILED:
            messages.error(request, _('This payment cannot be retried.'))
            return redirect('reservations:dashboard')
        if reservation.target is None:
            messages.error(
                request,
                _(
                    'This pre-reservation cannot be retried because the '
                    'listing no longer exists.'
                ),
            )
            return redirect('reservations:dashboard')

        try:
            if reservation.target_type == PreReservation.TargetType.DOG:
                ensure_dog_is_available(reservation.animal)
            else:
                ensure_litter_has_capacity(
                    reservation.litter,
                    user=request.user,
                )
        except ReservationUnavailable as exc:
            messages.error(request, str(exc))
            return redirect('reservations:dashboard')

        checkout_url = _target_checkout_url(reservation)
        query = urlencode({'retry': reservation.public_id})
        return redirect(f'{checkout_url}?{query}')

    if reservation.status != PreReservation.Status.PENDING_PAYMENT:
        messages.error(request, _('This payment cannot be retried.'))
        return redirect('reservations:dashboard')

    success_url, cancel_url = _checkout_urls(request, reservation)
    try:
        checkout_url = initialize_checkout(
            reservation=reservation,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except PaymentError as exc:
        messages.error(request, str(exc))
        return redirect('reservations:dashboard')
    return redirect(checkout_url)


@login_required
@require_GET
def payment_success(request):
    session_id = request.GET.get('session_id', '')
    if not session_id:
        messages.error(request, _('Payment confirmation is missing.'))
        return redirect('reservations:dashboard')
    try:
        reservation = fulfill_checkout_session(session_id)
    except (PaymentError, stripe.StripeError) as exc:
        logger.warning('Payment success verification failed: %s', exc)
        messages.error(
            request,
            _('We could not verify the payment yet. Please check your dashboard.'),
        )
        return redirect('reservations:dashboard')
    if reservation.user_id != request.user.id:
        raise Http404

    sale_document = reservation.erp_documents.filter(
        kind=ERPDocument.Kind.SALE
    ).first()
    if sale_document:
        sale_document = process_erp_document(
            sale_document.pk,
            trigger='success_page',
        )
    return render(
        request,
        'reservations/payment_success.html',
        {'reservation': reservation, 'document': sale_document},
    )


@login_required
@require_GET
def reservation_confirmation(request, public_id):
    reservation = get_object_or_404(
        PreReservation,
        public_id=public_id,
        user=request.user,
    )
    return render(
        request,
        'reservations/payment_success.html',
        {'reservation': reservation, 'document': None},
    )


@login_required
def payment_cancelled(request, public_id):
    reservation = get_object_or_404(
        PreReservation.objects.select_related('payment'),
        public_id=public_id,
        user=request.user,
    )
    return render(
        request,
        'reservations/payment_cancelled.html',
        {'reservation': reservation},
    )


@login_required
def cancel_reservation(request, public_id):
    reservation = get_object_or_404(
        PreReservation.objects.select_related('payment'),
        public_id=public_id,
        user=request.user,
    )
    if not reservation.can_user_cancel:
        messages.error(
            request,
            _('This pre-reservation can no longer be cancelled.'),
        )
        return redirect('reservations:dashboard')
    if request.method != 'POST':
        return render(
            request,
            'reservations/cancel_confirmation.html',
            {'reservation': reservation},
        )
    try:
        cancel_customer_reservation(reservation=reservation, user=request.user)
    except (ReservationUnavailable, PaymentError, stripe.StripeError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            _(
                'Your pre-reservation was cancelled. The pre-reservation fee '
                'is non-refundable.'
            ),
        )
    return redirect('reservations:dashboard')


@login_required
@require_POST
def retry_pdf(request, document_id):
    document = get_object_or_404(
        ERPDocument,
        pk=document_id,
        reservation__user=request.user,
        status=ERPDocument.Status.INTEGRATED,
    )
    document = ensure_erp_pdf_and_email(document.pk, triggered_by=request.user)
    if document.pdf_status == ERPDocument.PDFStatus.AVAILABLE:
        messages.success(request, _('Your fiscal document is ready.'))
    else:
        messages.error(
            request,
            _('The fiscal document is temporarily unavailable. Please try again.'),
        )
    return redirect('reservations:dashboard')


@login_required
@require_GET
def download_document(request, document_id):
    documents = ERPDocument.objects.select_related('reservation')
    if not request.user.has_perm('reservations.view_erpdocument'):
        documents = documents.filter(reservation__user=request.user)
    document = get_object_or_404(
        documents,
        pk=document_id,
        pdf_status=ERPDocument.PDFStatus.AVAILABLE,
        pdf_data__isnull=False,
    )
    response = HttpResponse(bytes(document.pdf_data), content_type='application/pdf')
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


def _get_target(target_type: str, target_id: int):
    if target_type == PreReservation.TargetType.DOG:
        return get_object_or_404(
            Animal.objects.select_related('breed'),
            pk=target_id,
        )
    return get_object_or_404(
        Litter.objects.select_related('breed'),
        pk=target_id,
    )


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


def _checkout_urls(request, reservation):
    success_base = request.build_absolute_uri(
        reverse('reservations:payment_success')
    )
    success_url = f'{success_base}?session_id={{CHECKOUT_SESSION_ID}}'
    cancel_url = request.build_absolute_uri(
        reverse(
            'reservations:payment_cancelled',
            kwargs={'public_id': reservation.public_id},
        )
    )
    return success_url, cancel_url


def _target_detail_url(target_type: str, target_id: int):
    if target_type == PreReservation.TargetType.DOG:
        return reverse('breeding:dog_detail', args=[target_id])
    return reverse('breeding:litter_detail', args=[target_id])


def _target_checkout_url(reservation):
    if reservation.target_type == PreReservation.TargetType.DOG:
        return reverse('breeding:pre_reserve_dog', args=[reservation.animal_id])
    return reverse('breeding:pre_reserve_litter', args=[reservation.litter_id])


def _get_retry_source(*, request, target_type: str, target_id: int):
    retry_public_id = request.GET.get('retry')
    if not retry_public_id:
        return None
    try:
        retry_public_id = UUID(retry_public_id)
    except (TypeError, ValueError):
        return None

    target_filter = (
        {'animal_id': target_id}
        if target_type == PreReservation.TargetType.DOG
        else {'litter_id': target_id}
    )
    return (
        PreReservation.objects.filter(
            public_id=retry_public_id,
            user=request.user,
            target_type=target_type,
            status__in=RETRYABLE_RESERVATION_STATUSES,
            payment__status=Payment.Status.FAILED,
            **target_filter,
        )
        .select_related('payment')
        .first()
    )
