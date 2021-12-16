from unittest import mock, TestCase

from zenrows import ZenRowsClient
from zenrows.__version__ import __version__

apikey = "APIKEY"
url = "http://example.com"
api_url_base = "https://api.zenrows.com/v1/"


class TestZenRowsClient(TestCase):
    @classmethod
    def setUpClass(self):
        self.zenrows_client = ZenRowsClient(apikey)

    @mock.patch("requests.get")
    def test_get_url(self, mock_get):
        self.zenrows_client.get(url)

        mock_get.assert_called_once_with(
            api_url_base,
            params={
                "url": url,
                "apikey": apikey
            },
            headers={
                "User-Agent": f"zenrows/{__version__} python"
            }
        )

    @mock.patch("requests.get")
    def test_get_with_params(self, mock_get):
        self.zenrows_client.get(
            url, params={
                "premium_proxy": True,
                "proxy_country": "us"
            })

        mock_get.assert_called_once_with(
            api_url_base,
            params={
                "url": url,
                "apikey": apikey,
                "premium_proxy": True,
                "proxy_country": "us"
            },
            headers={
                "User-Agent": f"zenrows/{__version__} python"
            }
        )

    @mock.patch("requests.get")
    def test_get_with_headers(self, mock_get):
        self.zenrows_client.get(
            url, headers={"Referrer": "https://www.google.com"})

        mock_get.assert_called_once_with(
            api_url_base,
            params={
                "url": url,
                "apikey": apikey,
                "custom_headers": True
            },
            headers={
                "User-Agent": f"zenrows/{__version__} python",
                "Referrer": "https://www.google.com"
            }
        )

    @mock.patch("requests.get")
    def test_get_overwrite_ua(self, mock_get):
        self.zenrows_client.get(
            url, headers={"User-Agent": "MyCustomUserAgent", })

        mock_get.assert_called_once_with(
            api_url_base,
            params={
                "url": url,
                "apikey": apikey,
                "custom_headers": True
            },
            headers={
                "User-Agent": "MyCustomUserAgent"
            }
        )
