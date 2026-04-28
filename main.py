"""
Scrapes the 116117 Terminservice and notifies about available appointments via Telegram.
"""

import asyncio
import html
import os
import logging
import time
from typing import Optional
from dataclasses import dataclass
import hashlib
import json

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


@dataclass(frozen=True)
class Appointment:
    date: str
    time: str
    location: str
    distance_km: str

    def uid(self) -> str:
        """
        Stable identifier for deduplication across runs.
        Uses date+time+location only — distance_km is excluded because it may
        vary slightly between scrapes for the same slot.
        Truncated to 16 hex chars (64 bits), sufficient for O(thousands) slots.
        """
        raw = f"{self.date}|{self.time}|{self.location}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


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
STATE_FILE = os.path.join(SCRIPT_DIR, "seen_appointments.json")


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


async def send_telegram_message(token: str, chat_id: str, message: str) -> None:
    """Send a plain text message to a Telegram chat."""
    bot = Bot(token=token)
    try:
        async with bot:
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
            )
        logger.info("Sent Telegram notification.")
    except Exception as exc:
        logger.error("Failed to send Telegram message: %s", exc)
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


def _scrape_appointments(driver) -> list[Appointment]:
    """
    Scrape structured appointment data from the results page.
    Returns a list of Appointment objects.
    Falls back to sentinel appointments if results are present but
    structured data cannot be parsed (e.g., site markup changed).
    """
    appointments: list[Appointment] = []

    # Each wp2-terminprofil-wrapper is one provider/location block
    wrappers = driver.find_elements(By.CSS_SELECTOR, "wp2-terminprofil-wrapper")
    logger.info("Found %d appointment wrapper(s).", len(wrappers))

    for wrapper in wrappers:
        # --- Extract location name ---
        location = ""
        for sel in [
            ".wp2-terminprofil__name",
            ".wp2-terminprofil-header__name",
            "[class*='name']",
        ]:
            try:
                location = wrapper.find_element(By.CSS_SELECTOR, sel).text.strip()
                if location:
                    break
            except Exception:
                pass

        # --- Extract distance ---
        distance_km = ""
        for sel in [
            ".wp2-terminprofil__entfernung",
            ".wp2-terminprofil-header__entfernung",
            "[class*='entfernung']",
            "[class*='distance']",
        ]:
            try:
                distance_km = wrapper.find_element(By.CSS_SELECTOR, sel).text.strip()
                if distance_km:
                    # Drop "Auf der Karte zeigen" button text that gets scraped alongside the distance
                    distance_km = distance_km.splitlines()[0].strip()
                    break
            except Exception:
                pass

        # --- Extract time chips ---
        chips = wrapper.find_elements(
            By.CSS_SELECTOR, ".wp2-terminprofil-termine__chip"
        )
        for chip in chips:
            chip_text = chip.text.strip()
            lines = [ln.strip() for ln in chip_text.splitlines() if ln.strip()]
            if len(lines) >= 2:
                date_part = lines[0]
                time_part = lines[1]
            elif len(lines) == 1:
                parts = lines[0].split()
                date_part = " ".join(parts[:-1]) if len(parts) > 1 else lines[0]
                time_part = parts[-1] if len(parts) > 1 else ""
            else:
                continue
            appointments.append(
                Appointment(
                    date=date_part,
                    time=time_part,
                    location=location or "Unbekannt",
                    distance_km=distance_km or "?",
                )
            )

    # Fallback: if no wrappers but chips exist, site markup may have changed
    if not wrappers:
        chips_global = driver.find_elements(
            By.CSS_SELECTOR, ".wp2-terminprofil-termine__chip"
        )
        if chips_global:
            logger.warning(
                "Found %d chips outside wrappers — site markup may have changed. "
                "Cannot parse appointment details. Check the site manually.",
                len(chips_global),
            )
        else:
            # Last resort: check count text
            try:
                count_el = driver.find_element(
                    By.XPATH, "//*[contains(text(), 'TERMINE IM UMKREIS')]"
                )
                count_text = count_el.text.strip()
                logger.info("Results header: %r", count_text)
                if not count_text.startswith("0 "):
                    logger.warning(
                        "Results header indicates appointments exist but no "
                        "structured data could be parsed. Check the site manually."
                    )
            except Exception:
                pass

    logger.info("Scraped %d appointment(s).", len(appointments))
    return appointments


def load_seen_appointments() -> set[str]:
    """Load UIDs of previously seen appointments from disk."""
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        logger.warning("State file format unexpected, starting fresh.")
        return set()
    except Exception as exc:
        logger.warning("Could not read state file: %s", exc)
        return set()


def save_seen_appointments(seen: set[str]) -> None:
    """Persist UIDs of seen appointments to disk."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(seen), f, indent=2)
    except Exception as exc:
        logger.error("Could not write state file: %s", exc)


def filter_new_appointments(
    appointments: list[Appointment], seen: set[str]
) -> tuple[list[Appointment], set[str]]:
    """
    Return only appointments whose UID is not in seen, plus the updated seen set.
    The updated seen set includes all current appointments (new and existing).
    """
    new_appointments = [a for a in appointments if a.uid() not in seen]
    updated_seen = seen | {a.uid() for a in appointments}
    return new_appointments, updated_seen


def build_telegram_message(new_appointments: list[Appointment], booking_url: str) -> str:
    """Build an HTML-formatted message listing new appointments."""
    distances = ", ".join(html.escape(a.distance_km) for a in new_appointments)
    lines = [f"<b>Neue Termine: {distances}</b>"]
    lines.append(f'<a href="{booking_url}">Jetzt buchen</a>')
    lines.append("")
    for appt in new_appointments:
        lines.append(
            f"Praxis: {html.escape(appt.location)}\n"
            f"Entfernung: {html.escape(appt.distance_km)}\n"
            f"Datum: {html.escape(appt.date)}"
        )
        lines.append("")
    return "\n".join(lines).strip()


def check_appointments(url: str = BOOKING_URL) -> list[Appointment]:
    """
    Scrape available appointments, filter against seen state, notify for new ones.
    Returns list of newly found appointments.
    """
    driver = None
    try:
        driver = get_webdriver()
        logger.info("Loading: %s", url)
        driver.get(url)

        _wait_for_page_ready(driver)
        _debug_screenshot(driver, "01_page_loaded")

        _accept_cookie_banner(driver)

        _wait_for_results(driver)
        _debug_screenshot(driver, "02_results")

        appointments = _scrape_appointments(driver)

        seen = load_seen_appointments()
        new_appointments, updated_seen = filter_new_appointments(appointments, seen)
        save_seen_appointments(updated_seen)

        if new_appointments:
            msg = build_telegram_message(new_appointments, url)
            try:
                asyncio.run(
                    send_telegram_message(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, msg)
                )
            except Exception as exc:
                logger.error("Telegram send failed: %s", exc)
        else:
            logger.info("No new appointments since last run.")

        return new_appointments

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
        new_appointments = check_appointments()
        if new_appointments:
            logger.info("%d new appointment(s) found.", len(new_appointments))
        else:
            logger.info("No new appointments found.")
    except Exception as exc:
        logger.critical("Unhandled error in main: %s", exc)


if __name__ == "__main__":
    main()
