from django.db import models


class Manager(models.Manager):
    filters: dict

    def __init__(self, **kwargs):
        super().__init__()
        self.filters = kwargs

    def get_queryset(self):
        return super().get_queryset().filter(**self.filters)
