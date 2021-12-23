# ZenRows Python SDK
SDK to access [ZenRows](https://www.zenrows.com/) API directly from Python. ZenRows handles proxies rotation, headless browsers, and CAPTCHAs for you.

## Installation
Install the SDK with pip.

```bash
pip install zenrows
```

## Usage
Start using the API by [creating your API Key](https://www.zenrows.com/register?p=free).

The SDK uses [requests](https://docs.python-requests.org/) for HTTP requests. The client's response will be a requests `Response`.

It also uses [Retry](https://urllib3.readthedocs.io/en/latest/reference/urllib3.util.html) to automatically retry failed requests (status codes 429, 500, 502, 503, and 504). Retries are not active by default; you need to specify the number of retries, as shown below. It already includes an exponential back-off retry delay between failed requests.

```python
from zenrows import ZenRowsClient

client = ZenRowsClient("YOUR-API-KEY", retries=1)
url = "https://www.zenrows.com/"

response = client.get(url, params={
    # Our algorithm allows to automatically extract content from any website
    "autoparse": False,

    # CSS Selectors for data extraction (i.e. {"links":"a @href"} to get href attributes from links)
    "css_extractor": "",

    # Enable Javascript with a headless browser (5 credits)
    "js_render": False,

    # Use residential proxies (10 credits)
    "premium_proxy": False,

    # Make your request from a given country. Requires premium_proxy
    "proxy_country": "us",

    # Wait for a given CSS Selector to load in the DOM. Requires js_render
    "wait_for": ".content",

    # Wait a fixed amount of time in milliseconds. Requires js_render
    "wait": 2500,

    # Block specific resources from loading, check docs for the full list. Requires js_render
    "block_resources": "image,media,font",

    # Change the browser's window width and height. Requires js_render
    "window_width": 1920,
    "window_height": 1080,

    # Will automatically use either desktop or mobile user agents in the headers
    "device": "desktop",

    # Will return the status code returned by the website
    "original_status": False,
}, headers={
    "Referrer": "https://www.google.com",
    "User-Agent": "MyCustomUserAgent",
})

print(response.text)
```

You can also pass optional `params` and `headers`; the list above is a reference. For more info, check out [the documentation page](https://www.zenrows.com/documentation).

Sending headers to the target URL will overwrite our defaults. Be careful when doing it and contact us if there is any problem.

## Contributing
Pull requests are welcome. For significant changes, please open an issue first to discuss what you would like to change.

## License
[MIT](./LICENSE)
