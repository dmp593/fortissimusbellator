from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tags.models import Tag

from .models import Category, Post


TEST_STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class BlogPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = get_user_model().objects.create_user(
            username='author',
            first_name='Test',
            last_name='Author',
        )
        cls.category = Category.objects.create(name='Care')
        cls.other_category = Category.objects.create(name='Training')
        cls.published = cls.create_post(
            title='Published article',
            published_at=timezone.now() - timedelta(days=1),
            active=True,
            category=cls.category,
        )
        cls.inactive = cls.create_post(
            title='Inactive article',
            published_at=timezone.now() - timedelta(days=1),
            active=False,
            category=cls.category,
        )
        cls.future = cls.create_post(
            title='Future article',
            published_at=timezone.now() + timedelta(days=1),
            active=True,
            category=cls.category,
        )

    @classmethod
    def create_post(
        cls,
        *,
        title,
        published_at,
        active,
        category,
    ):
        post = Post.posts.create(
            author=cls.author,
            title=title,
            cover='posts/test.jpg',
            content={'blocks': []},
            published_at=published_at,
            active=active,
        )
        post.categories.add(category)
        return post

    def test_list_shows_only_active_published_posts_and_caps_page_size(self):
        response = self.client.get(
            reverse('blog:posts'),
            {'per_page': '999'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.published.title)
        self.assertNotContains(response, self.inactive.title)
        self.assertNotContains(response, self.future.title)
        self.assertEqual(response.context['posts'].paginator.per_page, 48)

    def test_load_more_request_uses_cards_partial(self):
        response = self.client.get(
            reverse('blog:posts'),
            HTTP_X_LOAD_MORE='1',
        )

        self.assertTemplateUsed(response, 'blog/partials/cards.html')
        self.assertTemplateNotUsed(response, 'blog/index.html')

    def test_detail_limits_related_posts_to_published_shared_category(self):
        expected = [
            self.create_post(
                title=f'Related {index}',
                published_at=timezone.now() - timedelta(hours=index + 1),
                active=True,
                category=self.category,
            )
            for index in range(5)
        ]
        unrelated = self.create_post(
            title='Unrelated',
            published_at=timezone.now() - timedelta(hours=1),
            active=True,
            category=self.other_category,
        )

        response = self.client.get(
            reverse('blog:post_detail', args=[self.published.pk]),
        )

        related = list(response.context['related_posts'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(related), 4)
        self.assertTrue(set(related).issubset(set(expected)))
        self.assertNotIn(self.published, related)
        self.assertNotIn(unrelated, related)

    def test_unpublished_detail_redirects_to_public_list(self):
        response = self.client.get(
            reverse('blog:post_detail', args=[self.future.pk]),
        )

        self.assertRedirects(response, reverse('blog:posts'))

    def test_detail_renders_generic_tag_value(self):
        Tag.objects.create(
            tag='health',
            content_type=ContentType.objects.get_for_model(Post),
            object_id=self.published.pk,
        )

        response = self.client.get(
            reverse('blog:post_detail', args=[self.published.pk]),
        )

        self.assertContains(response, 'health')
