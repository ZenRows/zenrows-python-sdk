import requests
import asyncio
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor
import urllib3
from functools import partial

from .__version__ import __version__


class ZenRowsClient:
    api_url = "https://api.zenrows.com/v1/"

    def __init__(self, apikey: str, retries: int = 0, concurrency: int = 5):
        self.apikey = apikey

        self.executor = ThreadPoolExecutor(max_workers=concurrency)

        self.requests_session = requests.Session()
        if (retries > 0):
            max_retries = Retry().new(
                total=retries,
                backoff_factor=0.5,
                status_forcelist=[422, 429, 500, 502, 503, 504],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=max_retries)
            self.requests_session.mount("https://", adapter)
            self.requests_session.mount("http://", adapter)

    def get(
        self, url: str, params: dict = None, headers: dict = None, **kwargs
    ) -> requests.Response:
        return self._worker("GET", url, params, headers, **kwargs)

    async def get_async(
        self, url: str, params: dict = None, headers: dict = None, **kwargs
    ) -> requests.Response:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, partial(self._worker, "GET", url, params, headers, **kwargs))

    def post(
        self, url: str, params: dict = None, headers: dict = None, data: dict = None, **kwargs
    ) -> requests.Response:
        return self._worker("POST", url, params, headers, data, **kwargs)

    async def post_async(
        self, url: str, params: dict = None, headers: dict = None, data: dict = None, **kwargs
    ) -> requests.Response:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, partial(self._worker, "POST", url, params, headers, data, **kwargs))

    def _worker(
        self, method, url: str, params: dict = None, headers: dict = None, data: dict = None, **kwargs
    ):
        final_params = {}
        if params:
            final_params.update(params)
        final_params.update({"url": url, "apikey": self.apikey})

        final_headers = {"User-Agent": f"zenrows/{__version__} python"}

        if headers:
            final_params["custom_headers"] = True

            final_headers["Accept"] = None
            final_headers["Accept-Encoding"] = urllib3.util.SKIP_HEADER
            final_headers["Connection"] = None
        else:
            headers = {}

        final_headers.update(headers)

        return self.requests_session.request(
            method, self.api_url, params=final_params, headers=final_headers, data=data, **kwargs
        )
