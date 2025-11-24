import json
import threading
import time
from calendar import monthrange, month_name, day_abbr
from datetime import datetime, timedelta, date
from os import execv, remove
from os.path import isfile, join, dirname, exists
from sys import executable, argv
from uuid import uuid4

import telebot
from peewee import DoesNotExist
from telebot import types

from config import BOT_TOKEN, UPLOAD_BASE, PROVIDER_TOKEN, CURRENCY, STATE_FILE_NAME, ADMIN_IDS, MANAGERS_CHAT, \
    REFUND_PERCENTS
from config import texts
from models import Resource, Images, Booking
from utils import build_resources_keyboard, get_day_status, get_booked_ranges_for_resource

# bot configurations
bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=15, skip_pending=True)
bot.set_my_commands([
    telebot.types.BotCommand("/start", texts.start_command),
    telebot.types.BotCommand("/bookings", texts.my_bookings_command),
    telebot.types.BotCommand("/help", texts.talk_manager_command),
])

# variables
user_booking_state, help_waiting_for_input, help_cancel_messages = {}, set(), {}
STATE_FILE = join(dirname(__file__), STATE_FILE_NAME)


def save_user_booking_state():
    """
        Saves the current `user_booking_state` dictionary to a JSON file.

        Behavior:
        - Iterates over all user states in `user_booking_state`.
        - Converts all `datetime` and `date` objects (e.g., 'created', 'check_in', 'check_out')
          into ISO 8601 formatted strings, which are JSON-serializable.
        - Preserves other data types (ints, strings, etc.) as-is.
        - Writes the resulting dictionary to the file specified by `STATE_FILE`
          in UTF-8 encoding with pretty-printed indentation.

        Purpose:
        - Provides persistent storage of in-memory booking states so that the bot can
          restore them after a restart or crash.

        Notes:
        - This function only serializes the current state in memory; it does not modify it.
        - Ensure `STATE_FILE` is defined as a valid file path accessible by the bot.

        Example:
            save_user_booking_state()
    """
    data = {}
    for user_id, state in user_booking_state.items():
        data[user_id] = {}
        for k, v in state.items():
            if isinstance(v, (datetime, date)):
                data[user_id][k] = v.isoformat()
            else:
                data[user_id][k] = v
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_user_booking_state():
    """
        Loads the `user_booking_state` dictionary from a JSON file.

        Behavior:
        - Checks if the file specified by `STATE_FILE` exists.
        - Reads the JSON data from the file.
        - Iterates over each user state and converts ISO 8601 strings back into
          `datetime` or `date` objects:
            - 'created' is converted to `datetime`
            - 'check_in' and 'check_out' are converted to `date`
        - Preserves other data types (ints, strings, etc.) as-is.
        - Stores the restored states in the global `user_booking_state` dictionary.
        - Delete json file after store in memory

        Purpose:
        - Restores previously saved in-memory booking states after a bot restart
          or crash.

        Notes:
        - If the file does not exist, `user_booking_state` is initialized as an empty dictionary.
        - Any parsing errors during datetime conversion will leave the value as-is.

        Example:
            load_user_booking_state()
    """
    global user_booking_state
    if exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        user_booking_state = {}
        for user_id, state in data.items():
            restored_state = {}
            for k, v in state.items():
                if k in ["created", "check_in", "check_out"] and v is not None:
                    try:
                        restored_state[k] = datetime.fromisoformat(v)
                        if k in ["check_in", "check_out"]:
                            restored_state[k] = restored_state[k].date()
                    except Exception:
                        restored_state[k] = v
                else:
                    restored_state[k] = v
            user_booking_state[int(user_id)] = restored_state
        remove(STATE_FILE)
    else:
        user_booking_state = {}


@bot.message_handler(commands=['start'], chat_types=["private"])
def start(message):
    """
        Handles the /start command in private chats.

        This function checks if there are any available Resource entries.
        - If no resources exist, it sends a message informing the user that no records are available.
        - If resources exist, it builds the first page of the resource selection keyboard
          and sends a message prompting the user to choose a location.

        Args:
            message: The Telegram message object containing user information and chat context.
    """
    total = Resource.select().count()
    if total == 0:
        bot.send_message(message.chat.id, texts.no_records)
        return
    keyboard = build_resources_keyboard(Resource, page=1)
    bot.send_message(message.chat.id, texts.pick_address, reply_markup=keyboard)


@bot.message_handler(commands=["restart"], chat_types=["private"], func=lambda ms: ms.from_user.id in ADMIN_IDS)
def restart_bot(message):
    """
        Handles the /restart command for administrators.

        Behavior:
        - Only allows users whose ID is in `ADMIN_IDS` to execute the command.
        - Sends a message to the user indicating that the bot is restarting.
        - Saves the current `user_booking_state` to a JSON file using `save_user_booking_state()`.
        - Replaces the current Python process with a new one using `execv`,
          effectively restarting the bot while keeping the same command-line arguments.

        Purpose:
        - Provides a safe way for administrators to restart the bot without
          losing in-memory booking state.

        Notes:
        - The restart is immediate; the current bot process is terminated and
          replaced by a new one.
        - Ensure `save_user_booking_state()` successfully completes before `execv`
          to avoid state loss.

        Example:
            /restart  # issued by an admin user
    """
    bot.send_message(message.chat.id, texts.restart_bot_msg)
    save_user_booking_state()
    execv(executable, [executable] + argv)


