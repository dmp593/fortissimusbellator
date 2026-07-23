import hashlib
import logging
from collections.abc import Mapping
from datetime import timedelta
from urllib.parse import urlunsplit

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from reservations.exceptions import ERPIntegrationError
from reservations.models import ERPDocument, ERPIntegrationAttempt
from reservations.services.notifications import (
    notify_erp_needs_attention,
    send_document_email,
)


PROCESSING_LEASE = timedelta(minutes=10)
logger = logging.getLogger(__name__)


class ERPDocumentCreationUncertainError(ERPIntegrationError):
    """The financial create request may have succeeded without returning an ID."""


def process_erp_document(
    document_id: int,
    *,
    trigger=ERPIntegrationAttempt.Trigger.AUTOMATIC,
    triggered_by=None,
):
    document = _claim_document(document_id, trigger=trigger)
    if document is None:
        return ERPDocument.objects.get(pk=document_id)
    document = (
        ERPDocument.objects.select_related(
            'reservation',
            'reservation__payment',
        ).get(pk=document_id)
    )
    if document.status == ERPDocument.Status.INTEGRATED:
        ensure_erp_pdf_and_email(document.pk, triggered_by=triggered_by)
        return ERPDocument.objects.get(pk=document_id)

    started_at = timezone.now()
    result = ERPIntegrationAttempt.Result.FAILED
    error = None
    try:
        erp_result, reconciled = _reconcile_or_create(document)
        erp_document_id = _extract_document_id(erp_result)
        if not erp_document_id:
            raise ERPIntegrationError(
                'TOConline did not return or expose the created document ID.'
            )
        result = (
            ERPIntegrationAttempt.Result.RECONCILED
            if reconciled
            else ERPIntegrationAttempt.Result.SUCCESS
        )
    except Exception as exc:
        error = exc
        _record_integration_failure(document_id, exc, trigger=trigger)
    else:
        _record_integration_success(document_id, erp_result)
    finally:
        ERPIntegrationAttempt.objects.create(
            document_id=document_id,
            trigger=trigger,
            triggered_by=triggered_by,
            result=result,
            error_type=error.__class__.__name__ if error else '',
            error_message=_safe_error(error) if error else '',
            started_at=started_at,
            completed_at=timezone.now(),
        )

    document = ERPDocument.objects.get(pk=document_id)
    if document.status == ERPDocument.Status.INTEGRATED:
        ensure_erp_pdf_and_email(document_id, triggered_by=triggered_by)
    elif document.status == ERPDocument.Status.NEEDS_ATTENTION:
        notify_erp_needs_attention(document)
    return ERPDocument.objects.get(pk=document_id)


@transaction.atomic
def _claim_document(document_id: int, *, trigger):
    document = ERPDocument.objects.select_for_update().get(pk=document_id)
    if document.status == ERPDocument.Status.INTEGRATED:
        return document
    if (
        document.status == ERPDocument.Status.PROCESSING
        and document.processing_started_at
        and document.processing_started_at > timezone.now() - PROCESSING_LEASE
    ):
        return None
    if (
        trigger == ERPIntegrationAttempt.Trigger.AUTOMATIC
        and document.status == ERPDocument.Status.NEEDS_ATTENTION
    ):
        return None

    document.status = ERPDocument.Status.PROCESSING
    document.processing_started_at = timezone.now()
    document.last_attempt_at = timezone.now()
    document.attempt_count += 1
    document.next_retry_at = None
    document.save(
        update_fields=[
            'status',
            'processing_started_at',
            'last_attempt_at',
            'attempt_count',
            'next_retry_at',
            'updated_at',
        ]
    )
    return document


