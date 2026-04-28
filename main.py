"""
Scrapes the 116117 Terminservice and notifies about available appointments via Telegram.
"""

import asyncio
import os
import logging
import sys
from typing import Optional

from dotenv import load_dotenv
from selenium.webdriver import Chrome, ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)
from telegram import Bot
from telegram.constants import ParseMode


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

load_dotenv()

BOOKING_URL: Optional[str] = os.getenv("BOOKING_URL")
TELEGRAM_TOKEN: Optional[str] = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")

if not BOOKING_URL or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    ERROR_MSG = (
        "BOOKING_URL, TELEGRAM_TOKEN and TELEGRAM_CHAT_ID must be set in the .env file."
    )
    logger.critical(ERROR_MSG)
    raise RuntimeError(ERROR_MSG)


def get_webdriver() -> Chrome:
    """Create and return a configured Chrome WebDriver in headless mode."""
    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,2000")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    try:
        driver = Chrome(options=options)
        return driver
    except WebDriverException as e:
        logger.critical("Failed to initialize Chrome WebDriver: %s", e)
        raise


async def send_telegram_photo(
    token: str, chat_id: str, message: str, photo_path: str
) -> None:
    """Send a photo with a message to a Telegram chat."""
    bot = Bot(token=token)
    try:
        async with bot:
            with open(photo_path, "rb") as photo_file:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=message,
                    parse_mode=ParseMode.HTML,
                )
        logger.info("Sent Telegram notification with screenshot.")
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to send Telegram photo: %s", exc)
        raise


def _wait_for_spinner(driver) -> None:
    """Wait for the loading spinner to disappear."""
    try:
        WebDriverWait(driver, 30).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, ".loading-icon"))
        )
    except TimeoutException:
        logger.warning("Timeout waiting for spinner to disappear.")


def _accept_cookie_banner(driver) -> None:
    """Try to accept the cookie banner if present."""
    try:
        cookie_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    (
                        "//a[contains(@class, 'cookies-info-close') "
                        "and contains(., 'Auswahl bestätigen')]"
                    ),
                )
            )
        )
        cookie_btn.click()
        logger.info("Accepted cookie banner.")
    except TimeoutException:
        logger.info("No cookie banner present.")
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error accepting cookie banner: %s", exc)


def _wait_for_results(driver) -> None:
    """Wait for either an appointment result or a 'no results' message."""
    wait = WebDriverWait(driver, 40)
    try:
        wait.until(
            EC.any_of(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        (
                            "//*[contains(text(), 'Ihre Suche ergab leider keine Treffer')]"
                        ),
                    )
                ),
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        ".search-results-item.ets-search-results-item",
                    )
                ),
            )
        )
    except TimeoutException:
        logger.warning(
            "Timeout waiting for appointment results or 'no results' message."
        )


def _find_appointments(driver) -> bool:
    """Return True if appointments found, False otherwise."""
    try:
        no_results = driver.find_elements(
            By.XPATH, "//*[contains(text(), 'Ihre Suche ergab leider keine Treffer')]"
        )
        appointments = driver.find_elements(
            By.CSS_SELECTOR, ".search-results-item.ets-search-results-item"
        )
        found = not no_results and bool(appointments)
        logger.info("Appointments found: %s", found)
        return found
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error finding appointments: %s", exc)
        raise


def build_telegram_message(found: bool, booking_url: str) -> str:
    """Build the HTML message for Telegram notification."""
    if found:
        return (
            "<b>🎉 Termine verfügbar!</b>\n"
            f"<a href='{booking_url}'>Jetzt Termin sichern</a>\n"
            "<i>URL enthält bereits den richtigen Suchradius.</i>"
        )
    return (
        "<b>Keine Termine verfügbar.</b>\n"
        f"<a href='{booking_url}'>Zur Übersicht</a>"
    )


def check_appointments(url: str = BOOKING_URL) -> bool:
    """Checks for available appointments and sends a Telegram notification with a screenshot."""
    driver = None
    try:
        driver = get_webdriver()
        logger.info("Loading URL: %s", url)
        driver.get(url)
        _wait_for_spinner(driver)
        _accept_cookie_banner(driver)

        # Radius is set via the ?suchradius= URL parameter — no UI interaction needed
        logger.info("Using radius from URL (no manual selection needed)")

        _wait_for_results(driver)

        found = _find_appointments(driver)
        if found:
            msg = build_telegram_message(found, url)
            current_dir = os.path.dirname(os.path.abspath(__file__))
            screenshot_path = os.path.join(current_dir, "screenshot.png")
            try:
                driver.save_screenshot(screenshot_path)
                logger.info("Screenshot saved to %s", screenshot_path)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Failed to save screenshot: %s", exc)
                screenshot_path = None
            if screenshot_path:
                try:
                    asyncio.run(
                        send_telegram_photo(
                            TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, msg, screenshot_path
                        )
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.error("Failed to send Telegram notification: %s", exc)
        return found
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error during appointment check: %s", exc)
        raise
    finally:
        if driver:
            try:
                driver.quit()
                logger.info("WebDriver closed.")
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Error closing WebDriver: %s", exc)


def main() -> None:
    """Main entry point for the script."""
    try:
        found = check_appointments()
        if found:
            logger.info("Appointment found!")
        else:
            logger.info("No appointment found.")
    except Exception as exc:  # pylint: disable=broad-except
        logger.critical("Unhandled error in main: %s", exc)


if __name__ == "__main__":
    main()
