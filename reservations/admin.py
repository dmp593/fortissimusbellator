import decimal

import stripe
from django.contrib import admin, messages
from django.contrib.admin.helpers import AdminForm
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TranslationAdmin

from breeding.models import Animal
from reservations.exceptions import (
    ERPIntegrationError,
    PaymentError,
    ReservationUnavailable,
)
from reservations.forms import (
    AdminAcceptanceForm,
    AdminChargeAdjustmentForm,
    AdminClosureRefundForm,
    AdminCompleteSaleForm,
    AdminManualPaymentForm,
    AdminReservationCancellationForm,
    AdminRetryForm,
    AdminSaleCancellationForm,
    AdminSaleProcessForm,
    AdminWorkflowTransferForm,
    ResendDocumentForm,
)
from reservations.models import (
    AnimalSale,
    AnimalSaleCase,
    AnimalWorkflowTransfer,
    Charge,
    ChargeAdjustment,
    CreditAllocation,
    CustomerCredit,
    DocumentEmailAttempt,
    ERPDocument,
    ERPIntegrationAttempt,
    Payment,
    PaymentRefund,
    PreReservation,
    PreReservationTerms,
    ProcessedStripeEvent,
    Reservation,
    ReservationTerms,
    WorkflowClosure,
)
from reservations.services.closures import (
    calculate_refund_amount,
    record_workflow_closure,
)
from reservations.services.erp import (
    download_erp_pdf,
    ensure_sale_erp_document,
    ensure_erp_pdf_and_email,
    process_erp_document,
)
from reservations.services.notifications import send_document_email
from reservations.services.admin_workflows import (
    cancel_animal_sale,
    complete_existing_sale_case,
    create_admin_pre_reservation,
    create_admin_reservation,
    create_admin_sale,
    record_staff_terms_acceptance,
    synchronize_paid_charge,
)
from reservations.services.ledger import (
    add_charge_adjustment,
    record_manual_payment,
)
from reservations.services.payment import (
    cancel_staff_pre_reservation,
    cancel_staff_reservation,
    process_refund,
    reconcile_sale_case_checkouts_for_admin,
    request_refund,
)
from reservations.services.reservation import (
    accept_pre_reservation,
    reject_pre_reservation,
)
from reservations.services.transfers import transfer_animal_workflow


SETTLED_PAYMENT_STATUSES = (
    Payment.Status.PAID,
    Payment.Status.PARTIALLY_REFUNDED,
    Payment.Status.REFUNDED,
)

ADMIN_SALE_PROCESS_FIELDSETS = (
    (
        _('Purchase'),
        {
            'fields': (
                'start_stage',
                'animal',
            ),
        },
    ),
    (
        _('Customer'),
        {
            'fields': (
                'user',
                'customer_name',
                'customer_email',
                'customer_phone',
                'customer_tax_number',
                'billing_address',
                'billing_postcode',
                'billing_city',
                'billing_country',
                'language_code',
            ),
        },
    ),
    (
        _('Payment'),
        {
            'fields': (
                'amount',
                'payment_provider',
                'terms_accepted_in_person',
                'offer_hours',
                'sold_at',
                'credit',
                'credit_amount',
                'payment_reference',
                'payment_note',
            ),
        },
    ),
    (
        _('Details'),
        {
            'fields': (
                'notes',
                'confirm',
            ),
        },
    ),
)


def _admin_form_context(*, form, model_admin, fieldsets=None):
    resolved_fieldsets = fieldsets or (
        (
            None,
            {
                'fields': tuple(form.fields),
            },
        ),
    )
    admin_form = AdminForm(
        form,
        resolved_fieldsets,
        {},
        (),
        model_admin=model_admin,
    )
    return {
        'form': form,
        'adminform': admin_form,
        'media': admin_form.media,
        'errors': form.errors,
    }


class PreReservationWorkflowFilter(admin.SimpleListFilter):
    title = _('workflow')
    parameter_name = 'workflow'

    def lookups(self, request, model_admin):
        return (
            ('payment', _('Awaiting payment')),
            ('review', _('Awaiting breeder review')),
            ('approved', _('Approved')),
            ('rejected', _('Rejected')),
            ('cancelled', _('Cancelled')),
            ('failed', _('Failed or expired')),
        )

    def queryset(self, request, queryset):
        statuses = {
            'payment': (PreReservation.Status.PENDING_PAYMENT,),
            'review': (PreReservation.Status.AWAITING_REVIEW,),
            'approved': (
                PreReservation.Status.ACCEPTED,
                PreReservation.Status.CONVERTED_TO_RESERVATION,
            ),
            'rejected': (PreReservation.Status.NOT_ACCEPTED,),
            'cancelled': (
                PreReservation.Status.CANCELLED_BY_USER,
                PreReservation.Status.CANCELLED_BY_ADMIN,
            ),
            'failed': (
                PreReservation.Status.PAYMENT_FAILED,
                PreReservation.Status.EXPIRED,
                PreReservation.Status.RESERVATION_OFFER_EXPIRED,
            ),
        }
        selected = statuses.get(self.value())
        return queryset.filter(status__in=selected) if selected else queryset


class ReservationWorkflowFilter(admin.SimpleListFilter):
    title = _('workflow')
    parameter_name = 'workflow'

    def lookups(self, request, model_admin):
        return (
            ('customer', _('Awaiting customer')),
            ('reserved', _('Reserved')),
            ('cancelled', _('Cancelled')),
            ('failed', _('Failed or expired')),
        )

    def queryset(self, request, queryset):
        statuses = {
            'customer': (
                Reservation.Status.OFFERED,
                Reservation.Status.PENDING_PAYMENT,
            ),
            'reserved': (Reservation.Status.CONFIRMED,),
            'cancelled': (Reservation.Status.CANCELLED_BY_ADMIN,),
            'failed': (
                Reservation.Status.PAYMENT_FAILED,
                Reservation.Status.EXPIRED,
            ),
        }
        selected = statuses.get(self.value())
        return queryset.filter(status__in=selected) if selected else queryset


def _workflow_badge(label, tone):
    return format_html(
        '<span class="workflow-badge workflow-badge--{}">'
        '<span class="workflow-badge__dot" aria-hidden="true"></span>'
        '{}</span>',
        tone,
        label,
    )


def _charge_terms_are_pending(charge):
    purchase = charge.purchase
    if isinstance(purchase, PreReservation):
        return (
            purchase.terms_acceptance_source
            == PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER
        )
    if isinstance(purchase, Reservation):
        return (
            purchase.terms_acceptance_source
            == Reservation.TermsAcceptanceSource.PENDING_CUSTOMER
        )
    return False


class ImmutableWorkflowAdmin(admin.ModelAdmin):
    """Expose financial workflow records without allowing field mutation."""

    class Media:
        css = {'all': ('css/admin_workflow.css',)}

    def get_readonly_fields(self, request, obj=None):
        fields = [
            field.name
            for field in self.model._meta.get_fields()
            if field.concrete and not field.many_to_many
        ]
        return tuple(fields)

    def has_add_permission(self, request):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions


class PublishedTermsAdmin(TranslationAdmin):
    list_display = ('version', 'published_at', 'used')
    search_fields = ('version',)
    ordering = ('-published_at', '-pk')

    def has_delete_permission(self, request, obj=None):
        if obj and obj.reservations.exists():
            return False
        return super().has_delete_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if (
            obj
            and obj.reservations.exists()
            and request.method == 'POST'
        ):
            return False
        return super().has_change_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    @admin.display(boolean=True, description=_('used'))
    def used(self, obj):
        return obj.reservations.exists()


@admin.register(PreReservationTerms)
class PreReservationTermsAdmin(PublishedTermsAdmin):
    pass


@admin.register(ReservationTerms)
class ReservationTermsAdmin(PublishedTermsAdmin):
    pass