@bot.message_handler(commands=["help"], chat_types=["private"])
def help_command(message: types.Message):
    """
        Handles the /help command in private chat.

        Behavior:
        1. Retrieves the user’s Telegram ID.
        2. Creates an inline keyboard with a single “Cancel” button.
           - The button uses callback_data="help_cancel".
        3. Sends a message to the user asking what question they want to send to the manager.
           This message includes the inline keyboard for cancellation.
        4. Stores the sent message_id in help_cancel_messages[user_id] so that it can be
           deleted later if the user presses the cancel button.
        5. Adds the user_id to help_waiting_for_input, meaning:
           - The next non-command message from this user will be forwarded to MANAGERS_CHAT.
           - After forwarding, the user will be removed from help_waiting_for_input.

        This function is the entry point for the help/manager communication workflow.
    """
    user_id = message.from_user.id
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(texts.cancel, callback_data="help_cancel"))
    cancel_msg = bot.send_message(user_id, texts.help_question, reply_markup=keyboard)
    help_cancel_messages[user_id] = cancel_msg.message_id
    help_waiting_for_input.add(user_id)


@bot.callback_query_handler(func=lambda cq: cq.data == "help_cancel")
def callback_help_cancel(cq: types.CallbackQuery):
    """
        Handles the "Cancel" button for the /help workflow.

        Behavior:
        1. Acknowledges the callback query and shows a short notification
           (texts.help_canceled_msg) to the user.
        2. If the user was waiting to submit a message to the managers
           (i.e., their ID is present in help_waiting_for_input):
             — Removes the user_id from help_waiting_for_input,
               effectively canceling the help request session.
        3. Deletes the message that contained the “Cancel” button,
           removing the prompt from the user's chat.

        This function stops the help flow and prevents the user's next message
        from being forwarded to the managers.
    """
    bot.answer_callback_query(cq.id, texts.help_canceled_msg)
    if cq.from_user.id in help_waiting_for_input:
        help_waiting_for_input.discard(cq.from_user.id)
    bot.delete_message(chat_id=cq.message.chat.id, message_id=cq.message.message_id)


@bot.message_handler(func=lambda m: m.from_user.id in help_waiting_for_input, content_types=["text"],
                     chat_types=["private"])
def handle_help_message(message: types.Message):
    """
        Processes the user's next message after they triggered the /help command.

        Behavior:
        1. If the incoming message starts with "/", it is treated as a bot command.
           In this case the help session is canceled silently and the message is
           NOT forwarded to the managers.

        2. For regular text messages:
             — Forwards the user's message to MANAGERS_CHAT so managers can read it.
             — Sends a confirmation message back to the user
               (texts.success_help_msg).
             — Deletes the previously sent "What is your question?" message
               containing the Cancel button (if it still exists).

        3. Regardless of the message type, the user's ID is removed from
           help_waiting_for_input so the bot no longer waits for their help message.

        This function completes the help flow by forwarding the user's request
        and cleaning up temporary UI elements.
    """
    user_id = message.from_user.id
    if message.text.startswith("/"):
        return help_waiting_for_input.discard(user_id)
    bot.forward_message(chat_id=MANAGERS_CHAT, from_chat_id=message.chat.id, message_id=message.message_id)
    bot.send_message(user_id, texts.success_help_msg)
    if msg_id := help_cancel_messages.pop(user_id, None):
        try:
            if msg_id:
                bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception:
            pass

    return help_waiting_for_input.discard(user_id)


@bot.message_handler(func=lambda message: message.chat.id == MANAGERS_CHAT and message.reply_to_message,
                     content_types=['text', 'photo', 'document'], chat_types=["group"])
def handle_all_messages(message):
    """
        Handles manager replies inside the managers' group chat.

        Expected workflow:
        — A user's private /help message is forwarded to MANAGERS_CHAT.
        — Managers can respond by replying directly to the forwarded message.
        — This handler catches that reply and delivers it back to the original user.

        Behavior:
        1. Works only in MANAGERS_CHAT and only if the message is a reply
           (message.reply_to_message exists).

        2. Extracts the original sender's user_id from the forwarded message's
           forward_from field. If the forwarded message lacks forward_from
           (rare cases such as privacy restrictions), the reply is ignored.

        3. Depending on the content type:
             — text: forward text wrapped with texts.manager_title
             — photo: forward the highest resolution photo along with caption
             — document: forward the document and caption

        4. Messages sent back to the user always include texts.manager_title to
           clarify that the response is from a manager, not the bot itself.

        This function completes the two-way communication between users and managers
        without exposing the managers' identities to users.
    """
    original = message.reply_to_message
    if not original.forward_from:
        return
    user_id = original.forward_from.id
    if message.content_type == 'text':
        bot.send_message(user_id, f"{texts.manager_title}\n{message.text}")
    elif message.content_type == 'photo':
        caption = message.caption or ""
        file_id = message.photo[-1].file_id
        bot.send_photo(user_id, file_id, caption=f"{texts.manager_title}\n{caption}")
    elif message.content_type == 'document':
        caption = message.caption or ""
        bot.send_document(user_id, message.document.file_id, caption=f"{texts.manager_title}\n{caption}")


