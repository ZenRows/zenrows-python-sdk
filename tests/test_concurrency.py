from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from requests import Session

from zenrows import ZenRowsClient

apikey = "APIKEY"
url = "http://example.com"
api_url_base = "https://api.zenrows.com/v1/"


class TestZenRowsClientConcurrency(IsolatedAsyncioTestCase):
    @patch.object(Session, "get")
    async def test_get_async_requests_url(self, mock_get):
        client = ZenRowsClient(apikey, concurrency=2)

        await client.get_async(url)

        self.assertEqual(mock_get.call_count, 1)

    @patch.object(Session, "get")
    async def test_more_urls_than_concurrency(self, mock_get):
        client = ZenRowsClient(apikey, concurrency=2)

        await client.get_async(url)
        await client.get_async(url)
        await client.get_async(url)

        self.assertEqual(mock_get.call_count, 3)
