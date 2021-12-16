import requests

from .__version__ import __version__


class ZenRowsClient:
    api_url = "https://api.zenrows.com/v1/"

    def __init__(self, apikey: str):
        self.apikey = apikey

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

        return requests.get(self.api_url, params=params, headers=final_headers, **kwargs)
