from __future__ import annotations


MOBILE_AUTH_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) OfficeSpaceMobile/1.0"
)

MOBILE_QR_USER_AGENT = "OSSMobile/85 CFNetwork/3860.600.12 Darwin/25.5.0"

DEFAULT_USER_AGENT = MOBILE_AUTH_USER_AGENT

DEFAULT_AUTH_CONFIG_FILE = "auth.json"

OFFICESPACE_LOCAL_DATETIME_FORMAT = "%d %b %Y %H:%M:%S"

DAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

DAY_NAME_TO_INDEX = {
    day_name: day_index for day_index, day_name in enumerate(DAY_NAMES)
}