@bot.callback_query_handler(func=lambda cq: bool(cq.data) and cq.data.startswith("res:"))
def callback_resource(cq: types.CallbackQuery):
    """
        Handles selection of a resource when a user clicks a resource button.

        Callback data format:
            "res:<resource_id>:<page>"

        Special case:
            If page == "!", the callback is triggered from a user's booking view
            and sid refers to a booking ID. In this case, it displays booking info
            along with resource details.

        Behavior:
        1. Acknowledges the callback query to stop Telegram’s loading animation.
        2. Extracts the resource (or booking) ID and page from cq.data.
        3. Loads the Resource (or Booking if page == "!") from the database.
           - If invalid ID or resource does not exist, sends an error message.
        4. Builds a textual description including:
           - Location
           - Price
           - Currency
           - Description (optional)
           - If from booking view (page == "!"), also includes check-in and check-out dates.
        5. Loads all related Images for this resource and opens them for sending.
        6. Deletes the previous message to avoid clutter.
        7. Sends the images as a media group (gallery) if any exist.
        8. Builds an inline keyboard:
             - From booking view (page == "!"):
                 • Go back button
                 • Cancel booking button (if check-in > today)
             - Normal view:
                 • Go back button to resource list page
                 • Go to book button
        9. Sends a new message with the resource info and keyboard.
        10. Ensures all opened files are properly closed in a finally block.

        Notes:
        - This function allows viewing both resource info and a specific user's booking.
        - The gallery messages' IDs are tracked for later deletion when navigating pages or canceling bookings.
    """
    bot.answer_callback_query(cq.id)
    _, sid, page = cq.data.split(":", 2)
    if page == "!":
        booking = Booking.get_by_id(sid)
        sid = booking.resource_id
    try:
        res_id = int(sid)
    except ValueError:
        return bot.send_message(cq.message.chat.id, texts.wrong_id)
    try:
        res = Resource.get(Resource.id == res_id)
    except DoesNotExist:
        return bot.send_message(cq.message.chat.id, texts.not_found)
    text = f"📍 {res.location}\n💵 {texts.price} {res.price} {texts.currency}{f'\n📝 {texts.description} {res.description}' if res.description else ''}"
    if page == "!":
        text += f"\n📅 {texts.check_in}: {booking.check_in}\n🚀 {texts.check_out}: {booking.check_out}\n"
    text = text[:1019] + "…" if len(text) > 1024 else text
    imgs = list(Images.select().where(Images.resource == res).order_by(Images.id))
    media, opened_files = [], []
    try:
        for i, img in enumerate(imgs):
            path = join(dirname(__file__), UPLOAD_BASE, str(res.id), img.filename)
            if isfile(path):
                f = open(path, "rb")
                opened_files.append(f)
                media.append(types.InputMediaPhoto(media=f))
        bot.delete_message(chat_id=cq.message.chat.id, message_id=cq.message.message_id)
        gallery = bot.send_media_group(cq.message.chat.id, media) if media else []
        keyboard = types.InlineKeyboardMarkup()
        remove_ids = ",".join(str(i.message_id) for i in gallery)
        if page == "!":
            buttons = [types.InlineKeyboardButton(
                texts.go_back, callback_data=f"my_booking:{remove_ids}")]
            if booking.check_in > date.today():
                buttons.append(
                    types.InlineKeyboardButton(texts.cancel_book,
                                               callback_data=f"cancel_my_booking:{booking.id}:{remove_ids}"))
            keyboard.row(*buttons)
        else:
            keyboard.row(
                types.InlineKeyboardButton(
                    texts.go_back, callback_data=f"page:{page}:{",".join(str(i.message_id) for i in gallery)}"),
                types.InlineKeyboardButton(texts.go_book, callback_data=f"book:{res.id}:{page}:{remove_ids}")
            )
        bot.send_message(cq.message.chat.id, text, reply_markup=keyboard)
    finally:
        for f in opened_files:
            try:
                f.close()
            except Exception:
                pass