@admin.register(AnimalSaleCase)
class AnimalSaleCaseAdmin(ImmutableWorkflowAdmin):
    change_form_template = (
        'admin/reservations/animalsalecase/change_form.html'
    )
    list_display = (
        'short_reference',
        'target_name',
        'customer_identity',
        'origin',
        'status_badge',
        'financial_state',
        'created_at',
    )
    list_filter = ('status', 'origin', 'created_at')
    search_fields = (
        'public_id',
        'target_name',
        'customer_name',
        'customer_email',
        'user__email',
    )
    date_hierarchy = 'created_at'
    list_per_page = 50

    def has_add_permission(self, request):
        return request.user.has_perm('reservations.add_animalsalecase')

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related('animal', 'user', 'sale')
            .prefetch_related('charges__payments')
        )

    def get_urls(self):
        opts = self.model._meta
        return [
            path(
                '<path:object_id>/transfer/',
                self.admin_site.admin_view(self.transfer_view),
                name=f'{opts.app_label}_{opts.model_name}_transfer',
            ),
            path(
                '<path:object_id>/complete-sale/',
                self.admin_site.admin_view(self.complete_sale_view),
                name=f'{opts.app_label}_{opts.model_name}_complete_sale',
            ),
        ] + super().get_urls()

    def add_view(self, request, form_url='', extra_context=None):
        if not self.has_add_permission(request):
            raise PermissionDenied
        initial = {}
        if request.method == 'GET':
            stage = request.GET.get('start_stage')
            if stage in {
                AdminSaleProcessForm.Stage.PRE_RESERVATION,
                AdminSaleProcessForm.Stage.RESERVATION,
                AdminSaleProcessForm.Stage.SALE,
            }:
                initial['start_stage'] = stage
            try:
                animal = Animal.objects.filter(
                    pk=request.GET.get('animal'),
                ).first()
            except (TypeError, ValueError):
                animal = None
            if animal is not None:
                initial['animal'] = animal
                initial['amount'] = animal.current_price_in_euros
            if stage == AdminSaleProcessForm.Stage.SALE:
                initial['payment_provider'] = Payment.Provider.CASH
        form = AdminSaleProcessForm(
            request.POST or None,
            initial=initial,
        )
        if request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            common = {
                'animal_id': data['animal'].pk,
                'user': data['user'],
                'customer_data': form.customer_data,
                'payment_provider': data['payment_provider'],
                'created_by': request.user,
                'payment_reference': data['payment_reference'],
                'payment_note': data['payment_note'],
            }
            try:
                stage = data['start_stage']
                if stage == AdminSaleProcessForm.Stage.PRE_RESERVATION:
                    purchase = create_admin_pre_reservation(
                        fee_amount=data['amount'],
                        terms_accepted_in_person=data[
                            'terms_accepted_in_person'
                        ],
                        **common,
                    )
                elif stage == AdminSaleProcessForm.Stage.RESERVATION:
                    purchase = create_admin_reservation(
                        deposit_amount=data['amount'],
                        terms_accepted_in_person=data[
                            'terms_accepted_in_person'
                        ],
                        offer_hours=data['offer_hours'],
                        credit=data['credit'],
                        credit_amount=data['credit_amount'] or 0,
                        **common,
                    )
                else:
                    purchase = create_admin_sale(
                        final_price=data['amount'],
                        sold_at=data['sold_at'],
                        credit=data['credit'],
                        credit_amount=data['credit_amount'] or 0,
                        notes=data['notes'],
                        **common,
                    )
            except (PaymentError, ReservationUnavailable) as exc:
                form.add_error(None, str(exc))
            else:
                sale_case = purchase.sale_case
                self.message_user(
                    request,
                    _('The administrative sale process was created.'),
                    level=messages.SUCCESS,
                )
                return redirect(
                    'admin:reservations_animalsalecase_change',
                    sale_case.pk,
                )
        return self._workflow_form_response(
            request,
            form=form,
            title=_('Start animal sale process'),
            warning=_(
                'Choose the real starting stage and payment method. The '
                'system will preserve all resulting financial records.'
            ),
            submit_label=_('Create process'),
            fieldsets=ADMIN_SALE_PROCESS_FIELDSETS,
            change_url=reverse(
                'admin:reservations_animalsalecase_changelist'
            ),
            object_label=_('New animal sale process'),
        )

    def transfer_view(self, request, object_id):
        sale_case = self._get_object(request, object_id)
        available_value = sum(
            (
                charge.settled_amount
                for charge in sale_case.charges.exclude(
                    stage=Charge.Stage.SALE,
                )
            ),
            decimal.Decimal('0.00'),
        )
        form = AdminWorkflowTransferForm(
            request.POST or None,
            source_case=sale_case,
            initial={
                'transferred_amount': available_value,
                'refund_amount': decimal.Decimal('0.00'),
                'retained_amount': decimal.Decimal('0.00'),
                'difference_payment_provider': Payment.Provider.STRIPE,
            },
        )
        if request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            try:
                reconcile_sale_case_checkouts_for_admin(sale_case.pk)
                transfer, target_stage, refunds = transfer_animal_workflow(
                    source_case_id=sale_case.pk,
                    target_animal_id=data['target_animal'].pk,
                    target_charge_amount=data['target_charge_amount'],
                    transferred_amount=data['transferred_amount'],
                    refund_amount=data['refund_amount'],
                    retained_amount=data['retained_amount'],
                    difference_payment_provider=data[
                        'difference_payment_provider'
                    ],
                    payment_reference=data['payment_reference'],
                    payment_note=data['payment_note'],
                    terms_accepted_in_person=data[
                        'terms_accepted_in_person'
                    ],
                    reason=data['reason'],
                    created_by=request.user,
                    provider_loss_acknowledged=data[
                        'assume_processing_costs'
                    ],
                )
            except (PaymentError, ReservationUnavailable) as exc:
                form.add_error(None, str(exc))
            else:
                for payment_refund in refunds:
                    process_refund(payment_refund.pk)
                self.message_user(
                    request,
                    _('The workflow was transferred to the target dog.'),
                    level=messages.SUCCESS,
                )
                return redirect(
                    'admin:reservations_animalsalecase_change',
                    transfer.target_case_id,
                )
        return self._workflow_form_response(
            request,
            form=form,
            title=_('Transfer to another dog'),
            warning=_(
                'The split must equal the available value. Promotions are '
                'not copied. Any target difference is handled separately.'
            ),
            submit_label=_('Transfer workflow'),
            change_url=self._change_url(sale_case),
            object_label=sale_case,
        )

    def complete_sale_view(self, request, object_id):
        sale_case = self._get_object(request, object_id)
        form = AdminCompleteSaleForm(
            request.POST or None,
            sale_case=sale_case,
            initial={'final_price': sale_case.animal_price_amount},
        )
        if request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            try:
                reconcile_sale_case_checkouts_for_admin(sale_case.pk)
                sale = complete_existing_sale_case(
                    sale_case_id=sale_case.pk,
                    final_price=data['final_price'],
                    payment_provider=data['payment_provider'],
                    sold_at=data['sold_at'],
                    completed_by=request.user,
                    credit=data['credit'],
                    credit_amount=data['credit_amount'] or 0,
                    payment_reference=data['payment_reference'],
                    payment_note=data['payment_note'],
                    notes=data['notes'],
                )
            except (PaymentError, ReservationUnavailable) as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(
                    request,
                    _('The dog was marked as sold with an audited final price.'),
                    level=messages.SUCCESS,
                )
                return redirect(
                    'admin:reservations_animalsale_change',
                    sale.pk,
                )
        return self._workflow_form_response(
            request,
            form=form,
            title=_('Complete final sale'),
            warning=_(
                'The asking price is not changed. The final agreed price is '
                'recorded separately and the dog will be marked as sold.'
            ),
            submit_label=_('Complete sale'),
            change_url=self._change_url(sale_case),
            object_label=sale_case,
        )

    def _get_object(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        obj = self.get_object(request, object_id)
        if obj is None:
            raise Http404
        return obj

    def _workflow_form_response(
        self,
        request,
        *,
        form,
        title,
        warning,
        submit_label,
        change_url,
        object_label,
        fieldsets=None,
    ):
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            'admin/reservations/action_confirmation.html',
            {
                **self.admin_site.each_context(request),
                'opts': self.model._meta,
                'title': title,
                'warning': warning,
                'submit_label': submit_label,
                'object': object_label,
                'change_url': change_url,
                **_admin_form_context(
                    form=form,
                    model_admin=self,
                    fieldsets=fieldsets,
                ),
            },
        )

    def _change_url(self, obj):
        return reverse(
            'admin:reservations_animalsalecase_change',
            args=[obj.pk],
        )

    @admin.display(description=_('reference'))
    def short_reference(self, obj):
        return str(obj.public_id)[:8]

    @admin.display(description=_('customer'))
    def customer_identity(self, obj):
        return obj.customer_email or obj.customer_name or obj.user or '-'

    @admin.display(description=_('status'), ordering='status')
    def status_badge(self, obj):
        tones = {
            AnimalSaleCase.Status.PRE_RESERVATION: 'warning',
            AnimalSaleCase.Status.RESERVATION: 'success',
            AnimalSaleCase.Status.SOLD: 'success',
            AnimalSaleCase.Status.CLOSED: 'neutral',
            AnimalSaleCase.Status.TRANSFERRED: 'info',
        }
        return _workflow_badge(
            obj.get_status_display(),
            tones.get(obj.status, 'info'),
        )

    @admin.display(description=_('financial state'))
    def financial_state(self, obj):
        total = sum(
            (charge.total_amount for charge in obj.charges.all()),
            decimal.Decimal('0.00'),
        )
        settled = sum(
            (charge.settled_amount for charge in obj.charges.all()),
            decimal.Decimal('0.00'),
        )
        return f'{settled} / {total} {obj.currency}'


