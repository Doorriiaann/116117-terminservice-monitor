"""
Scrapes the 116117 Terminservice and notifies about available appointments via Telegram.
"""

import asyncio
import os
import logging
import time
from typing import Optional

from dotenv import load_dotenv
from selenium.webdriver import Chrome, ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


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


def _debug_screenshot(driver, name: str) -> None:
    """Save a debug screenshot to help diagnose issues."""
    path = os.path.join(SCRIPT_DIR, f"debug_{name}.png")
    try:
        driver.save_screenshot(path)
        logger.info("Debug screenshot: %s", path)
    except Exception as exc:
        logger.warning("Could not save debug screenshot %s: %s", name, exc)


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
    except Exception as exc:
        logger.error("Failed to send Telegram photo: %s", exc)
        raise


def _accept_cookie_banner(driver) -> None:
    """Try to accept the cookie banner. Tries multiple selectors for resilience."""
    selectors = [
        # 116117-termine.de / eterminservice.de current: button "AUSWAHL BESTÄTIGEN"
        (
            By.XPATH,
            (
                "//button[contains(translate(., 'abcdefghijklmnopqrstuvwxyz',"
                " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'AUSWAHL BESTÄTIGEN')]"
            ),
        ),
        # Legacy eterminservice.de: <a class="cookies-info-close">
        (
            By.XPATH,
            "//a[contains(@class, 'cookies-info-close')]",
        ),
        # Generic fallback: anything clickable with "bestätigen"
        (
            By.XPATH,
            (
                "//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                " 'abcdefghijklmnopqrstuvwxyz'), 'auswahl bestätigen')]"
            ),
        ),
    ]
    for by, selector in selectors:
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((by, selector))
            )
            btn.click()
            logger.info("Accepted cookie banner via: %s", selector[:60])
            time.sleep(1)
            return
        except TimeoutException:
            continue
        except Exception as exc:
            logger.warning("Cookie selector error (%s): %s", selector[:40], exc)
            continue
    logger.info("No cookie banner found (%d selectors tried).", len(selectors))


def _wait_for_page_ready(driver) -> None:
    """Wait for spinners to clear and document to be ready."""
    spinner_selectors = [".loading-icon", ".spinner", ".loading", "[class*='loading']"]
    for sel in spinner_selectors:
        try:
            WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            logger.info("Spinner appeared (%s), waiting...", sel)
            WebDriverWait(driver, 30).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, sel))
            )
            logger.info("Spinner gone (%s).", sel)
        except TimeoutException:
            pass

    WebDriverWait(driver, 10).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def _wait_for_results(driver) -> None:
    """Wait for results or a no-results message to appear on the page."""
    wait = WebDriverWait(driver, 45)
    try:
        wait.until(
            EC.any_of(
                # 116117-termine.de: "Gefundene Termine" heading when results exist
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(), 'Gefundene Termine')]")
                ),
                # 116117-termine.de: "Verfügbare" text in result items
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(), 'Verfügbare')]")
                ),
                # eterminservice.de: result item CSS classes
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".search-results-item, .ets-search-results-item")
                ),
                # Various no-results messages
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        (
                            "//*[contains(text(), 'keine Treffer')"
                            " or contains(text(), 'Keine Termine')"
                            " or contains(text(), 'keine Termine')]"
                        ),
                    )
                ),
            )
        )
        logger.info("Results section detected.")
    except TimeoutException:
        logger.warning("Timeout (45s) waiting for results.")
        _debug_screenshot(driver, "timeout_results")


def _find_appointments(driver) -> bool:
    """Return True if appointments are present on the page."""
    page_text = driver.page_source.lower()

    # Explicit no-results indicators
    no_result_phrases = ["keine treffer", "keine termine", "leider keine"]
    for phrase in no_result_phrases:
        if phrase in page_text:
            logger.info("No-results phrase matched: %r", phrase)
            return False

    # Positive indicators (any match = appointments found)
    positive_selectors = [
        (By.XPATH, "//*[contains(text(), 'Gefundene Termine')]"),
        (By.XPATH, "//*[contains(text(), 'Verfügbare')]"),
        (By.CSS_SELECTOR, ".search-results-item, .ets-search-results-item"),
        (By.CSS_SELECTOR, "[class*='termin'][class*='liste'], [class*='terminliste']"),
    ]
    for by, sel in positive_selectors:
        elems = driver.find_elements(by, sel)
        if elems:
            logger.info(
                "Appointments detected (%d elements): %s", len(elems), sel[:60]
            )
            return True

    logger.info("No appointment indicators found on page.")
    return False


def build_telegram_message(found: bool, booking_url: str) -> str:
    """Build the HTML message for Telegram notification."""
    if found:
        return (
            "<b>🎉 Termine verfügbar!</b>\n"
            f"<a href='{booking_url}'>Jetzt Termin sichern</a>\n"
            "<i>URL enthält bereits den Suchradius.</i>"
        )
    return (
        "<b>Keine Termine verfügbar.</b>\n"
        f"<a href='{booking_url}'>Zur Übersicht</a>"
    )


def check_appointments(url: str = BOOKING_URL) -> bool:
    """Check for available appointments and send Telegram notification if found."""
    driver = None
    try:
        driver = get_webdriver()
        logger.info("Loading: %s", url)
        driver.get(url)

        _wait_for_page_ready(driver)
        _debug_screenshot(driver, "01_page_loaded")

        _accept_cookie_banner(driver)
        _debug_screenshot(driver, "02_after_cookie")

        logger.info("Radius set via URL parameter — skipping UI selection.")

        _wait_for_results(driver)
        _debug_screenshot(driver, "03_results")

        found = _find_appointments(driver)

        # Always save final screenshot
        screenshot_path = os.path.join(SCRIPT_DIR, "screenshot.png")
        try:
            driver.save_screenshot(screenshot_path)
            logger.info("Final screenshot: %s", screenshot_path)
        except Exception as exc:
            logger.error("Failed to save final screenshot: %s", exc)
            screenshot_path = None

        if found and screenshot_path:
            msg = build_telegram_message(True, url)
            try:
                asyncio.run(
                    send_telegram_photo(
                        TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, msg, screenshot_path
                    )
                )
            except Exception as exc:
                logger.error("Telegram send failed: %s", exc)

        return found

    except Exception as exc:
        logger.error("Error during check: %s", exc)
        if driver:
            _debug_screenshot(driver, "error")
        raise
    finally:
        if driver:
            try:
                driver.quit()
                logger.info("WebDriver closed.")
            except Exception as exc:
                logger.error("Error closing WebDriver: %s", exc)


def main() -> None:
    """Main entry point."""
    try:
        found = check_appointments()
        if found:
            logger.info("Appointment found!")
        else:
            logger.info("No appointment found.")
    except Exception as exc:
        logger.critical("Unhandled error in main: %s", exc)


if __name__ == "__main__":
    main()
