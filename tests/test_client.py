from unittest import TestCase, mock

import urllib3
from requests import Session

from zenrows import ZenRowsClient
from zenrows.__version__ import __version__

apikey = "APIKEY"
url = "http://example.com"
api_url_base = "https://api.zenrows.com/v1/"
default_headers = {"User-Agent": f"zenrows/{__version__} python"}


class TestZenRowsClient(TestCase):
    @classmethod
    def setUpClass(self):
        self.zenrows_client = ZenRowsClient(apikey)

    @mock.patch.object(Session, "request")
    def test_get_url(self, mock_request):
        self.zenrows_client.get(url)

        mock_request.assert_called_once_with(
            "GET",
            api_url_base,
            params={"url": url, "apikey": apikey},
            headers=default_headers,
            data=None,
        )

    @mock.patch.object(Session, "request")
    def test_get_with_params(self, mock_request):
        self.zenrows_client.get(url, params={"premium_proxy": True, "proxy_country": "us"})

        mock_request.assert_called_once_with(
            "GET",
            api_url_base,
            params={"url": url, "apikey": apikey, "premium_proxy": True, "proxy_country": "us"},
            headers=default_headers,
            data=None,
        )

    @mock.patch.object(Session, "request")
    def test_fetch_is_the_primary_method_get_is_a_deprecated_alias(self, mock_request):
        self.zenrows_client.fetch(url)

        mock_request.assert_called_once_with(
            "GET",
            api_url_base,
            params={"url": url, "apikey": apikey},
            headers=default_headers,
            data=None,
        )

    @mock.patch.object(Session, "request")
    def test_extract_sets_the_extract_param_defaulting_to_auto(self, mock_request):
        self.zenrows_client.extract(url)

        mock_request.assert_called_once_with(
            "GET",
            api_url_base,
            params={"url": url, "apikey": apikey, "extract": "auto"},
            headers=default_headers,
            data=None,
        )

    @mock.patch.object(Session, "request")
    def test_extract_accepts_an_explicit_mode(self, mock_request):
        self.zenrows_client.extract(url, mode="native")

        mock_request.assert_called_once_with(
            "GET",
            api_url_base,
            params={"url": url, "apikey": apikey, "extract": "native"},
            headers=default_headers,
            data=None,
        )

    @mock.patch.object(Session, "request")
    def test_get_with_headers(self, mock_request):
        self.zenrows_client.get(url, headers={"Referrer": "https://www.google.com"})

        mock_request.assert_called_once_with(
            "GET",
            api_url_base,
            params={"url": url, "apikey": apikey, "custom_headers": True},
            headers={
                "User-Agent": f"zenrows/{__version__} python",
                "Referrer": "https://www.google.com",
                "Accept-Encoding": urllib3.util.SKIP_HEADER,
                "Connection": None,
                "Accept": None,
            },
            data=None,
        )

    @mock.patch.object(Session, "request")
    def test_get_overwrite_ua(self, mock_request):
        self.zenrows_client.get(
            url,
            headers={
                "User-Agent": "MyCustomUserAgent",
            },
        )

        mock_request.assert_called_once_with(
            "GET",
            api_url_base,
            params={"url": url, "apikey": apikey, "custom_headers": True},
            headers={
                "User-Agent": "MyCustomUserAgent",
                "Accept-Encoding": urllib3.util.SKIP_HEADER,
                "Connection": None,
                "Accept": None,
            },
            data=None,
        )

    @mock.patch.object(Session, "request")
    def test_post_with_data(self, mock_request):
        self.zenrows_client.post(url, data={"key1": "value1", "key2": "value2"})

        mock_request.assert_called_once_with(
            "POST",
            api_url_base,
            params={
                "url": url,
                "apikey": apikey,
            },
            headers=default_headers,
            data={
                "key1": "value1",
                "key2": "value2",
            },
        )

    @mock.patch.object(Session, "request")
    def test_put_with_data(self, mock_request):
        self.zenrows_client.put(url, data={"key1": "value1", "key2": "value2"})

        mock_request.assert_called_once_with(
            "PUT",
            api_url_base,
            params={
                "url": url,
                "apikey": apikey,
            },
            headers=default_headers,
            data={
                "key1": "value1",
                "key2": "value2",
            },
        )