def _reconcile_or_create(document: ERPDocument):
    if not settings.TOCONLINE_ENABLED:
        raise ValueError('TOConline integration is disabled.')

    from toconline.api import models
    from toconline.services import toconline

    if document.erp_document_id:
        return {'id': document.erp_document_id}, True

    if document.creation_uncertain:
        existing = _find_document_by_external_reference(document)
        if existing:
            return existing, True
        raise ERPDocumentCreationUncertainError(
            'TOConline creation may already have succeeded. Verify the '
            'external reference before creating another document.'
        )

    body = models.ApiV1CommercialSalesDocumentsPostRequest(
        **_build_document_payload(document)
    )
    _mark_creation_started(document.pk)
    try:
        result = toconline.api.sales.create_sales_document(body=body)
    except Exception as exc:
        return _reconcile_after_uncertain_creation(document, exc)

    if _extract_document_id(result):
        return result, False

    return _reconcile_after_uncertain_creation(
        document,
        ERPDocumentCreationUncertainError(
            'TOConline did not return the created document ID.'
        ),
    )


def _reconcile_after_uncertain_creation(document: ERPDocument, error: Exception):
    try:
        existing = _find_document_by_external_reference(document)
    except Exception as reconciliation_error:
        raise ERPDocumentCreationUncertainError(
            'TOConline creation could not be reconciled after an uncertain '
            'response. Verify the external reference before retrying.'
        ) from reconciliation_error
    if existing:
        return existing, True
    raise ERPDocumentCreationUncertainError(
        'TOConline creation could not be confirmed. Verify the external '
        'reference before retrying.'
    ) from error


def _find_document_by_external_reference(document: ERPDocument):
    from toconline.services import toconline

    result = toconline.api.sales.list_sales_documents(
        params={'filter[external_reference]': document.external_reference},
    )
    for candidate in _document_records(result):
        if (
            _extract_document_id(candidate)
            and _extract_external_reference(candidate) == document.external_reference
        ):
            return candidate
    return None


def _document_records(result):
    data = _value(result, 'data', result)
    if isinstance(data, Mapping):
        return (data,)
    if isinstance(data, (list, tuple)):
        return data
    return ()


def _mark_creation_started(document_id: int):
    now = timezone.now()
    ERPDocument.objects.filter(pk=document_id).update(
        creation_uncertain=True,
        creation_started_at=now,
        updated_at=now,
    )


def _build_document_payload(document: ERPDocument):
    reservation = document.reservation
    payment = reservation.payment
    accounting_timestamp = (
        payment.refunded_at
        if document.kind == ERPDocument.Kind.CREDIT_NOTE
        else payment.paid_at
    ) or reservation.confirmed_at or reservation.created_at
    line = {
        'item_type': 'Service',
        'description': (
            f'Non-refundable pre-reservation fee - {reservation.target_name}'
        ),
        'quantity': 1,
        'unit_price': float(reservation.total_amount),
    }
    if settings.TOCONLINE_TAX_CODE:
        line['tax_code'] = settings.TOCONLINE_TAX_CODE
    if settings.TOCONLINE_TAX_PERCENTAGE:
        line['tax_percentage'] = float(settings.TOCONLINE_TAX_PERCENTAGE)

    payload = {
        'document_type': 'FR',
        'date': timezone.localdate(accounting_timestamp).isoformat(),
        'customer_business_name': reservation.customer_name,
        'customer_address_detail': reservation.billing_address,
        'customer_postcode': reservation.billing_postcode,
        'customer_city': reservation.billing_city,
        'customer_country': reservation.billing_country,
        'customer_tax_registration_number': reservation.customer_tax_number,
        'currency_iso_code': reservation.currency,
        'external_reference': document.external_reference,
        'notes': (
            f'Pre-reservation {reservation.public_id}. '
            'Non-refundable when cancelled by the customer.'
        ),
        'vat_included_prices': True,
        'finalize': 1,
        'lines': [line],
    }
    if settings.TOCONLINE_PAYMENT_MECHANISM:
        payload['payment_mechanism'] = settings.TOCONLINE_PAYMENT_MECHANISM

    if document.kind == ERPDocument.Kind.CREDIT_NOTE:
        sale = reservation.erp_documents.get(kind=ERPDocument.Kind.SALE)
        if not sale.erp_document_id:
            raise ERPIntegrationError(
                'The original ERP sale must be integrated before its credit note.'
            )
        payload['document_type'] = 'NC'
        payload['parent_documents_ids'] = [sale.erp_document_id]
    return {key: value for key, value in payload.items() if value not in ('', None)}


