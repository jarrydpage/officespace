from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=None)
def load_query(name: str) -> str:
    return files(__package__).joinpath(f"{name}.graphql").read_text(encoding="utf-8").strip()


CREATE_BOOKING_MUTATION = load_query("create_booking_series")
CURRENT_USER_QUERY = load_query("current_user_linked_employee")
MY_BOOKINGS_QUERY = load_query("my_bookings")
SEAT_SITE_QUERY = load_query("seat_site")
SITE_BOOKING_WINDOW_QUERY = load_query("site_booking_window")