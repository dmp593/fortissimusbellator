from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


class ReservationHistoryDeleteMixin:
    """Require an additional confirmation before deleting reserved targets."""

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    def changeform_view(
        self,
        request,
        object_id=None,
        form_url='',
        extra_context=None,
    ):
        if request.method != 'POST' or object_id is None:
            return super().changeform_view(
                request,
                object_id,
                form_url,
                extra_context,
            )

        # Reservation creation locks the same target row. Holding it across
        # validation and save closes the price/capacity update race.
        with transaction.atomic():
            self.model._default_manager.select_for_update().filter(
                pk=object_id
            ).first()
            return super().changeform_view(
                request,
                object_id,
                form_url,
                extra_context,
            )

    def delete_view(self, request, object_id, extra_context=None):
        if request.method != 'POST':
            return self._reservation_aware_delete_view(
                request,
                object_id,
                extra_context,
            )

        # Serialize deletion with reservation creation so the second-warning
        # decision cannot be based on stale reservation history.
        with transaction.atomic():
            self.model._default_manager.select_for_update().filter(
                pk=object_id
            ).first()
            return self._reservation_aware_delete_view(
                request,
                object_id,
                extra_context,
            )

    def _reservation_aware_delete_view(
        self,
        request,
        object_id,
        extra_context=None,
    ):
        obj = self.get_object(request, object_id)
        if obj is None:
            return super().delete_view(request, object_id, extra_context)

        has_history = obj.pre_reservations.exists()
        is_first_confirmation = (
            request.method == 'POST'
            and request.POST.get('post') == 'yes'
            and request.POST.get('confirm_reservation_history') != 'yes'
        )
        if has_history and is_first_confirmation:
            if not self.has_delete_permission(request, obj):
                raise PermissionDenied
            opts = self.model._meta
            context = {
                **self.admin_site.each_context(request),
                'title': _('Confirm deletion of an item with reservation history'),
                'opts': opts,
                'object': obj,
                'reservation_count': obj.pre_reservations.count(),
                'change_url': reverse(
                    f'admin:{opts.app_label}_{opts.model_name}_change',
                    args=[obj.pk],
                ),
            }
            request.current_app = self.admin_site.name
            return TemplateResponse(
                request,
                'admin/reservations/confirm_target_delete.html',
                context,
            )
        return super().delete_view(request, object_id, extra_context)
