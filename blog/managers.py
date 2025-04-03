from django.utils import timezone
from fortissimusbellator.managers import Manager


class PublishedPostsManager(Manager):
    def get_queryset(self):
        return super().get_queryset().filter(
            published_at__lte=timezone.now()
        )
