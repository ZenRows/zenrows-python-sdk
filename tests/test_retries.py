import asyncio
from unittest import TestCase
from unittest.mock import patch

from requests import Response, Session

from zenrows import ZenRowsClient, is_no_data_extracted
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


def _fake_response(status_code: int, body: bytes = b"") -> Response:
    response = Response()
    response.status_code = status_code
    response._content = body
    return response


NO_DATA = b'{"code": "no_data_extracted", "title": "No data extracted"}'
NO_CONTENT = b'{"code": "RESP001", "title": "Could not get content"}'


@patch("zenrows.client.time.sleep")
class TestUnprocessableRetries(TestCase):
    """A 422 is retried in the client, not in urllib3, because only the body
    says whether another try can help."""

    @patch.object(Session, "request")
    def test_no_data_extracted_is_not_retried(self, mock_request, mock_sleep):
        mock_request.return_value = _fake_response(422, NO_DATA)

        response = ZenRowsClient(apikey, retries=3).extract(url)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(mock_request.call_count, 1)
        mock_sleep.assert_not_called()

    @patch.object(Session, "request")
    def test_other_422_is_retried_up_to_retries(self, mock_request, mock_sleep):
        mock_request.return_value = _fake_response(422, NO_CONTENT)

        response = ZenRowsClient(apikey, retries=3).fetch(url)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(mock_request.call_count, 4)  # 1 + 3 retries
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [1.0, 2.0])

    @patch.object(Session, "request")
    def test_other_422_stops_retrying_on_success(self, mock_request, mock_sleep):
        mock_request.side_effect = [_fake_response(422, NO_CONTENT), _fake_response(200, b"ok")]

        response = ZenRowsClient(apikey, retries=3).fetch(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_request.call_count, 2)

    @patch.object(Session, "request")
    def test_422_not_retried_without_retries(self, mock_request, mock_sleep):
        mock_request.return_value = _fake_response(422, NO_CONTENT)

        ZenRowsClient(apikey).fetch(url)

        self.assertEqual(mock_request.call_count, 1)

    @patch.object(Session, "request")
    def test_no_data_extracted_is_not_retried_async(self, mock_request, mock_sleep):
        mock_request.return_value = _fake_response(422, NO_DATA)

        response = asyncio.run(ZenRowsClient(apikey, retries=3).extract_async(url))

        self.assertEqual(response.status_code, 422)
        self.assertEqual(mock_request.call_count, 1)


class TestIsNoDataExtracted(TestCase):
    def test_matches_422_no_data_extracted_any_case(self):
        self.assertTrue(is_no_data_extracted(_fake_response(422, NO_DATA)))
        self.assertTrue(is_no_data_extracted(_fake_response(422, b'{"code": "NO_DATA_EXTRACTED"}')))

    def test_rejects_other_codes_statuses_and_bodies(self):
        self.assertFalse(is_no_data_extracted(_fake_response(422, NO_CONTENT)))
        self.assertFalse(is_no_data_extracted(_fake_response(400, NO_DATA)))
        self.assertFalse(is_no_data_extracted(_fake_response(422, b"not json")))
        self.assertFalse(is_no_data_extracted(_fake_response(422, b"[]")))
