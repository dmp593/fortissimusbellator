from django.test import SimpleTestCase

from chat.response_policy import has_unsupported_urls


class ModelResponseUrlPolicyTests(SimpleTestCase):
    source = (
        'Website: https://fortissimusbellator.pt\n'
        'Article: https://fortissimusbellator.pt/en/blog/welcome/'
    )

    def test_response_without_urls_is_supported(self):
        self.assertFalse(
            has_unsupported_urls('Please contact the breeder.', self.source)
        )

    def test_exact_source_url_is_supported(self):
        self.assertFalse(
            has_unsupported_urls(
                'Website: https://fortissimusbellator.pt/',
                self.source,
            )
        )

    def test_invented_path_is_rejected(self):
        self.assertTrue(
            has_unsupported_urls(
                'See https://fortissimusbellator.pt/returns',
                self.source,
            )
        )

    def test_external_url_is_rejected(self):
        self.assertTrue(
            has_unsupported_urls(
                'See https://example.com/returns',
                self.source,
            )
        )

    def test_bare_invented_domain_path_is_rejected(self):
        self.assertTrue(
            has_unsupported_urls(
                'See fortissimusbellator.pt/returns',
                self.source,
            )
        )

    def test_bare_relative_path_is_rejected(self):
        self.assertTrue(
            has_unsupported_urls(
                'Open /returns for details.',
                self.source,
            )
        )

    def test_unpublished_relative_markdown_link_is_rejected(self):
        self.assertTrue(
            has_unsupported_urls(
                'Read [the return policy](/returns/).',
                self.source,
            )
        )
