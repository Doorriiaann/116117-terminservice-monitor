# 116117 Terminservice Monitor

Monitors the [116117 Terminservice](https://www.116117-termine.de) for available medical appointments using a Dringlichkeitscode (urgency code) and sends a Telegram notification when new slots appear.

## Features

- Scrapes the 116117 appointment service for available slots
- Sends structured Telegram notifications only for newly detected appointments
- Deduplicates across runs — no repeated alerts for the same slot
- Designed to run as a cronjob

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/Doorriiaann/116117-terminservice-scraper.git
cd 116117-terminservice-scraper
```

### 2. Install dependencies

Create a virtualenv and install dependencies directly (the locked `requirements.txt` has hash issues with some pip versions, so install the packages by name):

```bash
python3 -m venv .venv
.venv/bin/pip install selenium python-telegram-bot python-dotenv
```

### 3. Configure environment variables

Edit `.env` in the project directory:

```env
BOOKING_URL=https://www.116117-termine.de/terminservice/suche/XXXX-XXXX-XXXX/12345/W001?suchradius=100
TELEGRAM_TOKEN=0123456789:your-bot-token
TELEGRAM_CHAT_ID=0123456789
```

| Variable | How to get it |
|---|---|
| `BOOKING_URL` | Go to [116117-termine.de](https://www.116117-termine.de), enter your Vermittlungscode, zip code, and appointment type. Copy the URL from your browser. Append `?suchradius=100` (or your preferred radius in km). |
| `TELEGRAM_TOKEN` | Create a bot via [@BotFather](https://t.me/BotFather) and copy the token. |
| `TELEGRAM_CHAT_ID` | Send a message to your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat ID. |

### 4. Run once to verify

```bash
.venv/bin/python3 main.py
```

On the first run, `seen_appointments.json` is created to track known slots. Subsequent runs only notify for new ones.

### 5. Set up as a cronjob

```bash
crontab -e
```

Add this line (runs every 2 minutes, adjust the path if your project is elsewhere):

```cron
*/2 * * * * cd /root/116117-terminservice-scraper && /root/116117-terminservice-scraper/.venv/bin/python3 main.py >> /root/116117-terminservice-scraper/cron.log 2>&1
```

Verify it was saved:

```bash
crontab -l
```

## Optional: Debug screenshots

By default no screenshots are saved. To enable them for troubleshooting:

```bash
DEBUG_SAVE_IMAGES=true python3 main.py
```

This saves `debug_01_page_loaded.png`, `debug_02_results.png`, and `debug_error.png` (on failure) next to the script.

## Telegram message format

When new appointments are detected you receive a message like:

```
Neue Termine verfuegbar (2)
Jetzt buchen

Datum: Mo, 05.05.2026
Zeit: 09:30
Praxis: Psychiatrische Praxis Musterstadt
Entfernung: 3,2 km

Datum: Di, 06.05.2026
Zeit: 14:00
Praxis: Nervenärzte am Markt
Entfernung: 7,8 km
```

No notification is sent if no new slots were found since the last run.

## Contributing

Bug reports and pull requests are welcome.
