import stripe
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Exists, OuterRef, Prefetch
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TranslationAdmin

from reservations.exceptions import ERPIntegrationError, PaymentError, ReservationUnavailable
from reservations.forms import AdminCancellationForm, ResendDocumentForm
from reservations.models import (
    DocumentEmailAttempt,
    ERPDocument,
    ERPIntegrationAttempt,
    Payment,
    PreReservation,
    PreReservationTerms,
    ProcessedStripeEvent,
)
from reservations.services.erp import (
    download_erp_pdf,
    ensure_erp_pdf_and_email,
    process_erp_document,
)
from reservations.services.notifications import send_document_email
from reservations.services.payment import (
    cancel_staff_reservation,
    process_refund,
)
from reservations.services.reservation import ensure_sale_erp_document, mark_fulfilled


PAID_PAYMENT_STATUSES = (
    Payment.Status.PAID,
    Payment.Status.REFUND_PENDING,
    Payment.Status.REFUND_FAILED,
    Payment.Status.REFUNDED,
)


@admin.register(PreReservationTerms)
class PreReservationTermsAdmin(TranslationAdmin):
    list_display = ('version', 'published_at', 'reservation_count')
    search_fields = ('version', 'description')
    ordering = ('-published_at', '-pk')

    @admin.display(description=_('reservations'))
    def reservation_count(self, obj):
        return obj.reservations.count()

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.reservations.exists():
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.reservations.exists():
            return False
        return super().has_delete_permission(request, obj)


class ERPAttentionFilter(admin.SimpleListFilter):
    title = _('ERP integration')
    parameter_name = 'erp_health'

    def lookups(self, request, model_admin):
        return (
            ('attention', _('Paid without an integrated sale document')),
            ('integrated', _('Paid and integrated')),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value not in {'attention', 'integrated'}:
            return queryset
        integrated_sale = ERPDocument.objects.filter(
            reservation_id=OuterRef('pk'),
            kind=ERPDocument.Kind.SALE,
            status=ERPDocument.Status.INTEGRATED,
        )
        queryset = queryset.filter(
            payment__status__in=PAID_PAYMENT_STATUSES,
            payment__amount__gt=0,
        ).annotate(has_integrated_sale=Exists(integrated_sale))
        return queryset.filter(has_integrated_sale=value == 'integrated')


class ERPDocumentInline(admin.TabularInline):
    model = ERPDocument
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        'kind',
        'status',
        'erp_document_number',
        'creation_uncertain',
        'creation_started_at',
        'attempt_count',
        'pdf_status',
        'pdf_attempt_count',
        'updated_at',
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).defer('pdf_data')