@admin.register(PreReservation)
class PreReservationAdmin(ImmutableWorkflowAdmin):
    change_form_template = (
        'admin/reservations/prereservation/change_form.html'
    )
    list_display = (
        'short_reference',
        'target_name',
        'customer_email',
        'status_badge',
        'payment_status',
        'reservation_status',
        'erp_state',
        'created_at',
    )
    list_filter = (
        PreReservationWorkflowFilter,
        'status',
        'target_type',
        'charge__status',
        'payment__status',
        'reservation__status',
        'created_at',
    )
    search_fields = (
        'public_id',
        'target_name',
        'customer_name',
        'customer_email',
        'promotion_code',
    )
    date_hierarchy = 'created_at'
    list_per_page = 50
    actions = ('accept_selected_pre_reservations',)
    fieldsets = (
        (
            _('Lifecycle'),
            {
                'fields': (
                    'public_id',
                    'status',
                    'created_at',
                    'updated_at',
                    'hold_expires_at',
                    'confirmed_at',
                    'reviewed_at',
                    'reviewed_by',
                    'review_reason',
                    'cancelled_at',
                    'cancelled_by',
                    'cancellation_reason',
                )
            },
        ),
        (
            _('Dog snapshot'),
            {
                'fields': (
                    'target_type',
                    'animal',
                    'litter',
                    'target_name',
                    'target_breed',
                    'target_birth_date',
                    'target_deleted_at',
                    'animal_price_amount',
                    'reservation_deposit_percentage',
                    'reservation_deposit_amount',
                )
            },
        ),
        (
            _('Customer and billing'),
            {
                'fields': (
                    'user',
                    'customer_name',
                    'customer_email',
                    'customer_phone',
                    'customer_tax_number',
                    'billing_address',
                    'billing_postcode',
                    'billing_city',
                    'billing_country',
                    'language_code',
                )
            },
        ),
        (
            _('Pre-reservation payment snapshot'),
            {
                'fields': (
                    'fee_amount',
                    'discount_amount',
                    'total_amount',
                    'currency',
                    'promotion',
                    'promotion_code',
                    'promotion_discount_type',
                    'promotion_value',
                    'terms',
                    'non_refundable_accepted_at',
                )
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                'animal',
                'litter',
                'user',
                'sale_case',
                'charge',
                'payment',
                'reservation__charge',
                'reservation__payment',
            )
            .prefetch_related(
                'charge__payments__refunds',
                'charge__erp_documents',
                'charge__payments__erp_documents',
                'reservation__charge__erp_documents',
                'reservation__charge__payments__erp_documents',
                'payment__erp_documents',
                'reservation__payment__erp_documents',
            )
        )

    def get_urls(self):
        opts = self.model._meta
        urls = [
            path(
                '<path:object_id>/accept/',
                self.admin_site.admin_view(self.accept_view),
                name=f'{opts.app_label}_{opts.model_name}_accept',
            ),
            path(
                '<path:object_id>/reject/',
                self.admin_site.admin_view(self.reject_view),
                name=f'{opts.app_label}_{opts.model_name}_reject',
            ),
            path(
                '<path:object_id>/cancel/',
                self.admin_site.admin_view(self.cancel_view),
                name=f'{opts.app_label}_{opts.model_name}_cancel',
            ),
        ]
        return urls + super().get_urls()

    @admin.action(description=_('Accept selected paid pre-reservations'))
    def accept_selected_pre_reservations(self, request, queryset):
        accepted = 0
        for pre_reservation in queryset:
            try:
                accept_pre_reservation(
                    pre_reservation_id=pre_reservation.pk,
                    admin_user=request.user,
                )
            except ReservationUnavailable as exc:
                self.message_user(
                    request,
                    f'{pre_reservation}: {exc}',
                    level=messages.ERROR,
                )
            else:
                accepted += 1
        if accepted:
            self.message_user(
                request,
                _('%(count)d pre-reservations accepted.') % {
                    'count': accepted,
                },
                level=messages.SUCCESS,
            )

    def accept_view(self, request, object_id):
        pre_reservation = self._get_change_object(request, object_id)
        if pre_reservation.status != PreReservation.Status.AWAITING_REVIEW:
            self.message_user(
                request,
                _('This pre-reservation is not awaiting review.'),
                level=messages.ERROR,
            )
            return redirect(self._change_url(pre_reservation))
        form = AdminAcceptanceForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                accept_pre_reservation(
                    pre_reservation_id=pre_reservation.pk,
                    admin_user=request.user,
                    reason=form.cleaned_data['reason'],
                )
            except ReservationUnavailable as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(
                    request,
                    _('The pre-reservation was accepted.'),
                    level=messages.SUCCESS,
                )
                return redirect(self._change_url(pre_reservation))
        return self._action_response(
            request,
            pre_reservation,
            title=_('Accept pre-reservation'),
            warning=_(
                'The customer will be allowed to pay the reservation deposit.'
            ),
            submit_label=_('Accept pre-reservation'),
            form=form,
        )

    def reject_view(self, request, object_id):
        pre_reservation = self._get_change_object(request, object_id)
        if pre_reservation.status != PreReservation.Status.AWAITING_REVIEW:
            self.message_user(
                request,
                _('Only pre-reservations awaiting review can be rejected.'),
                level=messages.ERROR,
            )
            return redirect(self._change_url(pre_reservation))
        return self._closure_view(
            request,
            pre_reservation,
            rejected=True,
        )

    def cancel_view(self, request, object_id):
        pre_reservation = self._get_change_object(request, object_id)
        return self._closure_view(
            request,
            pre_reservation,
            rejected=False,
        )

    def _closure_view(self, request, pre_reservation, *, rejected):
        payment_groups = _charge_payment_groups(
            pre_reservation.charge,
            label=_('Pre-reservation payment'),
        )
        form = AdminClosureRefundForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                with transaction.atomic():
                    refund_amount = calculate_refund_amount(
                        sale_case=pre_reservation.sale_case,
                        stage=Charge.Stage.PRE_RESERVATION,
                        calculation_type=(
                            form.cleaned_data['refund_calculation']
                        ),
                        fixed_amount=form.cleaned_data.get('fixed_amount'),
                        target_percentage=form.cleaned_data.get(
                            'target_percentage'
                        ),
                    )
                    if rejected:
                        reject_pre_reservation(
                            pre_reservation_id=pre_reservation.pk,
                            admin_user=request.user,
                            reason=form.cleaned_data['reason'],
                        )
                    else:
                        cancel_staff_pre_reservation(
                            pre_reservation=pre_reservation,
                            admin_user=request.user,
                            reason=form.cleaned_data['reason'],
                        )
                    closure, payment_refunds = record_workflow_closure(
                        sale_case=pre_reservation.sale_case,
                        stage=Charge.Stage.PRE_RESERVATION,
                        kind=(
                            WorkflowClosure.Kind.REJECTED
                            if rejected
                            else WorkflowClosure.Kind.CANCELLED
                        ),
                        reason=form.cleaned_data['reason'],
                        refund_amount=refund_amount,
                        credit_amount=(
                            form.cleaned_data.get('credit_amount') or 0
                        ),
                        created_by=request.user,
                        provider_loss_acknowledged=form.cleaned_data.get(
                            'assume_processing_costs',
                            False,
                        ),
                    )
            except (
                PaymentError,
                ReservationUnavailable,
                stripe.StripeError,
            ) as exc:
                form.add_error(None, str(exc))
            else:
                for payment_refund in payment_refunds:
                    process_refund(payment_refund.pk)
                self.message_user(
                    request,
                    _(
                        'The pre-reservation was closed and the refund '
                        'decision was recorded.'
                    ),
                    level=messages.SUCCESS,
                )
                return redirect(self._change_url(pre_reservation))
        return self._action_response(
            request,
            pre_reservation,
            title=(
                _('Do not accept pre-reservation')
                if rejected
                else _('Cancel pre-reservation')
            ),
            warning=_(
                'No refund is automatic. Split the available value between '
                'refund, customer credit, and the amount retained.'
            ),
            submit_label=(
                _('Do not accept')
                if rejected
                else _('Cancel pre-reservation')
            ),
            form=form,
            payment_groups=payment_groups,
        )

    def _get_change_object(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        obj = self.get_object(request, object_id)
        if obj is None:
            raise Http404
        return obj

    def _action_response(
        self,
        request,
        obj,
        *,
        title,
        warning,
        submit_label,
        form,
        payment=None,
        payment_groups=None,
    ):
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            'admin/reservations/action_confirmation.html',
            {
                **self.admin_site.each_context(request),
                'opts': self.model._meta,
                'title': title,
                'warning': warning,
                'submit_label': submit_label,
                'object': obj,
                'payment': payment,
                'payment_groups': payment_groups or (),
                'change_url': self._change_url(obj),
                **_admin_form_context(
                    form=form,
                    model_admin=self,
                ),
            },
        )

    def _change_url(self, obj):
        return reverse(
            'admin:reservations_prereservation_change',
            args=[obj.pk],
        )

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return False
        if obj.status != PreReservation.Status.PAYMENT_FAILED:
            return False
        has_reservation = Reservation.objects.filter(
            pre_reservation=obj,
        ).exists()
        if has_reservation:
            return False
        payments = _pre_reservation_payments(obj)
        if (
            payments.exclude(status=Payment.Status.FAILED).exists()
            or payments.filter(
                Q(erp_documents__isnull=False)
                | Q(refunds__isnull=False)
            ).exists()
        ):
            return False
        if obj.charge_id and (
            obj.charge.adjustments.exists()
            or obj.charge.credit_allocations.exists()
            or obj.charge.erp_documents.exists()
        ):
            return False
        if obj.sale_case_id and (
            obj.sale_case.closures.exists()
            or obj.sale_case.issued_credits.exists()
            or obj.sale_case.outgoing_transfers.exists()
            or AnimalWorkflowTransfer.objects.filter(
                target_case=obj.sale_case,
            ).exists()
            or AnimalSale.objects.filter(sale_case=obj.sale_case).exists()
            or obj.sale_case.charges.exclude(pk=obj.charge_id).exists()
        ):
            return False
        return super().has_delete_permission(request, obj)

    def delete_view(self, request, object_id, extra_context=None):
        if request.method != 'POST' or request.POST.get('post') != 'yes':
            return super().delete_view(request, object_id, extra_context)

        with transaction.atomic():
            obj = PreReservation.objects.select_for_update().filter(
                pk=object_id,
            ).first()
            if obj is None:
                raise Http404
            if not self.has_delete_permission(request, obj):
                raise PermissionDenied
            charge = obj.charge
            sale_case = obj.sale_case
            _pre_reservation_payments(obj).select_for_update().delete()
            self.log_deletions(request, [obj])
            obj.delete()
            if charge:
                charge.delete()
            if sale_case:
                sale_case.delete()
        self.message_user(
            request,
            _('The failed unpaid attempt was deleted.'),
            level=messages.SUCCESS,
        )
        return redirect('admin:reservations_prereservation_changelist')

    def delete_model(self, request, obj):
        _pre_reservation_payments(obj).delete()
        charge = obj.charge
        sale_case = obj.sale_case
        super().delete_model(request, obj)
        if charge:
            charge.delete()
        if sale_case:
            sale_case.delete()

    @admin.display(description=_('reference'))
    def short_reference(self, obj):
        return str(obj.public_id)[:8]

    @admin.display(description=_('status'), ordering='status')
    def status_badge(self, obj):
        tones = {
            PreReservation.Status.PENDING_PAYMENT: 'warning',
            PreReservation.Status.AWAITING_REVIEW: 'warning',
            PreReservation.Status.ACCEPTED: 'success',
            PreReservation.Status.CONVERTED_TO_RESERVATION: 'success',
            PreReservation.Status.NOT_ACCEPTED: 'danger',
            PreReservation.Status.CANCELLED_BY_USER: 'neutral',
            PreReservation.Status.CANCELLED_BY_ADMIN: 'neutral',
            PreReservation.Status.PAYMENT_FAILED: 'danger',
            PreReservation.Status.EXPIRED: 'neutral',
            PreReservation.Status.RESERVATION_OFFER_EXPIRED: 'neutral',
        }
        return _workflow_badge(
            obj.get_status_display(),
            tones.get(obj.status, 'info'),
        )

    @admin.display(description=_('payment'))
    def payment_status(self, obj):
        if obj.charge_id:
            return obj.charge.get_status_display()
        try:
            return obj.payment.get_status_display()
        except Payment.DoesNotExist:
            return _('Missing')

    @admin.display(description=_('reservation'))
    def reservation_status(self, obj):
        try:
            reservation = obj.reservation
        except Reservation.DoesNotExist:
            return '-'
        return reservation.get_status_display()

    @admin.display(description=_('ERP'))
    def erp_state(self, obj):
        documents = {}
        charges = [obj.charge] if obj.charge_id else []
        try:
            if obj.reservation.charge_id:
                charges.append(obj.reservation.charge)
        except Reservation.DoesNotExist:
            pass
        for charge in charges:
            for document in charge.erp_documents.all():
                documents[document.pk] = document
            for payment in charge.payments.all():
                for document in payment.erp_documents.all():
                    documents[document.pk] = document
        states = [
            str(document.get_status_display())
            for document in documents.values()
        ]
        return ', '.join(states) or '-'


@admin.register(Reservation)
class ReservationAdmin(ImmutableWorkflowAdmin):
    change_form_template = 'admin/reservations/reservation/change_form.html'
    list_display = (
        'short_reference',
        'dog',
        'customer',
        'status_badge',
        'payment_amount',
        'payment_status',
        'offer_expires_at',
    )
    list_filter = (
        ReservationWorkflowFilter,
        'status',
        'charge__status',
        'payment__status',
        'created_at',
    )
    search_fields = (
        'public_id',
        'pre_reservation__target_name',
        'pre_reservation__customer_email',
        'sale_case__target_name',
        'sale_case__customer_email',
    )
    date_hierarchy = 'created_at'
    list_per_page = 50

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                'pre_reservation',
                'sale_case',
                'sale_case__animal',
                'charge',
                'payment',
            )
            .prefetch_related(
                'charge__payments__refunds',
                'charge__erp_documents',
                'charge__payments__erp_documents',
            )
        )

    def get_urls(self):
        opts = self.model._meta
        return [
            path(
                '<path:object_id>/cancel/',
                self.admin_site.admin_view(self.cancel_view),
                name=f'{opts.app_label}_{opts.model_name}_cancel',
            ),
        ] + super().get_urls()

    def cancel_view(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        reservation = self.get_object(request, object_id)
        if reservation is None:
            raise Http404
        payment_groups = _reservation_payment_groups(reservation)
        form = AdminReservationCancellationForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                with transaction.atomic():
                    refund_amount = calculate_refund_amount(
                        sale_case=reservation.sale_case,
                        stage=Charge.Stage.RESERVATION,
                        calculation_type=(
                            form.cleaned_data['refund_calculation']
                        ),
                        fixed_amount=form.cleaned_data.get('fixed_amount'),
                        target_percentage=form.cleaned_data.get(
                            'target_percentage'
                        ),
                    )
                    cancel_staff_reservation(
                        reservation=reservation,
                        admin_user=request.user,
                        reason=form.cleaned_data['reason'],
                    )
                    closure, payment_refunds = record_workflow_closure(
                        sale_case=reservation.sale_case,
                        stage=Charge.Stage.RESERVATION,
                        kind=WorkflowClosure.Kind.CANCELLED,
                        reason=form.cleaned_data['reason'],
                        refund_amount=refund_amount,
                        credit_amount=(
                            form.cleaned_data.get('credit_amount') or 0
                        ),
                        created_by=request.user,
                        provider_loss_acknowledged=form.cleaned_data.get(
                            'assume_processing_costs',
                            False,
                        ),
                    )
            except (
                PaymentError,
                ReservationUnavailable,
                stripe.StripeError,
            ) as exc:
                form.add_error(None, str(exc))
            else:
                for payment_refund in payment_refunds:
                    process_refund(payment_refund.pk)
                self.message_user(
                    request,
                    _('The reservation was cancelled.'),
                    level=messages.SUCCESS,
                )
                return redirect(
                    'admin:reservations_reservation_change',
                    reservation.pk,
                )
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            'admin/reservations/action_confirmation.html',
            {
                **self.admin_site.each_context(request),
                'opts': self.model._meta,
                'title': _('Cancel reservation'),
                'warning': _(
                    'Cancellation can refund, convert to customer credit, or '
                    'retain part or all of the available value.'
                ),
                'submit_label': _('Cancel reservation'),
                'object': reservation,
                'payment_groups': payment_groups,
                'change_url': reverse(
                    'admin:reservations_reservation_change',
                    args=[reservation.pk],
                ),
                **_admin_form_context(
                    form=form,
                    model_admin=self,
                ),
            },
        )

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_('reference'))
    def short_reference(self, obj):
        return str(obj.public_id)[:8]

    @admin.display(description=_('status'), ordering='status')
    def status_badge(self, obj):
        tones = {
            Reservation.Status.OFFERED: 'warning',
            Reservation.Status.PENDING_PAYMENT: 'warning',
            Reservation.Status.CONFIRMED: 'success',
            Reservation.Status.PAYMENT_FAILED: 'danger',
            Reservation.Status.EXPIRED: 'neutral',
            Reservation.Status.CANCELLED_BY_ADMIN: 'neutral',
        }
        return _workflow_badge(
            obj.get_status_display(),
            tones.get(obj.status, 'info'),
        )

    @admin.display(description=_('dog'))
    def dog(self, obj):
        return obj.target_name

    @admin.display(description=_('customer'))
    def customer(self, obj):
        return obj.customer_email

    @admin.display(description=_('payment'))
    def payment_status(self, obj):
        if obj.charge_id:
            return obj.charge.get_status_display()
        try:
            return obj.payment.get_status_display()
        except Payment.DoesNotExist:
            return _('Not started')


