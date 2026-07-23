from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .forms import PromotionAdminForm
from .models import Promotion


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    form = PromotionAdminForm
    list_display = (
        'code',
        'discount_type',
        'value',
        'scope',
        'active',
        'starts_at',
        'ends_at',
        'used',
    )
    list_filter = ('active', 'discount_type', 'scope')
    search_fields = ('code',)
    filter_horizontal = ('breeds', 'dogs', 'litters')
    readonly_fields = ('created_at', 'updated_at')

    class Media:
        js = ('discounts/admin/promotion_scope.js',)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.pre_reservations.exists():
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    @admin.display(description=_('used'))
    def used(self, obj):
        return obj.pre_reservations.exists()