@transaction.atomic
def _record_integration_success(document_id: int, result):
    document = ERPDocument.objects.select_for_update().get(pk=document_id)
    document.status = ERPDocument.Status.INTEGRATED
    document.erp_document_id = _extract_document_id(result)
    document.erp_document_number = _extract_document_number(result)
    document.integrated_at = timezone.now()
    document.processing_started_at = None
    document.next_retry_at = None
    document.last_error = ''
    document.creation_uncertain = False
    document.pdf_status = ERPDocument.PDFStatus.PENDING
    document.save(
        update_fields=[
            'status',
            'erp_document_id',
            'erp_document_number',
            'integrated_at',
            'processing_started_at',
            'next_retry_at',
            'last_error',
            'creation_uncertain',
            'pdf_status',
            'updated_at',
        ]
    )


@transaction.atomic
def _record_integration_failure(document_id: int, exc: Exception, *, trigger):
    document = ERPDocument.objects.select_for_update().get(pk=document_id)
    max_attempts = settings.RESERVATION_ERP_MAX_AUTOMATIC_ATTEMPTS
    creation_uncertain = _is_creation_uncertain(exc)
    retryable = _is_retryable(exc)
    exhausted = document.attempt_count >= max_attempts
    needs_attention = creation_uncertain or not retryable or (
        exhausted and trigger == ERPIntegrationAttempt.Trigger.AUTOMATIC
    )
    document.status = (
        ERPDocument.Status.NEEDS_ATTENTION
        if needs_attention
        else ERPDocument.Status.RETRYABLE_FAILURE
    )
    document.processing_started_at = None
    document.last_error = _safe_error(exc)
    document.creation_uncertain = creation_uncertain
    document.next_retry_at = (
        None
        if needs_attention
        else timezone.now()
        + timedelta(minutes=min(2 ** document.attempt_count, 60))
    )
    document.save(
        update_fields=[
            'status',
            'processing_started_at',
            'last_error',
            'creation_uncertain',
            'next_retry_at',
            'updated_at',
        ]
    )


def download_erp_pdf(document_id: int):
    document = ERPDocument.objects.select_related('reservation').get(pk=document_id)
    if document.status != ERPDocument.Status.INTEGRATED:
        raise ERPIntegrationError('The ERP sale is not integrated yet.')

    ERPDocument.objects.filter(pk=document_id).update(
        pdf_status=ERPDocument.PDFStatus.PENDING,
        pdf_attempt_count=document.pdf_attempt_count + 1,
        pdf_last_error='',
    )
    try:
        from toconline.services import toconline

        print_url = toconline.api.documents.get_sales_document_print_url(
            document.erp_document_id,
        )
        pdf_data = _download_sales_document_pdf(print_url)
        if not pdf_data or not pdf_data.startswith(b'%PDF'):
            raise ValueError('TOConline returned an invalid PDF document.')
        if len(pdf_data) > settings.RESERVATION_PDF_MAX_BYTES:
            raise ValueError('TOConline PDF exceeds the configured size limit.')
    except Exception as exc:
        ERPDocument.objects.filter(pk=document_id).update(
            pdf_status=ERPDocument.PDFStatus.FAILED,
            pdf_last_error=_safe_error(exc),
        )
        return ERPDocument.objects.get(pk=document_id)

    filename = f'{document.external_reference}.pdf'
    ERPDocument.objects.filter(pk=document_id).update(
        pdf_status=ERPDocument.PDFStatus.AVAILABLE,
        pdf_data=pdf_data,
        pdf_filename=filename,
        pdf_sha256=hashlib.sha256(pdf_data).hexdigest(),
        pdf_downloaded_at=timezone.now(),
        pdf_last_error='',
    )
    return ERPDocument.objects.get(pk=document_id)


