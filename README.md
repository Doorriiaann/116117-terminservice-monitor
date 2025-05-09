# 116117 Terminservice Scraper

This project helps you find medical appointments using a "Dringlichkeitscode" (urgency code) on the [116117 Terminservice](https://www.eterminservice.de/terminservice) website. It automatically checks for available appointments and notifies you via Telegram.

## Features

- Scrapes the 116117 appointment service for available slots.
- Sends Telegram notifications (with screenshot) when appointments are found.
- Can be run periodically (e.g., as a cronjob) for continuous monitoring.

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/TorbenWetter/116117-terminservice-scraper.git
cd 116117-terminservice-scraper
```

### 2. Install dependencies

You can use either [Poetry](https://python-poetry.org) or `pip` with `requirements.txt`:

#### Option A: Poetry (recommended)

```bash
poetry install
```

#### Option B: pip

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment variables

Update the following variables in the `.env` file:

- **BOOKING_URL**: Visit [116117 Terminservice](https://www.eterminservice.de/terminservice) and enter your Vermittlungscode, zip code, and appointment type (e.g., Psychiatrie). Copy the resulting link from your browser and paste it here.
- **TELEGRAM_TOKEN**: Token from your Telegram Bot (create one via [@BotFather](https://t.me/BotFather)).
- **TELEGRAM_CHAT_ID**: Your Telegram chat ID (see [how to get it](https://gist.github.com/nafiesl/4ad622f344cd1dc3bb1ecbe468ff9f8a)).

### 4. Run the script

With Poetry:

```bash
poetry run python main.py
```

With pip/venv:

```bash
python main.py
```

### 5. (Optional) Set up as a cronjob

You can run the script periodically (e.g., every 2 minutes):

```cron
*/2 * * * * /path/to/venv/bin/python /path/to/116117-terminservice-scraper/main.py >> /path/to/116117-terminservice-scraper/cron.log 2>&1
```

In practice, running every 2 minutes has not resulted in being blocked by the site.

## Contributing

Contributions and bug fixes are welcome! Feel free to open issues or pull requests.

**Dependency management:**

- If you add or update dependencies using Poetry, please also run:
  ```bash
  poetry export -f requirements.txt --output requirements.txt
  ```
  This ensures the `requirements.txt` stays in sync for users who install via pip.
