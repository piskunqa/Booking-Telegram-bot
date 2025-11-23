import logging
import math
import time
from abc import ABC
from datetime import date, datetime

from peewee import OperationalError, MySQLDatabase, SENTINEL
from telebot import types

from config import RECORD_PER_PAGE, RECORDS_ROWS, texts


class ReconnectMixin(object):
    reconnect_errors = (
        (OperationalError, '2006'),
        (OperationalError, '2013'),
        (OperationalError, '2003'),
        (OperationalError, '2014'),
        (OperationalError, 'MySQL Connection not available.'),
    )

    def __init__(self, *args, **kwargs):
        super(ReconnectMixin, self).__init__(*args, **kwargs)

        self._reconnect_errors = {}
        for exc_class, err_fragment in self.reconnect_errors:
            self._reconnect_errors.setdefault(exc_class, [])
            self._reconnect_errors[exc_class].append(err_fragment.lower())

    def execute_sql(self, sql, params=None, commit=SENTINEL, tr=0):
        try:
            return super(ReconnectMixin, self).execute_sql(sql, params, commit)
        except Exception as exc:
            exc_class = type(exc)
            if exc_class not in self._reconnect_errors:
                raise exc

            exc_repr = str(exc).lower()
            for err_fragment in self._reconnect_errors[exc_class]:
                if err_fragment in exc_repr:
                    break
            else:
                raise exc

            if not self.is_closed():
                self.close()
            try:
                self.connect()
            except Exception as e:
                logging.error("CONNECTION ERROR: %s", str(e))

            if tr >= 20:
                raise exc
            time.sleep(tr / 10)
            return self.execute_sql(sql, params, commit, tr=tr + 1)


class MySQLDatabaseReconnected(ReconnectMixin, MySQLDatabase, ABC): ...


def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def build_resources_keyboard(model, page: int = 1) -> types.InlineKeyboardMarkup:
    """
    Builds an InlineKeyboard with resources for the specified page (1-indexed).
    Resource buttons use the callback_data "res:<id>".
    Navigation buttons use the callback_data "page:<page_number>"
    """
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    total = model.select().where(model.status == 1).count()
    if total == 0:
        return keyboard
    total_pages = math.ceil(total / RECORD_PER_PAGE)
    page = max(1, min(page, total_pages))
    query = model.select().where(model.status == 1).order_by(model.created).paginate(page, RECORD_PER_PAGE)
    for i in chunks(list(query), RECORDS_ROWS):
        keyboard.row(
            *[types.InlineKeyboardButton(text=str(res.location), callback_data=f"res:{res.id}:{page}") for res in i])
    nav_buttons = []
    if page > 1:
        nav_buttons.append(types.InlineKeyboardButton(text=texts.go_back, callback_data=f"page:{page - 1}:"))
    nav_buttons.append(types.InlineKeyboardButton(text=f"{texts.page} {page}/{total_pages}", callback_data="null"))
    if page < total_pages:
        nav_buttons.append(types.InlineKeyboardButton(text=texts.go_next, callback_data=f"page:{page + 1}:"))
    keyboard.row(*nav_buttons)
    return keyboard


def date_in_bookings(day_date, booked_ranges):
    """
        Checks whether a given date falls within any booked range.

        This function iterates through the list of booked date ranges and
        determines whether `day_date` lies between the start and end of any
        range (inclusive).

        Args:
            day_date: A `date` object representing the day to check.
            booked_ranges: A list of tuples in the format (start_date, end_date),
                           where each element is a `date` representing a continuous
                           booked interval.

        Returns:
            True if `day_date` falls within at least one booked range.
            False otherwise.
    """
    for (s, e) in booked_ranges:
        if s <= day_date <= e:
            return True
    return False


def get_day_status(day_date, bookings, current_user_id):
    """
        Determines the status of a specific date based on existing bookings.

        This function checks whether `day_date` falls within any of the provided
        booking ranges and identifies whether the booking belongs to the current user
        or another user.

        Booking entries must be in the format:
            (telegram_id, start_date, end_date)

        Status values:
            - "mine"    → the date is booked by the current user
            - "others"  → the date is booked by someone else
            - "free"    → the date is not booked at all

        Args:
            day_date: A `date` object representing the day being evaluated.
            bookings: A list of tuples (telegram_id, start_date, end_date).
            current_user_id: Telegram ID of the user interacting with the calendar.

        Returns:
            A string representing the booking status of the date:
            "mine", "others", or "free".
    """
    for (t, s, e) in bookings:
        if s <= day_date <= e:
            if t == current_user_id:
                return "mine"
            else:
                return "others"
    return "free"


def get_booked_ranges_for_resource(res_id, model):
    """
        Returns all confirmed booked date ranges for a specific resource.

        This function queries the Booking table and retrieves only those bookings that:
            - Belong to the given resource ID.
            - Have a status of "confirmed".
            - Have a check-out date that is either not set or occurs today or later
              (past bookings are skipped to improve performance).

        For each valid booking:
            - Ensures check-in and check-out values are converted to `date` objects.
            - If check-out is missing, the check-in date is used as both start and end.
            - Bookings that ended before today are ignored.

        The returned list contains tuples of the form:
            (telegram_id, start_date, end_date)

        Args:
            res_id: The ID of the resource whose booked ranges should be retrieved.

        Returns:
            A list of tuples representing booked date ranges:
                [
                    (user_telegram_id, check_in_date, check_out_date),
                    ...
                ]
    """
    today = date.today()
    q = (model
         .select(model.telegram_id, model.check_in, model.check_out)
         .where(((model.resource == res_id) & (model.check_out.is_null() | (model.check_out >= today)) & (
            model.status == "confirmed"))))
    booked = []
    for b in q:
        if b.check_in is None:
            continue
        starts = b.check_in
        end = b.check_out or b.check_in
        if isinstance(starts, datetime):
            starts = starts.date()
        if isinstance(end, datetime):
            end = end.date()
        if end < today:
            continue
        booked.append((b.telegram_id, starts, end))
    return booked
