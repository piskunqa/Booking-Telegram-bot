# Telegram Booking Bot with Flask Admin

This repository contains a Telegram bot for booking resources (rooms, apartments, etc.) with a built-in calendar, payment integration via Telegram invoices (Redsys Test), and a Flask admin panel for managing resources. The bot supports multi-user booking, handles overlapping bookings, and allows cancellations and notifications to managers.

---

## Features

- **Resource browsing**: Users can view available resources with photos, location, price, and description.
- **Pagination**: Resource list is paginated (10 items per page) with next/previous buttons.
- **Booking**: Users can select check-in and check-out dates via a calendar UI.
- **Calendar restrictions**: Shows already booked dates; highlights user's own bookings in green, others in red.
- **Payments**: Integrated with Telegram `send_invoice` for payments in local currency.
- **Booking confirmation**: Only confirmed after successful payment.
- **Cancellation**: Users can cancel upcoming bookings; managers are notified.
- **Help system**: Users can send messages to managers; managers can reply via bot.
- **Flask Admin panel**: Manage resources and uploaded images through a web interface.
- **Persistence**: Bookings and uploaded files are saved using MySQL and Docker volumes.
- **Auto-cleanup**: Old incomplete bookings are removed automatically every hour.

---


https://github.com/user-attachments/assets/e91f2ce6-2a6c-4b77-a6e4-3c14365e4490


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
```


## Installation and Setup

**Clone the repository**:
- git clone <repo-url>
- cd <repo-directory>

**Build Docker containers:**
- docker-compose build

**Start containers:**
- docker-compose up -d

This will start three containers:

bot: Telegram bot service

admin: Flask admin panel accessible at http://localhost:8000

mysql: MySQL database

Docker Volumes

./images:/app/images - stores uploaded images persistently.

MySQL data is stored in mysql-data volume to persist database records.

Usage

Bot: Start the bot via Telegram /start. Browse resources, pick dates, and pay.

Admin panel: Access Flask admin at http://localhost:8000 to manage resources and images.

Cancel Booking: Users can cancel upcoming bookings; managers will be notified.

Scripts & Utilities

Auto-cleanup: Removes incomplete bookings older than 24 hours.

Save/load state: Bot maintains user_booking_state between restarts.

Payments: handle_pre_checkout_query ensures no double-booking during payment.

Restart: /restart command saves the bot state and reloads the bot (admin only).

Development

Python dependencies are defined in requirements.txt.

Reload bot code with:

- docker-compose down
- docker-compose up -d --build

Save and restore user booking state via /restart command in Telegram (admin only).