@admin.register(Charge)
class ChargeAdmin(ImmutableWorkflowAdmin):
    change_form_template = 'admin/reservations/charge/change_form.html'
    list_display = (
        'short_reference',
        'sale_case',
        'stage',
        'status_badge',
        'total_display',
        'settled_display',
        'due_display',
    )
    list_filter = ('stage', 'status', 'currency', 'created_at')
    search_fields = (
        'public_id',
        'sale_case__public_id',
        'sale_case__target_name',
        'sale_case__customer_email',
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related('sale_case', 'promotion')
            .prefetch_related(
                'adjustments',
                'payments__refunds',
                'credit_allocations',
            )
        )

    def get_urls(self):
        opts = self.model._meta
        return [
            path(
                '<path:object_id>/record-payment/',
                self.admin_site.admin_view(self.record_payment_view),
                name=f'{opts.app_label}_{opts.model_name}_record_payment',
            ),
            path(
                '<path:object_id>/adjust/',
                self.admin_site.admin_view(self.adjust_view),
                name=f'{opts.app_label}_{opts.model_name}_adjust',
            ),
        ] + super().get_urls()

    def record_payment_view(self, request, object_id):
        charge = self._get_charge(request, object_id)
        form = AdminManualPaymentForm(
            request.POST or None,
            initial={'amount': charge.amount_due},
            terms_acceptance_required=_charge_terms_are_pending(charge),
        )
        if request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            try:
                reconcile_sale_case_checkouts_for_admin(
                    charge.sale_case_id,
                )
                with transaction.atomic():
                    if data['terms_accepted_in_person']:
                        record_staff_terms_acceptance(charge.pk)
                    record_manual_payment(
                        charge_id=charge.pk,
                        amount=data['amount'],
                        provider=data['provider'],
                        recorded_by=request.user,
                        external_reference=data['external_reference'],
                        note=data['note'],
                        purchase=charge.purchase,
                    )
                    synchronize_paid_charge(
                        charge.pk,
                        admin_user=request.user,
                    )
                charge.refresh_from_db()
            except (PaymentError, ReservationUnavailable) as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(
                    request,
                    _('The manual payment was recorded.'),
                    level=messages.SUCCESS,
                )
                return redirect(
                    'admin:reservations_charge_change',
                    charge.pk,
                )
        return self._charge_action_response(
            request,
            charge,
            form=form,
            title=_('Record manual payment'),
            warning=_(
                'Only confirm money that was actually received outside '
                'Stripe. This entry is immutable.'
            ),
            submit_label=_('Record payment'),
        )

    def adjust_view(self, request, object_id):
        charge = self._get_charge(request, object_id)
        form = AdminChargeAdjustmentForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            try:
                reconcile_sale_case_checkouts_for_admin(
                    charge.sale_case_id,
                )
                with transaction.atomic():
                    if data['terms_accepted_in_person']:
                        record_staff_terms_acceptance(charge.pk)
                    add_charge_adjustment(
                        charge_id=charge.pk,
                        amount=data['amount'],
                        kind=data['kind'],
                        reason=data['reason'],
                        created_by=request.user,
                    )
                    synchronize_paid_charge(
                        charge.pk,
                        admin_user=request.user,
                    )
            except (PaymentError, ReservationUnavailable) as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(
                    request,
                    _('The financial adjustment was recorded.'),
                    level=messages.SUCCESS,
                )
                return redirect(
                    'admin:reservations_charge_change',
                    charge.pk,
                )
        return self._charge_action_response(
            request,
            charge,
            form=form,
            title=_('Adjust charge'),
            warning=_(
                'This creates an immutable adjustment. It does not overwrite '
                'the original price or payment history.'
            ),
            submit_label=_('Record adjustment'),
        )

    def _get_charge(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        charge = self.get_object(request, object_id)
        if charge is None:
            raise Http404
        return charge

    def _charge_action_response(
        self,
        request,
        charge,
        *,
        form,
        title,
        warning,
        submit_label,
    ):
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            'admin/reservations/action_confirmation.html',
            {
                **self.admin_site.each_context(request),
                'opts': self.model._meta,
                'title': title,
                'warning': warning,
                'submit_label': submit_label,
                'object': charge,
                'change_url': reverse(
                    'admin:reservations_charge_change',
                    args=[charge.pk],
                ),
                **_admin_form_context(
                    form=form,
                    model_admin=self,
                ),
            },
        )

    @admin.display(description=_('reference'))
    def short_reference(self, obj):
        return str(obj.public_id)[:8]

    @admin.display(description=_('status'), ordering='status')
    def status_badge(self, obj):
        tones = {
            Charge.Status.OPEN: 'warning',
            Charge.Status.PARTIALLY_PAID: 'warning',
            Charge.Status.PAID: 'success',
            Charge.Status.VOID: 'neutral',
        }
        return _workflow_badge(
            obj.get_status_display(),
            tones.get(obj.status, 'info'),
        )

    @admin.display(description=_('total'))
    def total_display(self, obj):
        return f'{obj.total_amount} {obj.currency}'

    @admin.display(description=_('settled'))
    def settled_display(self, obj):
        return f'{obj.settled_amount} {obj.currency}'

    @admin.display(description=_('outstanding'))
    def due_display(self, obj):
        return f'{obj.amount_due} {obj.currency}'