@bot.callback_query_handler(func=lambda cq: bool(cq.data) and cq.data.startswith("page:"))
def callback_page(cq: types.CallbackQuery):
    """
        Handles pagination for the resource list.

        Triggered when the user clicks a "Next" or "Previous" page button
        whose callback data begins with "page:".

        The callback data contains:
            - The target page number.
            - A list of message IDs (image gallery) that must be removed
              before displaying the resource list again.

        Behavior:
        1. Acknowledge the callback query.
        2. If message IDs to remove are included:
            - Delete all gallery messages that were previously sent.
            - Replace the current message text with the default prompt.
            - Rebuild and show the first page of the resource list.
        3. If no gallery messages are provided:
            - Update only the inline keyboard of the current message
              to show the requested page.

        Args:
            cq: The callback query triggered when the user interacts
                with pagination buttons.
    """
    bot.answer_callback_query(cq.id)
    _, s_page, remove_ids = cq.data.split(":", 2)
    if not remove_images(remove_ids, cq):
        page = int(s_page) if s_page.isdigit() else 1
        bot.edit_message_reply_markup(chat_id=cq.message.chat.id, message_id=cq.message.message_id,
                                      reply_markup=build_resources_keyboard(Resource, page=page))


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("book:"))
def callback_book(cq: types.CallbackQuery):
    """
        Handles the user's selection of the "Book" button for a specific resource.

        Triggered when the callback data begins with "book:".

        The callback data includes:
            - The selected resource ID.
            - The originating resource list page.
            - A list of message IDs belonging to the image gallery
              that should be removed before proceeding.

        Behavior:
        1. Acknowledge the callback query.
        2. If gallery message IDs are provided:
             - Delete all associated media group messages.
        3. Initialize the user's booking state, including:
             - Resource ID
             - Empty check-in and check-out dates
             - Current year and month for calendar navigation
             - Timestamp of booking flow start
        4. Display the calendar interface for selecting the check-in date.

        Args:
            cq: The callback query sent when the user presses the "Book" button.
    """
    bot.answer_callback_query(cq.id)
    _, res_id, page, remove_ids = cq.data.split(":", 3)
    if remove_ids:
        for gallery_id in remove_ids.split(","):
            bot.delete_message(chat_id=cq.message.chat.id, message_id=int(gallery_id))
    res_id = int(res_id)
    today = datetime.now()
    user_booking_state[cq.from_user.id] = {
        'res_id': res_id,
        'check_in': None,
        'check_out': None,
        'calendar_year': today.year,
        'calendar_month': today.month,
        "created": today
    }
    send_calendar(cq.message, cq.from_user.id, page, select_type='check_in')


