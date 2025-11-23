# Telegram Booking Bot with Flask Admin

This repository contains a Telegram bot for booking resources (rooms, apartments, etc.) with a built-in calendar, payment integration via Telegram invoices (Redsys Test), and a Flask admin panel for managing resources. The bot supports multi-user booking, handles overlapping bookings, and allows cancellations and notifications to managers.

---

## Features

- **Resource browsing**: Users can view available resources with photos, location, price, and description.
- **Pagination**: Resource list is paginated (10 items per page) with next/previous buttons.
- **Booking**: Users can select check-in and check-out dates via a calendar UI.
- **Calendar restrictions**: Shows already booked dates; highlights user's own bookings in green, others in red.
- **Payments**: Integrated with Telegram `send_invoice` for payments in local currency (UAH).
- **Booking confirmation**: Only confirmed after successful payment.
- **Cancellation**: Users can cancel upcoming bookings; managers are notified.
- **Help system**: Users can send messages to managers; managers can reply via bot.
- **Flask Admin panel**: Manage resources and uploaded images through a web interface.
- **Persistence**: Bookings and uploaded files are saved using MySQL and Docker volumes.
- **Auto-cleanup**: Old incomplete bookings are removed automatically every hour.

---

## Requirements

- Docker & Docker Compose
- Python 3.10+
- MySQL (Dockerized)
- Telegram Bot Token

---

## Environment Variables

Create a `.env` file in the project root with the following variables:

```dotenv
BOT_TOKEN=your_telegram_bot_token # create in botfather
CURRENCY=UAH
PROVIDER_TOKEN=your_payment_provider_token # take in botfather bot settings
MANAGERS_CHAT=telegram_chat_id_for_managers
ADMIN_IDS=123456,789012  # comma-separated list of Telegram IDs
REFUND_PERCENTS=0.8
RECORDS_ROWS=2
RECORD_PER_PAGE=6
LANGUAGE=ru # The language of the texts in the bot can be added to language.json
MYSQL_HOST=mysql
MYSQL_DATABASE=booking
MYSQL_USER=root
MYSQL_PASSWORD=secret
MYSQL_ROOT_PASSWORD=secret2
MYSQL_PORT=3306
