import os
from typing import Any, Optional, Sequence


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


_DEFAULT_CONTENT_URL_CAP = _positive_int_env("CONTENT_MAX_URLS_PER_SITE", 5000)
CONTENT_MAX_OLD_URLS = _positive_int_env("CONTENT_MAX_OLD_URLS", _DEFAULT_CONTENT_URL_CAP)
CONTENT_MAX_NEW_URLS = _positive_int_env("CONTENT_MAX_NEW_URLS", _DEFAULT_CONTENT_URL_CAP)


class ContentJobUrlCapExceeded(ValueError):
    """
    Raised when a content pipeline request exceeds configured URL caps.
    """

    def __init__(
        self,
        *,
        reason_code: str,
        user_message: str,
        affected_file: str,
        old_url_count: int,
        new_url_count: int,
        max_old_urls: int,
        max_new_urls: int,
        pipeline_type: str = "content",
    ) -> None:
        super().__init__(user_message)
        self.reason_code = reason_code
        self.user_message = user_message
        self.affected_file = affected_file
        self.old_url_count = old_url_count
        self.new_url_count = new_url_count
        self.max_old_urls = max_old_urls
        self.max_new_urls = max_new_urls
        self.pipeline_type = pipeline_type

    def to_api_payload(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": self.user_message,
            "user_message": self.user_message,
            "code": self.reason_code,
            "reason_code": self.reason_code,
            "retryable": False,
            "next_action": "reduce_csv_rows_or_switch_pipeline",
            "affected_file": self.affected_file,
            "pipeline_type": self.pipeline_type,
            "old_url_count": self.old_url_count,
            "new_url_count": self.new_url_count,
            "max_old_urls": self.max_old_urls,
            "max_new_urls": self.max_new_urls,
        }

    def to_worker_error_message(self) -> str:
        return (
            f"{self.reason_code}: {self.user_message} "
            f"(old_url_count={self.old_url_count}, new_url_count={self.new_url_count}, "
            f"max_old_urls={self.max_old_urls}, max_new_urls={self.max_new_urls})"
        )


def validate_content_job_url_counts(
    old_urls: Optional[Sequence[str]],
    new_urls: Optional[Sequence[str]],
    pipeline_type: str = "content",
) -> None:
    """
    Enforce hard caps for content jobs. url_only jobs are exempt.
    """
    if pipeline_type != "content":
        return

    old_count = len(old_urls or ())
    new_count = len(new_urls or ())
    exceeds_old = old_count > CONTENT_MAX_OLD_URLS
    exceeds_new = new_count > CONTENT_MAX_NEW_URLS

    if not (exceeds_old or exceeds_new):
        return

    if exceeds_old and exceeds_new:
        reason_code = "content_both_url_caps_exceeded"
        affected_file = "both"
        user_message = (
            f"Deep Match limits are {CONTENT_MAX_OLD_URLS:,} URLs for Old Site CSV "
            f"and {CONTENT_MAX_NEW_URLS:,} URLs for New Site CSV. "
            f"You uploaded {old_count:,} old URLs and {new_count:,} new URLs. "
            "Split your CSVs or switch to Quick Match."
        )
    elif exceeds_old:
        reason_code = "content_old_url_cap_exceeded"
        affected_file = "old"
        user_message = (
            f"Deep Match has a per-file limit of {CONTENT_MAX_OLD_URLS:,} URLs. "
            f"Your Old Site CSV has {old_count:,} URLs. "
            "Split your CSV or switch to Quick Match."
        )
    else:
        reason_code = "content_new_url_cap_exceeded"
        affected_file = "new"
        user_message = (
            f"Deep Match has a per-file limit of {CONTENT_MAX_NEW_URLS:,} URLs. "
            f"Your New Site CSV has {new_count:,} URLs. "
            "Split your CSV or switch to Quick Match."
        )

    raise ContentJobUrlCapExceeded(
        reason_code=reason_code,
        user_message=user_message,
        affected_file=affected_file,
        old_url_count=old_count,
        new_url_count=new_count,
        max_old_urls=CONTENT_MAX_OLD_URLS,
        max_new_urls=CONTENT_MAX_NEW_URLS,
        pipeline_type=pipeline_type,
    )
