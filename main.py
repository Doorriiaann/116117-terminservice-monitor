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
DEBUG_SAVE_IMAGES: bool = os.getenv("DEBUG_SAVE_IMAGES", "").lower() in ("1", "true", "yes")

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
    """Save a debug screenshot. Only runs when DEBUG_SAVE_IMAGES=true."""
    if not DEBUG_SAVE_IMAGES:
        return
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
    """Try to accept the cookie banner if present."""
    selectors = [
        # Current button style: "AUSWAHL BESTÄTIGEN"
        (
            By.XPATH,
            "//button[contains(translate(., 'abcdefghijklmnopqrstuvwxyz',"
            " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'AUSWAHL BESTÄTIGEN')]",
        ),
        # Legacy eterminservice.de
        (By.XPATH, "//a[contains(@class, 'cookies-info-close')]"),
        # Generic fallback
        (
            By.XPATH,
            "//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            " 'abcdefghijklmnopqrstuvwxyz'), 'auswahl bestätigen')]",
        ),
    ]
    for by, selector in selectors:
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((by, selector))
            )
            btn.click()
            logger.info("Accepted cookie banner.")
            time.sleep(1)
            return
        except TimeoutException:
            continue
        except Exception as exc:
            logger.warning("Cookie selector error: %s", exc)
            continue
    logger.info("No cookie banner found.")


def _wait_for_page_ready(driver) -> None:
    """Wait for spinners to clear and document ready."""
    for sel in [".loading-icon", ".spinner", "[class*='loading']"]:
        try:
            WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            WebDriverWait(driver, 30).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, sel))
            )
        except TimeoutException:
            pass
    WebDriverWait(driver, 10).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def _wait_for_results(driver) -> None:
    """Wait for the 116117-termine.de results page to render."""
    wait = WebDriverWait(driver, 45)
    try:
        wait.until(
            EC.any_of(
                # 116117-termine.de: Angular appointment wrapper component
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "wp2-terminprofil-wrapper")
                ),
                # 116117-termine.de: results count "X TERMINE IM UMKREIS"
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(), 'TERMINE IM UMKREIS')]")
                ),
                # 116117-termine.de: "Suchergebnisse" heading
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(), 'Suchergebnisse')]")
                ),
                # 116117-termine.de: bookable time slot chips
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".wp2-terminprofil-termine__chip")
                ),
                # 116117-terme.de: no results — "Umkreis erweitern" without any termine
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(), 'Umkreis erweitern')]")
                ),
                # 116117-terme.de: 0 results
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(), '0 TERMINE')]")
                ),
            )
        )
        logger.info("Results page detected.")
    except TimeoutException:
        logger.warning("Timeout (45s) waiting for results page.")
        _debug_screenshot(driver, "timeout_results")


def _find_appointments(driver) -> bool:
    """Return True if appointments are present on the page."""
    # Check for bookable time slot chips (the actual clickable appointment links)
    chips = driver.find_elements(
        By.CSS_SELECTOR, ".wp2-terminprofil-termine__chip"
    )
    if chips:
        logger.info("Found %d bookable appointment slot(s).", len(chips))
        return True

    # Check for appointment wrapper components
    wrappers = driver.find_elements(
        By.CSS_SELECTOR, "wp2-terminprofil-wrapper"
    )
    if wrappers:
        logger.info("Found %d appointment group(s).", len(wrappers))
        return True

    # Check the results count text: "X TERMINE IM UMKREIS VON Y KM"
    try:
        count_el = driver.find_element(
            By.XPATH, "//*[contains(text(), 'TERMINE IM UMKREIS')]"
        )
        count_text = count_el.text.strip()
        logger.info("Results header: %r", count_text)
        # "0 TERMINE" means nothing found
        if count_text.startswith("0 "):
            return False
        return True
    except Exception:
        pass

    # Fallback: check page source for Nächster freier Termin
    if "nächster freier termin" in driver.page_source.lower():
        logger.info("Found 'Nächster freier Termin' in page source.")
        return True

    logger.info("No appointment indicators found.")
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

        logger.info("Radius set via URL parameter.")

        _wait_for_results(driver)
        _debug_screenshot(driver, "02_results")

        found = _find_appointments(driver)

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
