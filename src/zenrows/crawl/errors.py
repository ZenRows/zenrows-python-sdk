"""RFC 9457 problem+json → `CrawlAPIError`.

The Crawl API answers errors as `application/problem+json` with a
stable `code`. Branch on
`status_code` + `code`; display `detail`, don't parse it.
"""

import httpx

from zenrows.batch.errors import ProblemDetail

# The code of the 403 answer to a key whose account does not have
# Crawl enabled.
CRAWL_NOT_ENABLED = "REQS008"


class CrawlAPIError(Exception):
    """A non-2xx response from the Crawl API.

    `code` is the problem's `code` member: `crawl_not_found`,
    `content_not_found`, `invalid_parameter`, `too_many_crawls`, ...,
    `REQS008` when Crawl is not enabled for the account, and the API's
    usual auth and credit codes. `problem`
    holds the whole body, or None when it was not JSON.
    """

    def __init__(
        self,
        status_code: int,
        problem: ProblemDetail | None,
        raw: bytes,
        *,
        retry_after: float | None = None,
    ):
        self.status_code = status_code
        # Seconds the server asked to wait (`Retry-After`), e.g. on 429
        # `too_many_crawls`.
        self.retry_after = retry_after
        self.problem = problem
        self.raw = raw
        self.code: str = problem.code if problem else "internal"
        self.detail: str | None = problem.detail if problem else None
        if problem and self.code == CRAWL_NOT_ENABLED:
            msg = f"{status_code} Crawl is not enabled for this account ({CRAWL_NOT_ENABLED})" + (
                f": {problem.detail}" if problem.detail else ""
            )
        elif problem:
            msg = f"{status_code} {problem.title}: {problem.detail or problem.code}"
        else:
            msg = f"{status_code} (no problem body)"
        super().__init__(msg)

    @property
    def not_enabled(self) -> bool:
        """True when the account does not have Crawl enabled (403 REQS008)."""
        return self.status_code == 403 and self.code == CRAWL_NOT_ENABLED

    @classmethod
    def from_response(cls, response: httpx.Response) -> "CrawlAPIError":
        try:
            retry_after: float | None = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after = None
        return cls(
            status_code=response.status_code,
            problem=ProblemDetail.from_response(response),
            raw=response.content,
            retry_after=retry_after,
        )


__all__ = ["CRAWL_NOT_ENABLED", "CrawlAPIError"]