def _download_sales_document_pdf(print_url):
    url = _build_trusted_print_url(print_url)
    response = requests.get(
        url,
        stream=True,
        allow_redirects=False,
        timeout=settings.TOCONLINE_TIMEOUT,
    )
    try:
        response.raise_for_status()
        content_length = response.headers.get('Content-Length')
        if content_length and int(content_length) > settings.RESERVATION_PDF_MAX_BYTES:
            raise ValueError('TOConline PDF exceeds the configured size limit.')

        pdf_data = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            pdf_data.extend(chunk)
            if len(pdf_data) > settings.RESERVATION_PDF_MAX_BYTES:
                raise ValueError('TOConline PDF exceeds the configured size limit.')
        return bytes(pdf_data)
    finally:
        response.close()


def _build_trusted_print_url(print_url):
    url = _value(_value(_value(print_url, 'data'), 'attributes'), 'url')
    scheme = str(_value(url, 'scheme', '')).lower()
    host = str(_value(url, 'host', '')).lower().rstrip('.')
    port = _value(url, 'port')
    path = str(_value(url, 'path', ''))
    if (
        scheme != 'https'
        or not host
        or port not in (None, 443, 443.0)
        or not path.startswith('/')
        or not _is_trusted_download_host(host)
    ):
        raise ERPIntegrationError('TOConline returned an untrusted PDF URL.')
    return urlunsplit((scheme, host, path, '', ''))


def _is_trusted_download_host(host: str) -> bool:
    allowed_hosts = {
        configured_host.lower().lstrip('.').rstrip('.')
        for configured_host in settings.TOCONLINE_ALLOWED_DOWNLOAD_HOSTS
        if configured_host.strip()
    }
    return any(
        host == allowed_host or host.endswith(f'.{allowed_host}')
        for allowed_host in allowed_hosts
    )


def ensure_erp_pdf_and_email(document_id: int, *, triggered_by=None):
    document = ERPDocument.objects.select_related('reservation').get(pk=document_id)
    if document.pdf_status != ERPDocument.PDFStatus.AVAILABLE:
        document = download_erp_pdf(document_id)
    if (
        document.pdf_status == ERPDocument.PDFStatus.AVAILABLE
        and not document.email_attempts.filter(
            status='sent',
            recipient=document.reservation.customer_email,
        ).exists()
    ):
        try:
            send_document_email(
                document=document,
                recipient=document.reservation.customer_email,
                triggered_by=triggered_by,
            )
        except Exception:
            logger.exception(
                'Unable to email ERP document PDF',
                extra={'erp_document_id': document_id},
            )
    return ERPDocument.objects.get(pk=document_id)


def _extract_document_id(result):
    if not result:
        return None
    result = _first_document_result(result)
    return _value(result, 'id')


def _extract_document_number(result):
    if not result:
        return ''
    result = _first_document_result(result)
    attributes = _value(result, 'attributes', {}) or {}
    document_number = (
        _value(result, 'number')
        or _value(result, 'document_number')
        or _value(attributes, 'number')
        or _value(attributes, 'document_number')
    )
    if document_number:
        return str(document_number)

    document_number = _value(attributes, 'document_no')
    document_series_prefix = _value(attributes, 'document_series_prefix')
    if document_number and document_series_prefix:
        return f'{document_series_prefix}/{document_number}'
    return str(document_number or '')


def _extract_external_reference(result):
    result = _first_document_result(result)
    attributes = _value(result, 'attributes', {}) or {}
    return _value(result, 'external_reference') or _value(
        attributes,
        'external_reference',
    )


def _first_document_result(result):
    if isinstance(result, (list, tuple)):
        result = result[0] if result else None
    return _value(result, 'data', result)


def _value(value, key, default=None):
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, ERPDocumentCreationUncertainError):
        return False
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    if isinstance(exc, ERPIntegrationError):
        return True
    return False


def _is_creation_uncertain(exc: Exception) -> bool:
    if isinstance(exc, ERPDocumentCreationUncertainError):
        return True
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else None
        return status_code is None or status_code in {408, 429} or status_code >= 500
    return False


def _safe_error(exc: Exception | None) -> str:
    if exc is None:
        return ''
    return f'{exc.__class__.__name__}: {str(exc)}'[:2000]
