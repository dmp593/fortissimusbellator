from django.contrib.contenttypes.admin import (
    GenericStackedInline, GenericTabularInline
)

from . import models


class TagAdminStackedInline(GenericStackedInline):
    model = models.Tag
    extra = 1  # Number of empty forms to display by default
    fields = ('tag', 'color_light', 'color_dark', )


class TagAdminTabularInline(GenericTabularInline):
    model = models.Tag
    extra = 1  # Number of empty forms to display by default
    fields = ('tag', 'color_light', 'color_dark', )
