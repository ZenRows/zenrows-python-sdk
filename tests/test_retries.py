from unittest import TestCase
from unittest.mock import patch

from requests import Session

from zenrows import ZenRowsClient
from zenrows.client import _RETRY_STATUSES

apikey = "APIKEY"
url = "http://example.com"
api_url_base = "https://api.zenrows.com/v1/"


class TestZenRowsClientRetries(TestCase):
    @patch("zenrows.client.Retry")
    @patch.object(Session, "mount")
    def test_custom_session_not_initiated(self, mock_mount, mock_retry):
        ZenRowsClient(apikey, retries=0)

        mock_retry.assert_not_called()
        self.assertEqual(mock_mount.call_count, 2)  # called internally

    @patch("zenrows.client.Retry")
    def test_retry_parameters(self, mock_retry):
        ZenRowsClient(apikey, retries=2)

        mock_retry.assert_called_once_with(
            total=2,
            backoff_factor=0.5,
            status_forcelist=list(_RETRY_STATUSES),
            raise_on_status=False,
        )

    @patch.object(Session, "mount")
    def test_adapter_is_mount(self, mock_mount):
        ZenRowsClient(apikey, retries=2)

        self.assertEqual(mock_mount.call_count, 4)