def send_calendar(message, user_id, page, select_type='check_in', prefix_text=""):
    """
        Renders an interactive calendar for selecting check-in or check-out dates
        for a specific resource, taking into account existing bookings.

        Args:
            message: Telegram message object to edit with the calendar.
            user_id: Telegram user ID who is selecting the dates.
            page: The current page or context identifier for resource pagination.
            select_type: 'check_in' or 'check_out', determines which date the user is picking.
            prefix_text: Optional text to prepend above the calendar.

        Behavior:
        1. Validates that the user has an active booking session in user_booking_state.
           - If not, displays a session error message.
        2. Retrieves the user's current booking state including:
           - calendar year and month
           - resource ID
           - check-in/check-out selection progress
        3. Fetches all booked ranges for this resource using get_booked_ranges_for_resource.
        4. Builds an inline keyboard representing the calendar:
             - First row: Month and Year (non-clickable)
             - Second row: Day abbreviations (Mon, Tue, …) (non-clickable)
             - Remaining rows: Dates of the month
                 • Dates in the past: disabled ("—")
                 • Dates booked by the user: green square ("🟩")
                 • Dates booked by others: red square ("🟥")
                 • Dates unavailable for selection (e.g., before check-in for check-out selection): disabled
                 • Available dates: clickable buttons with callback_data `datepick:<select_type>:<iso_date>:<page>`
        5. Adds navigation buttons at the bottom:
             - "Go Back" to resource view
             - "⬅️" Previous month
             - "➡️" Next month
        6. Edits the original message to display the calendar with optional prefix text
           and the inline keyboard.

        Notes:
        - This function ensures the user cannot select invalid dates or dates already booked by others.
        - It visually distinguishes between free, self-booked, and other-booked dates.
        - Works for both initial check-in selection and subsequent check-out selection.
    """
    if user_id not in user_booking_state:
        bot.edit_message_text(chat_id=message.chat.id, message_id=message.message_id, text=texts.long_session_error)
    state = user_booking_state[user_id]
    year = state['calendar_year']
    month = state['calendar_month']
    res_id = state['res_id']
    booked_ranges = get_booked_ranges_for_resource(res_id, Booking)
    keyboard = types.InlineKeyboardMarkup(row_width=7)
    keyboard.add(types.InlineKeyboardButton(f"{month_name[month]} {year}", callback_data="null"))
    keyboard.add(*[types.InlineKeyboardButton(d, callback_data="null") for d in day_abbr])
    first_weekday, days_in_month = monthrange(year, month)
    today = date.today()
    min_date = today if select_type == "check_in" else date.max if state.get("check_in") is None else state.get(
        "check_in")
    month_buttons = []
    for _ in range(first_weekday):
        month_buttons.append(types.InlineKeyboardButton(" ", callback_data="null"))
    for day in range(1, days_in_month + 1):
        day_date = date(year, month, day)
        if day_date < today:
            month_buttons.append(types.InlineKeyboardButton("—", callback_data="null"))
            continue
        status = get_day_status(day_date, booked_ranges, user_id)
        if status == "mine":
            month_buttons.append(types.InlineKeyboardButton(f"🟩", callback_data="null"))
            continue
        elif status == "others":
            month_buttons.append(types.InlineKeyboardButton(f"🟥", callback_data="null"))
            continue
        if select_type == "check_out" and day_date <= min_date:
            month_buttons.append(types.InlineKeyboardButton("—", callback_data="null"))
            continue
        if select_type == "check_in":
            next_book_start = None
            for (t, s, e) in booked_ranges:
                if s > day_date:
                    if next_book_start is None or s < next_book_start:
                        next_book_start = s
        month_buttons.append(
            types.InlineKeyboardButton(str(day), callback_data=f"datepick:{select_type}:{day_date.isoformat()}:{page}"))
    for i in range(0, len(month_buttons), 7):
        keyboard.add(*month_buttons[i:i + 7])
    prev_month = datetime(year, month, 1) - timedelta(days=1)
    next_month = datetime(year, month, days_in_month) + timedelta(days=1)
    keyboard.add(
        types.InlineKeyboardButton(texts.go_back, callback_data=f"res:{res_id}:{page}"),
        types.InlineKeyboardButton("⬅️",
                                   callback_data=f"month:{prev_month.year}:{prev_month.month}:{select_type}:{page}"),
        types.InlineKeyboardButton("➡️",
                                   callback_data=f"month:{next_month.year}:{next_month.month}:{select_type}:{page}")
    )
    bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=message.message_id,
        text=f"{prefix_text}{texts.pick_date} {texts.check_in.lower() if select_type == 'check_in' else texts.check_out.lower()}:",
        reply_markup=keyboard
    )


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("datepick:"))
def callback_datepick(cq: types.CallbackQuery):
    """
        Handles user selection of a date from the interactive calendar.

        Callback data format:
            "datepick:<select_type>:<ISO_date>:<page>"

        Args:
            cq: Telegram CallbackQuery object.

        Behavior:
        1. Acknowledges the callback query to stop Telegram’s loading animation.
        2. Extracts the select_type ('check_in' or 'check_out'), the selected date, and page info.
        3. Converts the ISO date string to a datetime.date object.
        4. Retrieves the user's booking state from user_booking_state.

        5. If select_type is 'check_in':
             - Saves the selected date as check-in in the user's state.
             - Sends a new calendar for selecting check-out dates.

        6. If select_type is 'check_out':
             - Validates that the check-out date is after the check-in date; shows an alert if invalid.
             - Checks for overlapping with already confirmed bookings:
                 • If the selected range overlaps any existing confirmed booking, resets check-in and
                   prompts the user to select a new range via a calendar with an overlap warning.
             - Otherwise, saves the selected check-out date in the user's state.
             - Builds a summary message with:
                 • Resource location
                 • Picked period (check-in and check-out dates)
             - Adds an inline keyboard with:
                 • "Go Back" button to resource booking
                 • "Apply" button to confirm the booking
             - Edits the original calendar message with the summary text and keyboard.

        Notes:
        - Ensures that users cannot select a check-out date earlier than check-in.
        - Prevents booking overlaps with already confirmed bookings for the same resource.
        - Supports both stepwise date selection (check-in first, then check-out) and visual feedback.
    """
    bot.answer_callback_query(cq.id)
    _, select_type, date_str, page = cq.data.split(":", 3)
    date_obj = datetime.fromisoformat(date_str).date()
    state = user_booking_state[cq.from_user.id]
    if select_type == 'check_in':
        state['check_in'] = date_obj
        send_calendar(cq.message, cq.from_user.id, page, select_type='check_out')
    elif select_type == 'check_out':
        if date_obj <= state['check_in']:
            return bot.answer_callback_query(cq.id, texts.date_range_error, show_alert=True)
        booked_ranges = [(b.check_in, b.check_out) for b in Booking.select().where(
            (Booking.resource == state['res_id']) & (Booking.status == 'confirmed'))]
        for s, e in booked_ranges:
            if not s or not e:
                continue
            if state['check_in'] <= e and date_obj >= s:
                state['check_in'] = None
                return send_calendar(cq.message, cq.from_user.id, page, select_type='check_in',
                                     prefix_text=texts.overlaps_error)
        state['check_out'] = date_obj
        res = Resource.get_by_id(state['res_id'])
        text = (f"{res.location}\n{texts.picked_period}\n"
                f"{texts.check_in}: {state['check_in']}\n{texts.check_out}: {state['check_out']}")
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton(texts.go_back, callback_data=f"book:{state['res_id']}:{page}:"),
            types.InlineKeyboardButton(texts.apply, callback_data="confirm_booking")
        )
        bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id, text=text,
                              reply_markup=keyboard)
    return None


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("month:"))
def callback_change_month(cq: types.CallbackQuery):
    """
        Handles month navigation in the inline calendar.

        Triggered when the user clicks the previous or next month button
        in the calendar. The callback data format is:
            month:<year>:<month>:<select_type>:<page>

        Behavior:
        1. Acknowledges the callback query.
        2. Updates the user's booking state with the selected year and month.
        3. Re-renders the calendar message for the new month,
           keeping the same selection mode ('check_in' or 'check_out').

        Args:
            cq: The callback query triggered by pressing a month navigation button.
    """
    bot.answer_callback_query(cq.id)
    if cq.from_user.id not in user_booking_state:
        bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id,
                              text=texts.long_session_error)
    _, year, month, select_type, page = cq.data.split(":", 4)
    year, month = int(year), int(month)
    state = user_booking_state[cq.from_user.id]
    state['calendar_year'] = year
    state['calendar_month'] = month
    send_calendar(cq.message, cq.from_user.id, page, select_type=select_type)