@admin.register(ChargeAdjustment)
class ChargeAdjustmentAdmin(ImmutableWorkflowAdmin):
    list_display = ('charge', 'kind', 'amount', 'created_by', 'created_at')
    list_filter = ('kind', 'created_at')
    search_fields = (
        'charge__public_id',
        'charge__sale_case__target_name',
        'reason',
    )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CustomerCredit)
class CustomerCreditAdmin(ImmutableWorkflowAdmin):
    list_display = (
        'short_reference',
        'customer',
        'amount',
        'available',
        'currency',
        'status',
        'created_at',
    )
    list_filter = ('status', 'currency', 'created_at')
    search_fields = (
        'public_id',
        'user__email',
        'customer_email',
        'customer_name',
        'reason',
    )

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_('reference'))
    def short_reference(self, obj):
        return str(obj.public_id)[:8]

    @admin.display(description=_('customer'))
    def customer(self, obj):
        return obj.user or obj.customer_email or obj.customer_name

    @admin.display(description=_('available'))
    def available(self, obj):
        return obj.available_amount


@admin.register(CreditAllocation)
class CreditAllocationAdmin(ImmutableWorkflowAdmin):
    list_display = (
        'credit',
        'charge',
        'amount',
        'created_at',
        'reversed_at',
    )
    list_filter = ('created_at', 'reversed_at')
    search_fields = ('credit__public_id', 'charge__public_id')

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkflowClosure)
class WorkflowClosureAdmin(ImmutableWorkflowAdmin):
    list_display = (
        'short_reference',
        'sale_case',
        'kind',
        'stage',
        'refund_amount',
        'credit_amount',
        'retained_amount',
        'created_at',
    )
    list_filter = ('kind', 'stage', 'created_at')
    search_fields = (
        'public_id',
        'sale_case__public_id',
        'sale_case__target_name',
        'reason',
    )

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_('reference'))
    def short_reference(self, obj):
        return str(obj.public_id)[:8]


