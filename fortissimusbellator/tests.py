from unittest.mock import patch

from django.db.utils import OperationalError
from django.test import RequestFactory, SimpleTestCase

from fortissimusbellator.health import liveness, readiness


class HealthCheckTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get('/health/live/')

    def test_liveness_does_not_depend_on_external_services(self):
        response = liveness(self.request)

        self.assertEqual(response.status_code, 200)

    def test_readiness_reports_database_failure(self):
        with patch('fortissimusbellator.health.connection') as connection:
            connection.cursor.side_effect = OperationalError(
                'database unavailable'
            )
            response = readiness(self.request)

        self.assertEqual(response.status_code, 503)
