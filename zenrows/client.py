import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .__version__ import __version__


class ZenRowsClient:
    api_url = "https://api.zenrows.com/v1/"

    def __init__(self, apikey: str, retries: int = 0):
        self.apikey = apikey
        self.requests_session = requests.Session()
        if (retries > 0):
            max_retries = Retry().new(
                total=retries,
                backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=max_retries)
            self.requests_session.mount("https://", adapter)
            self.requests_session.mount("http://", adapter)

    def get(self, url: str, params: dict = None, headers: dict = None, **kwargs) -> requests.Response:
        if not params:
            params = {}

        if headers:
            params["custom_headers"] = True
        else:
            headers = {}

        final_headers = {"User-Agent": f"zenrows/{__version__} python"}
        final_headers.update(headers)

        params.update({"url": url, "apikey": self.apikey})

        return self.requests_session.get(self.api_url, params=params, headers=final_headers, **kwargs)