@admin.register(AnimalWorkflowTransfer)
class AnimalWorkflowTransferAdmin(ImmutableWorkflowAdmin):
    list_display = (
        'short_reference',
        'source_case',
        'target_case',
        'transferred_amount',
        'refund_amount',
        'retained_amount',
        'created_at',
    )
    list_filter = ('source_stage', 'target_stage', 'created_at')
    search_fields = (
        'public_id',
        'source_case__target_name',
        'target_case__target_name',
        'reason',
    )

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_('reference'))
    def short_reference(self, obj):
        return str(obj.public_id)[:8]


@admin.register(AnimalSale)
class AnimalSaleAdmin(ImmutableWorkflowAdmin):
    change_form_template = 'admin/reservations/animalsale/change_form.html'
    list_display = (
        'short_reference',
        'sale_case',
        'final_price',
        'sold_at',
        'status_badge',
        'source',
        'completed_by',
    )
    list_filter = ('source', 'voided_at', 'sold_at', 'created_at')
    search_fields = (
        'public_id',
        'sale_case__target_name',
        'sale_case__customer_email',
    )
    date_hierarchy = 'sold_at'

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related('sale_case', 'completed_by', 'voided_by')
        )

    def get_urls(self):
        opts = self.model._meta
        return [
            path(
                '<path:object_id>/cancel/',
                self.admin_site.admin_view(self.cancel_view),
                name=f'{opts.app_label}_{opts.model_name}_cancel',
            ),
        ] + super().get_urls()

    def cancel_view(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        animal_sale = self.get_object(request, object_id)
        if animal_sale is None:
            raise Http404
        payment_groups = _sale_payment_groups(animal_sale)
        form = AdminSaleCancellationForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                refund_amount = calculate_refund_amount(
                    sale_case=animal_sale.sale_case,
                    stage=Charge.Stage.SALE,
                    calculation_type=(
                        form.cleaned_data['refund_calculation']
                    ),
                    fixed_amount=form.cleaned_data.get('fixed_amount'),
                    target_percentage=form.cleaned_data.get(
                        'target_percentage'
                    ),
                )
                animal_sale, closure, payment_refunds = cancel_animal_sale(
                    animal_sale_id=animal_sale.pk,
                    reason=form.cleaned_data['reason'],
                    refund_amount=refund_amount,
                    credit_amount=(
                        form.cleaned_data.get('credit_amount') or 0
                    ),
                    cancelled_by=request.user,
                    provider_loss_acknowledged=form.cleaned_data.get(
                        'assume_processing_costs',
                        False,
                    ),
                )
            except (
                PaymentError,
                ReservationUnavailable,
                stripe.StripeError,
            ) as exc:
                form.add_error(None, str(exc))
            else:
                for payment_refund in payment_refunds:
                    process_refund(payment_refund.pk)
                self.message_user(
                    request,
                    _(
                        'The sale was cancelled and its financial outcome '
                        'was recorded.'
                    ),
                    level=messages.SUCCESS,
                )
                return redirect(
                    'admin:reservations_animalsale_change',
                    animal_sale.pk,
                )
        return TemplateResponse(
            request,
            'admin/reservations/action_confirmation.html',
            {
                **self.admin_site.each_context(request),
                'opts': self.model._meta,
                'title': _('Cancel completed sale'),
                'warning': _(
                    'The sale record will remain immutable. The dog will be '
                    'released and the refund, customer credit, and retained '
                    'amount decision will be recorded.'
                ),
                'submit_label': _('Cancel completed sale'),
                'object': animal_sale,
                'payment_groups': payment_groups,
                'change_url': reverse(
                    'admin:reservations_animalsale_change',
                    args=[animal_sale.pk],
                ),
                **_admin_form_context(
                    form=form,
                    model_admin=self,
                ),
            },
        )

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_('reference'))
    def short_reference(self, obj):
        return str(obj.public_id)[:8]

    @admin.display(description=_('status'), ordering='voided_at')
    def status_badge(self, obj):
        if obj.voided_at:
            return _workflow_badge(_('Cancelled'), 'danger')
        return _workflow_badge(_('Sold'), 'success')