@bot.callback_query_handler(func=lambda cq: cq.data == "confirm_booking")
def callback_confirm_booking(cq: types.CallbackQuery):
    """
        Handles the final confirmation of a booking and initiates the payment process.

        Triggered when the user presses the "Confirm Booking" button.

        Workflow:
        1. Acknowledges the callback query.
        2. Checks if the user's booking state exists and that both check-in and check-out
           dates are selected. If not, sends a session error message.
        3. Retrieves the selected resource from the database.
        4. Creates a new Booking record with status 'waiting_payment' and a unique order ID.
        5. Calculates the total payment amount based on the resource price and number of booked days.
        6. Sends a Telegram invoice to the user with the booking details.
        7. If sending the invoice fails:
            - Marks the booking status as 'failed'.
            - Updates the message with an error description.
        8. Deletes the previous calendar or booking message to keep the chat clean.
        9. Removes the user's temporary booking state from memory.

        Args:
            cq: The callback query triggered when the user presses "Confirm Booking".
    """
    bot.answer_callback_query(cq.id)
    if cq.from_user.id not in user_booking_state:
        bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id,
                              text=texts.long_session_error)
    state = user_booking_state.get(cq.from_user.id)
    if state is None or state['check_in'] is None or state['check_out'] is None:
        return bot.send_message(cq.message.chat.id, texts.session_error)
    resource = Resource.get_by_id(state['res_id'])
    booking = Booking.create(telegram_id=cq.message.chat.id, resource_id=resource, check_in=state['check_in'],
                             check_out=state['check_out'], status='waiting_payment',
                             order=f"{int(time.time())}-{uuid4().hex[:6]}")
    title = f"{texts.booking} {resource.location}"
    description = f"{texts.check_in}: {booking.check_in} {texts.check_out}: {booking.check_out}"
    amount = int(float(((booking.check_out - booking.check_in).days + 1) * resource.price) * 100)
    prices = [types.LabeledPrice(label=title, amount=amount)]
    try:
        bot.send_invoice(
            chat_id=cq.from_user.id,
            invoice_payload=str(booking.id),
            title=title,
            description=description[:1000],
            provider_token=PROVIDER_TOKEN,
            currency=CURRENCY,
            prices=prices,
            start_parameter=f"booking{booking.id}"
        )
    except Exception as e:
        booking.status = 'failed'
        booking.save()
        bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id,
                              text=f"{texts.payment_error}: {e}")
    bot.delete_message(chat_id=cq.from_user.id, message_id=cq.message.message_id)
    return user_booking_state.pop(cq.from_user.id, None)


@bot.pre_checkout_query_handler(func=lambda q: True)
def handle_pre_checkout_query(pre_q: types.PreCheckoutQuery):
    """
    Handles pre-checkout validation before confirming a payment.

    Triggered when a user attempts to pay an invoice. This function ensures
    the integrity of the booking and prevents double-booking. It performs
    the following checks:

    1. Confirms that the booking exists and its status is 'waiting_payment'.
       If not, the payment is rejected.

    2. Ensures that both check-in and check-out dates are set.
       If not, the booking status is marked as 'failed' and payment is rejected.

    3. Checks for booking conflicts:
       - Finds any existing confirmed bookings for the same resource
         that overlap with the selected date range.
       - If a conflict exists, rejects the payment and updates the booking
         status to 'conflict'. Notifies the user of the problem.

    4. Validates the total payment amount to ensure it matches the expected
       amount based on the resource price and number of booked days.

    5. If all checks pass, allows the payment to proceed by approving the
       pre-checkout query.

    Args:
        pre_q: The PreCheckoutQuery object representing the pending invoice.
    """
    invoice_payload = pre_q.invoice_payload
    user_id = pre_q.from_user.id
    booking = Booking.get_by_id(invoice_payload)
    if booking.status != 'waiting_payment':
        return bot.answer_pre_checkout_query(pre_q.id, ok=False, error_message=texts.pre_checkout_error_2)
    if not booking.check_in or not booking.check_out:
        bot.answer_pre_checkout_query(pre_q.id, ok=False, error_message=texts.pre_checkout_error_3)
        booking.status = 'failed'
        booking.save()
        return None
    conflict_q = (Booking.select().where(
        (Booking.resource == booking.resource) &
        (Booking.id != booking.id) &
        (Booking.status == 'confirmed') &
        (Booking.check_in <= booking.check_out) &
        (Booking.check_out >= booking.check_in)
    ).limit(1))

    if conflict_q.exists():
        bot.answer_pre_checkout_query(pre_q.id, ok=False, error_message=texts.pre_checkout_error_4)
        booking.status = 'conflict'
        booking.save()
        return bot.send_message(user_id, texts.payment_error_notification)
    try:
        if hasattr(pre_q, "total_amount") and pre_q.total_amount is not None:
            if int(pre_q.total_amount) != int(
                    float(((booking.check_out - booking.check_in).days + 1) * booking.resource.price) * 100):
                return bot.answer_pre_checkout_query(pre_q.id, ok=False, error_message="Некорректная сумма к оплате.")
    except Exception:
        pass
    return bot.answer_pre_checkout_query(pre_q.id, ok=True)