@admin.register(PreReservation)
class PreReservationAdmin(admin.ModelAdmin):
    change_form_template = 'admin/reservations/prereservation/change_form.html'
    list_display = (
        'short_reference',
        'target_name',
        'customer_email',
        'status',
        'payment_status',
        'sale_erp_status',
        'sale_pdf_status',
        'total_amount',
        'created_at',
    )
    list_filter = (
        'status',
        'target_type',
        'payment__status',
        ERPAttentionFilter,
        'created_at',
    )
    search_fields = (
        'public_id',
        'target_name',
        'customer_name',
        'customer_email',
        'promotion_code',
        'payment__stripe_checkout_session_id',
        'payment__stripe_payment_intent_id',
    )
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    inlines = (ERPDocumentInline,)
    readonly_fields = (
        'public_id',
        'user',
        'target_type',
        'animal',
        'litter',
        'promotion',
        'status',
        'target_name',
        'target_breed',
        'target_birth_date',
        'target_deleted_at',
        'customer_name',
        'customer_email',
        'customer_phone',
        'customer_tax_number',
        'billing_address',
        'billing_postcode',
        'billing_city',
        'billing_country',
        'language_code',
        'fee_amount',
        'discount_amount',
        'total_amount',
        'currency',
        'promotion_code',
        'promotion_discount_type',
        'promotion_value',
        'hold_expires_at',
        'terms',
        'non_refundable_accepted_at',
        'confirmed_at',
        'fulfilled_at',
        'cancelled_at',
        'cancelled_by',
        'cancellation_reason',
        'created_at',
        'updated_at',
        'payment_summary',
        'erp_summary',
    )
    fieldsets = (
        (_('Reservation'), {
            'fields': (
                'public_id', 'status', 'user', 'target_type', 'animal', 'litter',
                'target_name', 'target_breed', 'target_birth_date',
                'target_deleted_at',
            )
        }),
        (_('Customer snapshot'), {
            'fields': (
                'customer_name', 'customer_email', 'customer_phone',
                'customer_tax_number', 'billing_address', 'billing_postcode',
                'billing_city', 'billing_country', 'language_code',
            )
        }),
        (_('Price snapshot'), {
            'fields': (
                'fee_amount', 'discount_amount', 'total_amount', 'currency',
                'promotion', 'promotion_code', 'promotion_discount_type',
                'promotion_value',
            )
        }),
        (_('Operational state'), {'fields': ('payment_summary', 'erp_summary')}),
        (_('Lifecycle'), {
            'fields': (
                'hold_expires_at', 'terms',
                'non_refundable_accepted_at', 'confirmed_at', 'fulfilled_at',
                'cancelled_at', 'cancelled_by', 'cancellation_reason',
                'created_at', 'updated_at',
            )
        }),
    )

    def get_queryset(self, request):
        documents = ERPDocument.objects.defer('pdf_data').order_by('kind')
        return (
            super()
            .get_queryset(request)
            .select_related('user', 'animal', 'litter', 'payment', 'terms')
            .prefetch_related(
                Prefetch(
                    'erp_documents',
                    queryset=documents,
                    to_attr='admin_erp_documents',
                )
            )
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            # Keep deletion unavailable from the changelist and bulk actions.
            return False
        return (
            super().has_delete_permission(request, obj)
            and self._can_discard_unpaid_setup_failure(obj)
        )

    def delete_model(self, request, obj):
        if not self._can_discard_unpaid_setup_failure(obj):
            raise PermissionDenied
        super().delete_model(request, obj)

    def get_deleted_objects(self, objs, request):
        deleted_objects, model_count, perms_needed, protected = super().get_deleted_objects(
            objs,
            request,
        )
        reservations = list(objs)
        if (
            len(reservations) == 1
            and self._can_discard_unpaid_setup_failure(reservations[0])
        ):
            # The related Payment cannot be deleted on its own, but it is safe
            # to remove with a failed local setup that never reached Stripe.
            perms_needed.discard(Payment._meta.verbose_name)
        return deleted_objects, model_count, perms_needed, protected

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    def get_urls(self):
        opts = self.model._meta
        custom_urls = [
            path(
                '<path:object_id>/retry-erp/',
                self.admin_site.admin_view(self.retry_erp_view),
                name=f'{opts.app_label}_{opts.model_name}_retry_erp',
            ),
            path(
                '<path:object_id>/retry-pdf/',
                self.admin_site.admin_view(self.retry_pdf_view),
                name=f'{opts.app_label}_{opts.model_name}_retry_pdf',
            ),
            path(
                '<path:object_id>/resend-pdf/',
                self.admin_site.admin_view(self.resend_pdf_view),
                name=f'{opts.app_label}_{opts.model_name}_resend_pdf',
            ),
            path(
                '<path:object_id>/cancel/',
                self.admin_site.admin_view(self.cancel_view),
                name=f'{opts.app_label}_{opts.model_name}_cancel',
            ),
            path(
                '<path:object_id>/retry-refund/',
                self.admin_site.admin_view(self.retry_refund_view),
                name=f'{opts.app_label}_{opts.model_name}_retry_refund',
            ),
            path(
                '<path:object_id>/fulfill/',
                self.admin_site.admin_view(self.fulfill_view),
                name=f'{opts.app_label}_{opts.model_name}_fulfill',
            ),
        ]
        return custom_urls + super().get_urls()

    @admin.display(description=_('reference'))
    def short_reference(self, obj):
        return str(obj.public_id)[:8]

    @admin.display(description=_('payment'), ordering='payment__status')
    def payment_status(self, obj):
        return obj.payment.get_status_display()

    @admin.display(description=_('ERP sale'))
    def sale_erp_status(self, obj):
        if obj.payment.amount == 0:
            return _('Not required')
        document = self._sale_document(obj)
        return document.get_status_display() if document else _('Missing')

    @admin.display(description=_('PDF'))
    def sale_pdf_status(self, obj):
        document = self._sale_document(obj)
        return document.get_pdf_status_display() if document else '-'

    @admin.display(description=_('payment'))
    def payment_summary(self, obj):
        payment = obj.payment
        return format_html(
            '<strong>{}</strong><br>Provider: {}<br>Amount: {} {}<br>'
            'Checkout: {}<br>PaymentIntent: {}<br>Last error: {}',
            payment.get_status_display(),
            payment.get_provider_display(),
            payment.amount,
            payment.currency,
            payment.stripe_checkout_session_id or '-',
            payment.stripe_payment_intent_id or '-',
            payment.last_error or '-',
        )

    @admin.display(description=_('ERP and PDF'))
    def erp_summary(self, obj):
        if obj.payment.amount == 0:
            return _('No fiscal document is required for a zero-value reservation.')
        document = self._sale_document(obj)
        if not document:
            return _('No sale document task exists.')
        return format_html(
            '<strong>{}</strong><br>Reference: {}<br>ERP ID: {}<br>'
            'ERP number: {}<br>Attempts: {}<br>Last error: {}<br>'
            'PDF: {}<br>PDF error: {}',
            document.get_status_display(),
            document.external_reference,
            document.erp_document_id or '-',
            document.erp_document_number or '-',
            document.attempt_count,
            document.last_error or '-',
            document.get_pdf_status_display(),
            document.pdf_last_error or '-',
        )

    def retry_erp_view(self, request, object_id):
        reservation = self._get_authorized_object(request, object_id)
        document = self._sale_document(reservation)
        if (
            document is None
            and reservation.payment.status in PAID_PAYMENT_STATUSES
            and reservation.payment.amount > 0
        ):
            document = ensure_sale_erp_document(reservation)
        if document is None:
            self.message_user(
                request,
                _('Only paid reservations can be integrated with the ERP.'),
                level=messages.ERROR,
            )
            return redirect(self._change_url(reservation))

        if request.method == 'POST':
            document = process_erp_document(
                document.pk,
                trigger=ERPIntegrationAttempt.Trigger.ADMIN,
                triggered_by=request.user,
            )
            level = (
                messages.SUCCESS
                if document.status == ERPDocument.Status.INTEGRATED
                else messages.ERROR
            )
            self.message_user(
                request,
                _('ERP status: %(status)s')
                % {'status': document.get_status_display()},
                level=level,
            )
            return redirect(self._change_url(reservation))
        return self._render_action(
            request,
            reservation,
            title=_('Retry ERP integration'),
            warning=_(
                'The operation first searches TOConline by the stable external '
                'reference, then creates a document only when none exists.'
            ),
            submit_label=_('Retry integration'),
        )

    def retry_pdf_view(self, request, object_id):
        reservation = self._get_authorized_object(request, object_id)
        document = self._sale_document(reservation)
        if document is None or document.status != ERPDocument.Status.INTEGRATED:
            self.message_user(
                request,
                _('The ERP sale must be integrated before downloading its PDF.'),
                level=messages.ERROR,
            )
            return redirect(self._change_url(reservation))
        if request.method == 'POST':
            try:
                document = download_erp_pdf(document.pk)
                if document.pdf_status == ERPDocument.PDFStatus.AVAILABLE:
                    document = ensure_erp_pdf_and_email(
                        document.pk,
                        triggered_by=request.user,
                    )
            except ERPIntegrationError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                level = (
                    messages.SUCCESS
                    if document.pdf_status == ERPDocument.PDFStatus.AVAILABLE
                    else messages.ERROR
                )
                self.message_user(
                    request,
                    _('PDF status: %(status)s')
                    % {'status': document.get_pdf_status_display()},
                    level=level,
                )
            return redirect(self._change_url(reservation))
        return self._render_action(
            request,
            reservation,
            title=_('Retry fiscal document PDF'),
            warning=_(
                'This downloads the existing fiscal document again. It does not '
                'create another sale.'
            ),
            submit_label=_('Retry PDF download'),
        )

    def resend_pdf_view(self, request, object_id):
        reservation = self._get_authorized_object(request, object_id)
        document = self._sale_document(reservation)
        if document is None or document.pdf_status != ERPDocument.PDFStatus.AVAILABLE:
            self.message_user(
                request,
                _('Download the fiscal document PDF before sending it.'),
                level=messages.ERROR,
            )
            return redirect(self._change_url(reservation))
        form = ResendDocumentForm(
            request.POST or None,
            initial={'recipient': reservation.customer_email},
        )
        if request.method == 'POST' and form.is_valid():
            try:
                send_document_email(
                    document=document,
                    recipient=form.cleaned_data['recipient'],
                    triggered_by=request.user,
                )
            except Exception:
                self.message_user(
                    request,
                    _('The email could not be sent. The failed attempt was recorded.'),
                    level=messages.ERROR,
                )
            else:
                self.message_user(
                    request,
                    _('The fiscal document was sent.'),
                    level=messages.SUCCESS,
                )
            return redirect(self._change_url(reservation))
        return self._render_action(
            request,
            reservation,
            title=_('Resend fiscal document'),
            warning=_('The PDF contains customer and fiscal information.'),
            submit_label=_('Send PDF'),
            form=form,
        )

    def cancel_view(self, request, object_id):
        reservation = self._get_authorized_object(request, object_id)
        form = AdminCancellationForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                reservation, should_refund = cancel_staff_reservation(
                    reservation=reservation,
                    admin_user=request.user,
                    reason=form.cleaned_data['reason'],
                )
                if should_refund:
                    payment = process_refund(reservation.payment.pk)
                    self._process_credit_note_if_ready(
                        reservation,
                        payment,
                        triggered_by=request.user,
                    )
            except (PaymentError, ReservationUnavailable, stripe.StripeError) as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                if should_refund and payment.status != Payment.Status.REFUNDED:
                    self.message_user(
                        request,
                        _(
                            'The reservation was cancelled, but its refund needs '
                            'attention. Use Retry refund.'
                        ),
                        level=messages.WARNING,
                    )
                else:
                    self.message_user(
                        request,
                        _('The pre-reservation was cancelled.'),
                        level=messages.SUCCESS,
                    )
                return redirect(self._change_url(reservation))
        return self._render_action(
            request,
            reservation,
            title=_('Cancel pre-reservation'),
            warning=_(
                'This immediately releases the dog or litter place. A paid '
                'reservation will be refunded because staff initiated the cancellation.'
            ),
            submit_label=_('Cancel reservation'),
            form=form,
        )

    def retry_refund_view(self, request, object_id):
        reservation = self._get_authorized_object(request, object_id)
        if reservation.payment.status not in {
            Payment.Status.REFUND_PENDING,
            Payment.Status.REFUND_FAILED,
        }:
            self.message_user(
                request,
                _('This payment is not awaiting a refund.'),
                level=messages.ERROR,
            )
            return redirect(self._change_url(reservation))
        if request.method == 'POST':
            payment = process_refund(reservation.payment.pk)
            self._process_credit_note_if_ready(
                reservation,
                payment,
                triggered_by=request.user,
            )
            level = (
                messages.SUCCESS
                if payment.status == Payment.Status.REFUNDED
                else messages.ERROR
            )
            self.message_user(
                request,
                _('Refund status: %(status)s')
                % {'status': payment.get_status_display()},
                level=level,
            )
            return redirect(self._change_url(reservation))
        return self._render_action(
            request,
            reservation,
            title=_('Retry Stripe refund'),
            warning=_(
                'Stripe idempotency prevents this action from creating a duplicate refund.'
            ),
            submit_label=_('Retry refund'),
        )

    def fulfill_view(self, request, object_id):
        reservation = self._get_authorized_object(request, object_id)
        if request.method == 'POST':
            try:
                mark_fulfilled(reservation_id=reservation.pk)
            except ReservationUnavailable as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                self.message_user(
                    request,
                    _('The pre-reservation was marked fulfilled.'),
                    level=messages.SUCCESS,
                )
            return redirect(self._change_url(reservation))
        return self._render_action(
            request,
            reservation,
            title=_('Mark pre-reservation fulfilled'),
            warning=_(
                'Use this only after the reservation has been converted into the '
                'final sale or allocation.'
            ),
            submit_label=_('Mark fulfilled'),
        )

    def _get_authorized_object(self, request, object_id):
        reservation = self.get_object(request, object_id)
        if reservation is None:
            raise Http404
        if not self.has_change_permission(request, reservation):
            raise PermissionDenied
        return reservation

    @staticmethod
    def _sale_document(reservation):
        documents = getattr(reservation, 'admin_erp_documents', None)
        if documents is None:
            documents = reservation.erp_documents.all()
        return next(
            (
                document
                for document in documents
                if document.kind == ERPDocument.Kind.SALE
            ),
            None,
        )

    @staticmethod
    def _can_discard_unpaid_setup_failure(reservation):
        """Only discard records that never reached Stripe or accounting."""
        payment = reservation.payment
        return (
            reservation.status == PreReservation.Status.PAYMENT_FAILED
            and payment.status == Payment.Status.FAILED
            and not payment.stripe_checkout_session_id
            and not payment.stripe_payment_intent_id
            and not reservation.erp_documents.exists()
        )

    @staticmethod
    def _change_url(reservation):
        return reverse(
            'admin:reservations_prereservation_change',
            args=[reservation.pk],
        )

    def _render_action(
        self,
        request,
        reservation,
        *,
        title,
        warning,
        submit_label,
        form=None,
    ):
        context = {
            **self.admin_site.each_context(request),
            'title': title,
            'opts': self.model._meta,
            'reservation': reservation,
            'warning': warning,
            'submit_label': submit_label,
            'form': form,
            'change_url': self._change_url(reservation),
        }
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            'admin/reservations/action_confirmation.html',
            context,
        )

    @staticmethod
    def _process_credit_note_if_ready(
        reservation,
        payment,
        *,
        triggered_by,
    ):
        if payment.status != Payment.Status.REFUNDED:
            return
        sale = reservation.erp_documents.filter(kind=ERPDocument.Kind.SALE).first()
        credit_note = reservation.erp_documents.filter(
            kind=ERPDocument.Kind.CREDIT_NOTE
        ).first()
        if (
            sale
            and sale.status == ERPDocument.Status.INTEGRATED
            and credit_note
        ):
            process_erp_document(
                credit_note.pk,
                trigger=ERPIntegrationAttempt.Trigger.ADMIN,
                triggered_by=triggered_by,
            )


class ReadOnlyOperationalAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    def get_readonly_fields(self, request, obj=None):
        return tuple(
            field.name
            for field in self.model._meta.fields
            if field.name not in self.get_exclude(request, obj)
        )

    def get_exclude(self, request, obj=None):
        return self.exclude or ()


@admin.register(Payment)
class PaymentAdmin(ReadOnlyOperationalAdmin):
    list_display = ('reservation', 'provider', 'status', 'amount', 'paid_at', 'refunded_at')
    list_filter = ('provider', 'status')
    search_fields = (
        'reservation__public_id',
        'stripe_checkout_session_id',
        'stripe_payment_intent_id',
        'stripe_refund_id',
    )


@admin.register(ERPDocument)
class ERPDocumentAdmin(ReadOnlyOperationalAdmin):
    exclude = ('pdf_data',)
    list_display = (
        'external_reference', 'kind', 'status', 'erp_document_number',
        'creation_uncertain', 'attempt_count', 'pdf_status', 'pdf_attempt_count',
        'updated_at',
    )
    list_filter = ('kind', 'status', 'pdf_status')
    search_fields = (
        'external_reference', 'erp_document_id', 'erp_document_number',
        'reservation__public_id', 'reservation__customer_email',
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .defer('pdf_data')
            .select_related('reservation')
        )


@admin.register(ERPIntegrationAttempt)
class ERPIntegrationAttemptAdmin(ReadOnlyOperationalAdmin):
    list_display = ('document', 'trigger', 'result', 'started_at', 'completed_at')
    list_filter = ('trigger', 'result')
    search_fields = ('document__external_reference', 'error_type', 'error_message')


@admin.register(DocumentEmailAttempt)
class DocumentEmailAttemptAdmin(ReadOnlyOperationalAdmin):
    list_display = ('document', 'recipient', 'status', 'created_at', 'sent_at')
    list_filter = ('status',)
    search_fields = ('document__external_reference', 'recipient', 'error_message')


@admin.register(ProcessedStripeEvent)
class ProcessedStripeEventAdmin(ReadOnlyOperationalAdmin):
    list_display = ('event_id', 'event_type', 'reservation', 'processed_at')
    list_filter = ('event_type',)
    search_fields = ('event_id', 'reservation__public_id')