@admin.register(Payment)
class PaymentAdmin(ImmutableWorkflowAdmin):
    change_form_template = 'admin/reservations/payment/change_form.html'
    list_display = (
        'id',
        'purchase_link',
        'provider',
        'status',
        'amount',
        'provider_fee_amount',
        'provider_net_amount',
        'paid_at',
    )
    list_filter = ('provider', 'status', 'created_at')
    search_fields = (
        'stripe_checkout_session_id',
        'stripe_payment_intent_id',
        'stripe_charge_id',
        'pre_reservation__public_id',
        'animal_reservation__public_id',
        'charge__public_id',
        'charge__sale_case__public_id',
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                'pre_reservation',
                'animal_reservation__pre_reservation',
                'charge__sale_case',
            )
        )

    def get_urls(self):
        opts = self.model._meta
        return [
            path(
                '<path:object_id>/refund/',
                self.admin_site.admin_view(self.refund_view),
                name=f'{opts.app_label}_{opts.model_name}_refund',
            ),
        ] + super().get_urls()

    def refund_view(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        payment = self.get_object(request, object_id)
        if payment is None:
            raise Http404
        form = AdminClosureRefundForm(request.POST or None)
        form.fields['reason'].label = _('Refund reason')
        if request.method == 'POST' and form.is_valid():
            try:
                payment_refund = _request_refund_from_form(
                    form=form,
                    payment=payment,
                    requested_by=request.user,
                )
                if payment_refund is None:
                    raise PaymentError(_('Choose a refund amount.'))
            except PaymentError as exc:
                form.add_error(None, str(exc))
            else:
                process_refund(payment_refund.pk)
                self.message_user(
                    request,
                    _('The refund request was recorded.'),
                    level=messages.SUCCESS,
                )
                return redirect(
                    'admin:reservations_payment_change',
                    payment.pk,
                )
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            'admin/reservations/action_confirmation.html',
            {
                **self.admin_site.each_context(request),
                'opts': self.model._meta,
                'title': _('Create refund'),
                'warning': _(
                    'Refunds are sent to Stripe immediately and cannot be '
                    'undone from this website.'
                ),
                'submit_label': _('Create refund'),
                'object': payment.purchase,
                'payment': payment,
                'change_url': reverse(
                    'admin:reservations_payment_change',
                    args=[payment.pk],
                ),
                **_admin_form_context(
                    form=form,
                    model_admin=self,
                ),
            },
        )

    @admin.display(description=_('purchase'))
    def purchase_link(self, obj):
        purchase = obj.purchase
        if purchase is None:
            return '-'
        if obj.pre_reservation_id:
            model_name = 'prereservation'
        elif obj.animal_reservation_id:
            model_name = 'reservation'
        else:
            url = reverse(
                'admin:reservations_animalsalecase_change',
                args=[obj.charge.sale_case_id],
            )
            return format_html('<a href="{}">{}</a>', url, purchase)
        url = reverse(
            f'admin:reservations_{model_name}_change',
            args=[purchase.pk],
        )
        return format_html('<a href="{}">{}</a>', url, purchase)


@admin.register(PaymentRefund)
class PaymentRefundAdmin(ImmutableWorkflowAdmin):
    list_display = (
        'short_reference',
        'payment',
        'amount',
        'status',
        'requested_by',
        'requested_at',
        'credit_note_state',
    )
    list_filter = ('status', 'calculation_type', 'requested_at')
    search_fields = ('public_id', 'stripe_refund_id', 'payment__id')
    actions = ('retry_selected_refunds',)

    @admin.action(description=_('Retry selected refunds'))
    def retry_selected_refunds(self, request, queryset):
        for payment_refund in queryset:
            try:
                result = process_refund(payment_refund.pk)
            except (PaymentError, stripe.StripeError) as exc:
                self.message_user(
                    request,
                    f'{payment_refund}: {exc}',
                    level=messages.ERROR,
                )
            else:
                self.message_user(
                    request,
                    f'{payment_refund}: {result.get_status_display()}',
                    level=messages.SUCCESS,
                )

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_('reference'))
    def short_reference(self, obj):
        return str(obj.public_id)[:8]

    @admin.display(description=_('credit note'))
    def credit_note_state(self, obj):
        try:
            return obj.erp_document.get_status_display()
        except ERPDocument.DoesNotExist:
            return '-'


@admin.register(ERPDocument)
class ERPDocumentAdmin(ImmutableWorkflowAdmin):
    change_form_template = 'admin/reservations/erpdocument/change_form.html'
    list_display = (
        'external_reference',
        'kind',
        'amount',
        'currency',
        'status',
        'pdf_status',
        'attempt_count',
        'updated_at',
    )
    list_filter = ('kind', 'status', 'pdf_status')
    search_fields = (
        'external_reference',
        'erp_document_id',
        'erp_document_number',
    )
    actions = ('retry_selected_documents', 'retry_selected_pdfs')

    def get_urls(self):
        opts = self.model._meta
        return [
            path(
                '<path:object_id>/retry/',
                self.admin_site.admin_view(self.retry_view),
                name=f'{opts.app_label}_{opts.model_name}_retry',
            ),
            path(
                '<path:object_id>/retry-pdf/',
                self.admin_site.admin_view(self.retry_pdf_view),
                name=f'{opts.app_label}_{opts.model_name}_retry_pdf',
            ),
            path(
                '<path:object_id>/resend/',
                self.admin_site.admin_view(self.resend_view),
                name=f'{opts.app_label}_{opts.model_name}_resend',
            ),
        ] + super().get_urls()

    @admin.action(description=_('Retry selected ERP integrations'))
    def retry_selected_documents(self, request, queryset):
        for document in queryset:
            result = process_erp_document(
                document.pk,
                trigger=ERPIntegrationAttempt.Trigger.ADMIN,
                triggered_by=request.user,
            )
            self.message_user(
                request,
                f'{document.external_reference}: '
                f'{result.get_status_display()}',
            )

    @admin.action(description=_('Retry selected PDF downloads'))
    def retry_selected_pdfs(self, request, queryset):
        for document in queryset:
            result = download_erp_pdf(document.pk)
            self.message_user(
                request,
                f'{document.external_reference}: '
                f'{result.get_pdf_status_display()}',
            )

    def retry_view(self, request, object_id):
        document = self._get_document(request, object_id)
        form = AdminRetryForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                result = process_erp_document(
                    document.pk,
                    trigger=ERPIntegrationAttempt.Trigger.ADMIN,
                    triggered_by=request.user,
                )
            except ERPIntegrationError as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(
                    request,
                    result.get_status_display(),
                    level=messages.SUCCESS,
                )
                return redirect(
                    'admin:reservations_erpdocument_change',
                    document.pk,
                )
        return self._retry_action_response(
            request,
            document,
            title=_('Retry ERP integration'),
            warning=_(
                'The existing external reference will be reconciled before '
                'any new fiscal document is created.'
            ),
            submit_label=_('Retry ERP'),
            form=form,
        )

    def retry_pdf_view(self, request, object_id):
        document = self._get_document(request, object_id)
        form = AdminRetryForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            result = download_erp_pdf(document.pk)
            level = (
                messages.SUCCESS
                if result.pdf_status == ERPDocument.PDFStatus.AVAILABLE
                else messages.ERROR
            )
            self.message_user(
                request,
                result.get_pdf_status_display(),
                level=level,
            )
            return redirect(
                'admin:reservations_erpdocument_change',
                document.pk,
            )
        return self._retry_action_response(
            request,
            document,
            title=_('Retry fiscal PDF download'),
            warning=_(
                'This retries only the PDF download. It does not create a '
                'new fiscal document.'
            ),
            submit_label=_('Retry PDF'),
            form=form,
        )

    def resend_view(self, request, object_id):
        document = self._get_document(request, object_id)
        if document.charge_id:
            workflow = document.charge.sale_case
        elif document.payment_id and document.payment.pre_reservation_id:
            workflow = document.payment.pre_reservation
        else:
            reservation = document.payment.animal_reservation
            workflow = (
                reservation.pre_reservation or reservation.sale_case
            )
        form = ResendDocumentForm(
            request.POST or None,
            initial={'recipient': workflow.customer_email},
        )
        if request.method == 'POST' and form.is_valid():
            try:
                if document.pdf_status != ERPDocument.PDFStatus.AVAILABLE:
                    document = ensure_erp_pdf_and_email(
                        document.pk,
                        triggered_by=request.user,
                    )
                send_document_email(
                    document=document,
                    recipient=form.cleaned_data['recipient'],
                    triggered_by=request.user,
                )
            except Exception as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(
                    request,
                    _('The fiscal document was emailed.'),
                    level=messages.SUCCESS,
                )
                return redirect(
                    'admin:reservations_erpdocument_change',
                    document.pk,
                )
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            'admin/reservations/action_confirmation.html',
            {
                **self.admin_site.each_context(request),
                'opts': self.model._meta,
                'title': _('Resend fiscal document'),
                'warning': _('Confirm the recipient before sending the PDF.'),
                'submit_label': _('Send PDF'),
                'object': document.purchase,
                'payment': document.payment,
                'change_url': reverse(
                    'admin:reservations_erpdocument_change',
                    args=[document.pk],
                ),
                **_admin_form_context(
                    form=form,
                    model_admin=self,
                ),
            },
        )

    def _get_document(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        document = self.get_object(request, object_id)
        if document is None:
            raise Http404
        return document

    def _retry_action_response(
        self,
        request,
        document,
        *,
        title,
        warning,
        submit_label,
        form,
    ):
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            'admin/reservations/action_confirmation.html',
            {
                **self.admin_site.each_context(request),
                'opts': self.model._meta,
                'title': title,
                'warning': warning,
                'submit_label': submit_label,
                'object': document.purchase,
                'payment': document.payment,
                'change_url': reverse(
                    'admin:reservations_erpdocument_change',
                    args=[document.pk],
                ),
                **_admin_form_context(
                    form=form,
                    model_admin=self,
                ),
            },
        )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ERPIntegrationAttempt)