@bot.message_handler(content_types=['successful_payment'])
def handle_successful_payment(message: types.Message):
    """
        Handles the notification of a successful payment from Telegram.

        Triggered when the user completes a payment for an invoice.

        Workflow:
        1. Retrieves the invoice payload to identify the corresponding booking.
        2. Checks if the booking exists. If not, informs the user about an error.
        3. If the booking is already confirmed, informs the user that the booking
           has been previously confirmed.
        4. Otherwise, updates the booking status to 'confirmed' in the database.
        5. Sends a confirmation message to the user including:
           - Booking reference number
           - Resource location
           - Check-in and check-out dates

        Args:
            message: The Telegram message object containing successful payment data.
    """
    sp = message.successful_payment
    payload = sp.invoice_payload
    payer_id = message.from_user.id
    try:
        booking = Booking.get_by_id(payload)
    except Exception:
        return bot.send_message(payer_id, texts.error_after_payment)
    if booking.status == 'confirmed':
        return bot.send_message(payer_id, "Бронь уже подтверждена — спасибо за оплату.")
    booking.status = 'confirmed'
    booking.amount = float(sp.total_amount / 100)
    booking.save()
    bot.send_message(
        MANAGERS_CHAT,
        f"🟢🟢🟢{texts.booking_completed}🟢🟢🟢\n"
        f"{texts.user}: @{message.from_user.username} ({payer_id})\n"
        f"{texts.address}: {booking.resource.location}\n"
        f"{texts.period}: {booking.check_in} → {booking.check_out}\n"
        f"{texts.amount}: {booking.amount}\n",
        parse_mode="Markdown"
    )
    return bot.send_message(payer_id, f"{texts.success_booking}\n#{booking}: {booking.resource.location}\n"
                                      f"{texts.check_in}: {booking.check_in}\n{texts.check_out}: {booking.check_out}")


@bot.callback_query_handler(func=lambda cq: cq.data == "null")
def callback_null(cq: types.CallbackQuery):
    """
        Handles clicks on non-interactive (placeholder) inline buttons.

        Triggered when the callback data of the button is "null".

        Purpose:
            - These buttons are used as placeholders in the inline keyboard,
              such as day labels, empty cells in the calendar, or non-selectable dates.
            - Acknowledges the callback query to prevent Telegram from showing
              a loading indicator.
            - Any exceptions during acknowledgment are silently ignored, since
              no further action is needed.

        Args:
            cq: The callback query triggered by clicking a "null" button.
    """
    try:
        bot.answer_callback_query(cq.id)
    except Exception:
        pass


@bot.message_handler(commands=["bookings"], chat_types=["private"])
def all_my_bookings(message, edit_msg_text=None):
    """
        Displays all active (confirmed) future bookings for the requesting user.

        Args:
            message: Telegram message object (from the user requesting bookings).
            edit_msg_text: Optional text to prepend if editing an existing message.

        Behavior:
        1. Retrieves the current user’s Telegram ID from message.chat.id.
        2. Queries the Booking model for all confirmed bookings:
             - Owned by the current user (telegram_id)
             - Status is 'confirmed'
             - Check-out date is later than today
             - Orders results by check-in date descending
        3. If no bookings are found:
             - Sends a message or edits the existing message to indicate no bookings found.
        4. Otherwise:
             - Builds an InlineKeyboardMarkup with one button per booking.
                 • Button text: "<Resource location> (<check-in> → <check-out>)"
                 • Callback data: "res:<booking_id>:!"
             - If edit_msg_text is provided, edits the existing message with the keyboard.
             - Otherwise, sends a new message with the keyboard.

        Notes:
        - The callback "res:<booking_id>:!" allows the user to view booking details or cancel if applicable.
        - Only future bookings are displayed; past bookings are ignored.
        - Ensures a clean, interactive interface for managing user bookings.
    """
    user_id = message.chat.id
    today = date.today()
    my_bookings = (Booking.select().where((Booking.telegram_id == user_id) &
                                          (Booking.status == "confirmed") &
                                          (Booking.check_out > today)).order_by(Booking.check_in.desc()))
    if not my_bookings.exists():
        if edit_msg_text:
            return bot.edit_message_text(chat_id=user_id, message_id=message.message_id,
                                         text=f"{edit_msg_text or ''}{texts.bookings_not_found}")
        else:
            return bot.send_message(user_id, texts.bookings_not_found)
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for b in my_bookings:
        keyboard.add(types.InlineKeyboardButton(f"{b.resource.location} ({b.check_in} → {b.check_out})",
                                                callback_data=f"res:{b.id}:!"))
    if edit_msg_text:
        return bot.edit_message_text(chat_id=user_id, message_id=message.message_id, text=edit_msg_text,
                                     reply_markup=keyboard)
    else:
        return bot.send_message(user_id, texts.my_bookings, reply_markup=keyboard)


