from django.contrib import admin
from . import models, forms


@admin.register(models.AnimalFile)
class AnimalFileAdmin(admin.ModelAdmin):
    form = forms.AnimalFileForm
    ordering = ('-animal', 'order')
    list_display = (
        'animal',
        'filename',
        'content_type'
    )
    list_filter = (
        'animal',
        'content_type',
    )


class AnimalFileStackedInline(admin.StackedInline):
    model = models.AnimalFile
    form = forms.AnimalFileInlineForm


class AnimalCertificationStackedInline(admin.StackedInline):
    model = models.AnimalCertification


@admin.register(models.Animal)
class AnimalAdmin(admin.ModelAdmin):
    form = forms.AnimalForm

    inlines = [AnimalFileStackedInline, AnimalCertificationStackedInline]

    list_display = (
        'name',
        'breed',
        'current_price_in_euros',
    )

    list_filter = (
        'name',
        'breed',
    )
    
    ordering = ('order',)


@admin.register(models.Breed)
class BreedAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'parent',
    )

    list_filter = (
        'parent',
    )

    ordering = ('order',)


@admin.register(models.Certification)
class CertificationAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'parent',
    )

    list_filter = (
        'parent',
    )

    ordering = ('order',)


admin.site.register(models.AnimalKind)
admin.site.register(models.AnimalCertification)