class ERPIntegrationAttemptAdmin(ImmutableWorkflowAdmin):
    list_display = ('document', 'trigger', 'result', 'started_at')
    list_filter = ('trigger', 'result')

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentEmailAttempt)
class DocumentEmailAttemptAdmin(ImmutableWorkflowAdmin):
    list_display = ('document', 'recipient', 'status', 'created_at')
    list_filter = ('status',)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProcessedStripeEvent)
class ProcessedStripeEventAdmin(ImmutableWorkflowAdmin):
    list_display = ('event_id', 'event_type', 'payment', 'processed_at')
    search_fields = ('event_id', 'event_type')

    def has_delete_permission(self, request, obj=None):
        return False


def _pre_reservation_payments(pre_reservation):
    filters = Q(pre_reservation=pre_reservation)
    if pre_reservation.charge_id:
        filters |= Q(charge_id=pre_reservation.charge_id)
    return Payment.objects.filter(filters).distinct()


def _request_refund_from_form(
    *,
    form,
    payment,
    requested_by,
    acknowledgement_field='assume_processing_costs',
):
    calculation = form.cleaned_data['refund_calculation']
    if calculation == AdminClosureRefundForm.RefundChoice.NONE:
        return None
    if payment is None:
        raise PaymentError(_('There is no payment to refund.'))
    return request_refund(
        payment_id=payment.pk,
        calculation_type=calculation,
        reason=form.cleaned_data['reason'],
        requested_by=requested_by,
        fixed_amount=form.cleaned_data.get('fixed_amount'),
        target_percentage=form.cleaned_data.get('target_percentage'),
        provider_loss_acknowledged=form.cleaned_data.get(
            acknowledgement_field,
            False,
        ),
    )


def _reservation_payment_groups(reservation):
    groups = []
    if reservation.charge_id:
        groups.extend(
            _charge_payment_groups(
                reservation.charge,
                label=_('Reservation deposit payment'),
            )
        )
    else:
        try:
            groups.append(
                (_('Reservation deposit payment'), reservation.payment),
            )
        except Payment.DoesNotExist:
            pass
    if reservation.pre_reservation_id:
        if reservation.pre_reservation.charge_id:
            groups.extend(
                _charge_payment_groups(
                    reservation.pre_reservation.charge,
                    label=_('Pre-reservation payment'),
                )
            )
        else:
            try:
                groups.append(
                    (
                        _('Pre-reservation payment'),
                        reservation.pre_reservation.payment,
                    ),
                )
            except Payment.DoesNotExist:
                pass
    return groups


def _sale_payment_groups(animal_sale):
    groups = []
    for charge in animal_sale.sale_case.charges.all().order_by(
        'created_at',
        'pk',
    ):
        groups.extend(
            _charge_payment_groups(
                charge,
                label=charge.get_stage_display(),
            )
        )
    return groups


def _charge_payment_groups(charge, *, label):
    payments = charge.payments.all().order_by('created_at', 'pk')
    return [
        (
            _('%(stage)s · %(provider)s') % {
                'stage': label,
                'provider': payment.get_provider_display(),
            },
            payment,
        )
        for payment in payments
        if payment.status
        in {
            Payment.Status.PAID,
            Payment.Status.PARTIALLY_REFUNDED,
            Payment.Status.REFUNDED,
        }
    ]


def _request_reservation_refunds_from_form(
    *,
    form,
    reservation,
    requested_by,
):
    calculation = form.cleaned_data['refund_calculation']
    if calculation == AdminClosureRefundForm.RefundChoice.NONE:
        return []

    payments = _refundable_reservation_payments(reservation)
    if not payments:
        raise PaymentError(_('There is no payment to refund.'))

    request_values = {
        'reason': form.cleaned_data['reason'],
        'requested_by': requested_by,
        'provider_loss_acknowledged': form.cleaned_data.get(
            'assume_processing_costs',
            False,
        ),
    }
    if calculation == PaymentRefund.CalculationType.FIXED:
        return _request_fixed_reservation_refunds(
            payments=payments,
            amount=form.cleaned_data['fixed_amount'],
            request_values=request_values,
        )
    if calculation == PaymentRefund.CalculationType.TARGET_PERCENTAGE:
        refunds = _request_percentage_reservation_refunds(
            payments=payments,
            percentage=form.cleaned_data['target_percentage'],
            request_values=request_values,
        )
    elif calculation == PaymentRefund.CalculationType.FULL_REMAINING:
        refunds = [
            request_refund(
                payment_id=payment.pk,
                calculation_type=calculation,
                **request_values,
            )
            for payment in payments
        ]
    else:
        raise PaymentError(_('Choose a valid refund calculation.'))

    if not refunds:
        raise PaymentError(
            _('The selected refund does not leave an amount to return.')
        )
    return refunds


def _refundable_reservation_payments(reservation):
    return [
        payment
        for _, payment in _reservation_payment_groups(reservation)
        if payment.provider == Payment.Provider.STRIPE
        and payment.status
        in {
            Payment.Status.PAID,
            Payment.Status.PARTIALLY_REFUNDED,
        }
        and payment.refundable_amount > 0
    ]


def _request_fixed_reservation_refunds(
    *,
    payments,
    amount,
    request_values,
):
    refundable_total = sum(
        (payment.refundable_amount for payment in payments),
        decimal.Decimal('0.00'),
    )
    if amount > refundable_total:
        raise PaymentError(
            _('The refund cannot exceed the uncommitted payment amount.')
        )

    refunds = []
    remaining = amount
    for payment in payments:
        payment_amount = min(remaining, payment.refundable_amount)
        if payment_amount <= 0:
            continue
        refunds.append(
            request_refund(
                payment_id=payment.pk,
                calculation_type=PaymentRefund.CalculationType.FIXED,
                fixed_amount=payment_amount,
                **request_values,
            )
        )
        remaining -= payment_amount
    return refunds


def _request_percentage_reservation_refunds(
    *,
    payments,
    percentage,
    request_values,
):
    refunds = []
    for payment in payments:
        target = (
            payment.amount * percentage / decimal.Decimal('100')
        ).quantize(decimal.Decimal('0.01'))
        if target <= payment.committed_refund_amount:
            continue
        refunds.append(
            request_refund(
                payment_id=payment.pk,
                calculation_type=(
                    PaymentRefund.CalculationType.TARGET_PERCENTAGE
                ),
                target_percentage=percentage,
                **request_values,
            )
        )
    return refunds