def remove_images(remove_ids: list | str, cq: types.CallbackQuery):
    """
        Deletes Telegram messages corresponding to media/image galleries and optionally
        refreshes the resource selection keyboard.

        Args:
            remove_ids: Either a list of message IDs or a comma-separated string of message IDs to delete.
            cq: Telegram CallbackQuery object that triggered this action.

        Returns:
            True if any messages were deleted and keyboard refreshed, False otherwise.

        Behavior:
        1. If remove_ids is provided:
             - Converts a comma-separated string to a list of IDs if needed.
             - Iterates through each message ID and deletes the corresponding message in the chat.
             - Edits the current callback message to display the main resource selection text
               and keyboard.
        2. If remove_ids is empty or None:
             - Returns False without performing any deletion.

        Notes:
        - Ensures that old media messages from galleries do not clutter the chat.
        - Automatically restores the resource selection keyboard after removal.
        - Can handle both string and list inputs for flexibility.
    """
    if remove_ids:
        for gallery_id in remove_ids.split(",") if isinstance(remove_ids, str) else remove_ids:
            bot.delete_message(chat_id=cq.message.chat.id, message_id=int(gallery_id))
        bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id, text=texts.pick_address,
                              reply_markup=build_resources_keyboard(Resource, page=1))
        return True
    return False


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("my_booking:"))
def callback_my_booking(cq: types.CallbackQuery):
    """
        Handles the "my bookings" inline button action.

        Callback data format:
            "my_booking:<remove_ids>"

        Steps:
        1. Extracts the remove_ids part from the callback data.
           These IDs are used to remove temporary images associated with the previous view.
        2. Calls remove_images(remove_ids, cq) to clean up old media files.
        3. Calls all_my_bookings() to refresh the message and display the user's bookings.
           The text of the updated message is provided via texts.my_bookings.
    """
    _, remove_ids = cq.data.split(":", 1)
    remove_images(remove_ids, cq)
    all_my_bookings(cq.message, edit_msg_text=texts.my_bookings)


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("cancel_my_booking:"))
def callback_cancel_my_booking(cq: types.CallbackQuery):
    """
        Handles cancellation of a user's booking via callback query.

        Args:
            cq: Telegram CallbackQuery object triggered by pressing the "cancel booking" button.

        Behavior:
        1. Confirms the callback query to acknowledge the user's action.
        2. Extracts the booking ID and optional gallery message IDs from cq.data.
        3. Deletes any associated gallery messages using remove_images().
        4. Checks if the booking's check-in date has already passed:
             - If yes, does not allow cancellation and refreshes the user's bookings list with an error message.
        5. If the booking is eligible for cancellation:
             - Sets its status to "cancelled" and saves the change to the database.
             - Sends a notification message to the managers' chat with details of the canceled booking,
               including user info, resource, period, and refund amount.
        6. Refreshes the user's bookings list with a confirmation message.

        Notes:
        - Prevents cancellation of bookings that have already started.
        - Automatically cleans up gallery messages to keep the chat tidy.
        - Integrates refund info notification for managers.
        - Uses Markdown formatting for manager notifications.
    """
    bot.answer_callback_query(cq.id)
    _, booking_id, remove_ids = cq.data.split(":", 2)
    booking = Booking.get_by_id(booking_id)
    remove_images(remove_ids, cq)
    if booking.check_in <= date.today():
        return all_my_bookings(cq.message, edit_msg_text=texts.cancel_error)
    booking.status = "cancelled"
    booking.save()
    bot.send_message(
        MANAGERS_CHAT,
        f"❗❗❗{texts.booking_canceled}❗❗❗\n"
        f"{texts.user}: @{cq.from_user.username} ({cq.from_user.id})\n"
        f"{texts.address}: {booking.resource.location}\n"
        f"{texts.period}: {booking.check_in} → {booking.check_out}\n"
        f"{texts.amount}: {booking.amount}\n"
        f"{texts.refund_amount}: {booking.amount * REFUND_PERCENTS}\n",
        parse_mode="Markdown"
    )
    return all_my_bookings(cq.message, edit_msg_text=texts.cancel_apply.format(booking.resource.location))


def clean_expired_booking_states():
    """
    Removes stale entries from `user_booking_state`.

    An entry is considered stale if:
      - 'check_out' is None
      - 'created' timestamp is older than 24 hours from now

    This helps prevent old incomplete bookings from staying in memory.
    """
    now = datetime.now()
    expired_users = []
    global user_booking_state
    for user_id, state in user_booking_state.items():
        check_out = state.get('check_out')
        created = state.get('created')
        if check_out is None and created and (now - created) > timedelta(hours=24):
            expired_users.append(user_id)

    for user_id in expired_users:
        user_booking_state.pop(user_id, None)


def start_cleaner_thread():
    """
        Starts a background thread that cleans expired booking states every hour.
    """

    def cleaner_loop():
        while True:
            try:
                clean_expired_booking_states()
            except Exception as e:
                print(f"Error in cleaner thread: {e}")
            # Wait for 1 hour before next cleanup
            time.sleep(3600)

    thread = threading.Thread(target=cleaner_loop, daemon=True)
    thread.start()


if __name__ == '__main__':
    load_user_booking_state()
    start_cleaner_thread()
    bot.infinity_polling()
