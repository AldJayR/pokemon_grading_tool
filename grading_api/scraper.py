from playwright.async_api import async_playwright
import asyncio
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
import aiohttp
from ratelimit import limits, sleep_and_retry
import functools
import logging
import time
from typing import List, Dict, Optional, Any
from html import escape
import urllib.parse
from dataclasses import dataclass
from aiohttp import ClientTimeout
import aiofiles
import json
from urllib.parse import urlencode
import numpy as np
import string

# Add near the top of the file with other imports
import re

# Precompiled patterns for performance
PRODUCT_ID_PATTERN = re.compile(r"/product/(\d+)/")
PRODUCT_ID_ALT_PATTERN = re.compile(r"/product/(\d+)/pokemon")
PRICE_PATTERN = re.compile(r"\$([\d,]+\.?\d*)")


# Data classes for type safety and better structure
@dataclass
class CardDetails:
    name: str
    set_name: str
    language: str = "English"
    product_id: Optional[str] = None  # Add product_id


@dataclass
class CardPriceData:
    card_name: str
    set_name: str
    language: str
    rarity: str
    tcgplayer_price: Optional[float] = None
    product_id: Optional[str] = None
    psa_10_price: Optional[float] = None
    price_delta: Optional[str] = None  # Changed to str
    profit_potential: Optional[str] = None  # Changed to str
    last_updated: Optional[datetime] = None

    def calculate_metrics(self):
        """Calculate price_delta and profit_potential as formatted strings"""
        if self.psa_10_price is not None and self.tcgplayer_price is not None:
            delta = self.psa_10_price - self.tcgplayer_price
            self.price_delta = f"{delta:.2f}"

            if self.tcgplayer_price > 0:
                potential = (delta / self.tcgplayer_price) * 100
                self.profit_potential = f"{potential:.2f}"


# Configuration
@dataclass(frozen=True)
class Config:
    ONE_MINUTE: int = 60
    MAX_REQUESTS_TCG: int = 30
    MAX_REQUESTS_EBAY: int = 20
    CACHE_HOURS: int = 24
    MAX_RETRIES: int = 3
    RETRY_DELAYS: tuple = (5, 10, 20)
    TIMEOUT: int = 60
    TIMEOUT_SECONDS: int = 60
    CONCURRENCY: int = 5

    def __post_init__(self):
        assert all(
            [
                self.MAX_REQUESTS_TCG > 0,
                self.MAX_REQUESTS_EBAY > 0,
                self.CACHE_HOURS > 0,
                self.TIMEOUT > 0,
                self.CONCURRENCY > 0,
            ]
        ), "Invalid configuration values"

    WEBSITE_SELECTORS = {
        "TCGPlayer": {
            "card_element": "div.search-result",
            "title": "span.product-card__title",
            "price": "span.product-card__market-price--value",
            "set_name": "div.product-card__set-name__variant",
            "product_link": "a[data-testid^='product-card__image']",
            "wait_selector": ".search-result, .blank-slate",
        }
    }

    RARITY_MAPPING = {
        "English": ["Special Illustration Rare", "Illustration Rare", "Hyper Rare"],
        "Japanese": ["Art Rare", "Super Rare", "Special Art Rare", "Ultra Rare"],
    }


# Configure logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)
playwright_sem = asyncio.Semaphore(Config.CONCURRENCY)


class AsyncRateLimiter:
    def __init__(self, rpm: int, period: int):
        self.semaphore = asyncio.Semaphore(rpm)
        self.period = period

    async def __aenter__(self):
        async with self.semaphore:
            wait_time = self.period / max(1, self.semaphore._value)
            await asyncio.sleep(wait_time)
            return self

    async def __aexit__(self, *args):
        pass


class PriceCache:
    def __init__(self, save_interval: int = 300):  # 5 minutes
        self.cache = {}
        self.filename = "price_cache.json"
        self.save_interval = save_interval
        self._lock = asyncio.Lock()

    async def async_load_cache(self):
        try:
            async with aiofiles.open(self.filename, "r") as f:
                content = await f.read()
                cache_data = json.loads(content)
                self.cache = {
                    k: (v["data"], datetime.fromisoformat(v["timestamp"]))
                    for k, v in cache_data.items()
                }
        except FileNotFoundError:
            self.cache = {}

    async def save_cache(self):
        async with self._lock:
            cache_data = {
                k: {"data": v[0], "timestamp": v[1].isoformat()}
                for k, v in self.cache.items()
            }
            async with aiofiles.open(self.filename, "w") as f:
                await f.write(json.dumps(cache_data))

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:  # Add lock for thread safety on reads
            if key in self.cache:
                data, timestamp = self.cache[key]
                if datetime.now() - timestamp < timedelta(hours=Config.CACHE_HOURS):
                    return data
                del self.cache[key]
        return None

    async def set(self, key: str, value: Any):
        async with self._lock:
            self.cache[key] = (value, datetime.now())


# 4. Add these utility functions for security:


def safe_log(message: str):
    logger.info(escape(message))


def sanitize_url(url: str) -> str:
    return urllib.parse.quote(url, safe=":/?&=")


price_cache = PriceCache(save_interval=5)


def cache_results(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # More efficient key generation using hash
        args_str = ",".join(str(hash(arg)) for arg in args)
        kwargs_str = ",".join(f"{k}={hash(v)}" for k, v in sorted(kwargs.items()))
        key = f"{func.__name__}:{hash(args_str + kwargs_str)}"

        cached_result = await price_cache.get(key)
        if cached_result:
            logger.info(f"Cache hit for {func.__name__}")
            return cached_result

        try:
            result = await func(*args, **kwargs)
            if result:
                await price_cache.set(key, result)
            return result
        except Exception as e:
            logger.error(f"Error in cached function {func.__name__}: {str(e)}")
            return None

    return wrapper


class RequestError(Exception):
    pass


async def fetch_tcgplayer_data(
    card_details: CardDetails, context
) -> List[CardPriceData]:
    """
    Enhanced TCGPlayer data fetching with better error handling and typing
    """
    logger.info(f"Starting fetch_tcgplayer_data for {card_details.name}")
    if card_details.language not in Config.RARITY_MAPPING:
        raise ValueError(f"Unsupported language: {card_details.language}")

    async with playwright_sem:
        page = await context.new_page()
        await page.route("**/*.{png,jpg,jpeg}", lambda route: route.abort())

        all_card_data = []
        for rarity in Config.RARITY_MAPPING[card_details.language]:
            async with AsyncRateLimiter(Config.MAX_REQUESTS_TCG, Config.ONE_MINUTE):
                for attempt in range(Config.MAX_RETRIES):
                    try:
                        url = build_tcgplayer_url(card_details, rarity)
                        logger.info(f"Accessing URL: {url}")

                        await page.goto(url, wait_until="networkidle", timeout=60000)
                        logger.info("Page loaded")

                        if results := await fetch_and_process_page(
                            page, card_details, rarity
                        ):
                            all_card_data.extend(results)
                        break
                    except Exception as e:
                        safe_log(
                            f"Rarity {rarity} attempt {attempt+1} failed: {str(e)}"
                        )
                        if attempt == Config.MAX_RETRIES - 1:
                            safe_log(
                                f"Failed all {Config.MAX_RETRIES} attempts for {rarity}"
                            )
                        await asyncio.sleep(Config.RETRY_DELAYS[attempt])

        await page.close()

    return all_card_data


def build_tcgplayer_url(card: CardDetails, rarity: str) -> str:
    """
    Build a TCGPlayer URL for a given CardDetails and rarity.
    For example (for English):
    https://www.tcgplayer.com/search/pokemon/product?productLineName=pokemon&view=grid&page=1&
    ProductTypeName=Cards&Rarity=Special+Illustration+Rare&q=Pikachu&setName=surging-sparks|sv08-surging-sparks&Condition=Near+Mint
    """
    # Determine base URL and product line based on language.
    if card.language.lower() == "japanese":
        base_url = "https://www.tcgplayer.com/search/pokemon-japan/product"
        product_line = "pokemon-japan"
    else:
        base_url = "https://www.tcgplayer.com/search/pokemon/product"
        product_line = "pokemon"

    # Prepare query parameters.
    params = {
        "productLineName": product_line,
        "view": "grid",
        "page": "1",
        "ProductTypeName": "Cards",
        "Rarity": rarity.replace(" ", "+"),
        "Condition": "Near+Mint",
    }

    if card.name:
        params["q"] = card.name

    # Helper function to normalize set names.
    def normalize_set_name(s: str) -> str:
        return s.strip().lower().replace("&", "and").replace(" ", "-")

    if card.set_name:
        if ":" in card.set_name:
            clean_set = normalize_set_name(card.set_name.split(":", 1)[1])
            original_set = normalize_set_name(card.set_name.replace(":", ""))
        else:
            clean_set = original_set = normalize_set_name(card.set_name)
        params["setName"] = f"{clean_set}|{original_set}"

    # Manually join the parameters (no URL encoding).
    query_string = "&".join(f"{key}={value}" for key, value in params.items())
    return f"{base_url}?{query_string}"


async def fetch_and_process_page(
    page, card_details: CardDetails, rarity: str
) -> List[CardPriceData]:
    """Fetches and processes TCGPlayer pages with pagination support"""
    all_results = []
    current_page = 1
    max_pages = 5  # Limit to prevent infinite loops

    try:
        while current_page <= max_pages:
            logger.info(f"Processing page {current_page} for {card_details.name}")

            # Modify URL for pagination
            if current_page > 1:
                current_url = page.url
                if "page=" in current_url:
                    new_url = re.sub(r"page=\d+", f"page={current_page}", current_url)
                else:
                    new_url = f"{current_url}&page={current_page}"
                await page.goto(new_url, wait_until="networkidle")

            # Wait for results or blank slate
            await page.wait_for_selector(".search-result, .blank-slate", timeout=60000)

            has_results = await page.locator(".search-result").count() > 0
            has_blank = await page.locator(".blank-slate").count() > 0

            if has_blank and current_page == 1:
                logger.info(f"No results found for {card_details.name}")
                return []

            if has_results:
                html = await page.content()
                soup = BeautifulSoup(html, "lxml")
                page_results = process_card_elements(soup, card_details, rarity)
                if page_results:
                    all_results.extend(page_results)

                # Check for next page button
                next_button = await page.query_selector(
                    'button[aria-label="Next Page"]'
                )
                if not next_button or await next_button.is_disabled():
                    logger.info(f"No more pages available after page {current_page}")
                    break

                current_page += 1
                await asyncio.sleep(1)  # Small delay between pages
            else:
                break

        logger.info(
            f"Total cards found across {current_page} pages: {len(all_results)}"
        )
        return all_results

    except Exception as e:
        logger.error(f"Error processing pages for {card_details.name}: {str(e)}")
        return all_results


def process_card_elements(
    soup: BeautifulSoup, card_details: CardDetails, rarity: str
) -> List[CardPriceData]:
    cards = []
    search_results = soup.find_all("div", class_="search-result")
    logger.info(f"Found {len(search_results)} search results")

    for card in search_results:
        try:
            if card_data := extract_card_data(card, card_details, rarity):
                cards.append(card_data)
        except Exception as e:
            logger.error(f"Error processing card element: {str(e)}", exc_info=True)

    logger.info(f"Processed {len(cards)} valid cards")
    return cards


def extract_card_data(
    card_element: BeautifulSoup, card_details: CardDetails, rarity: str
) -> Optional[CardPriceData]:
    """Extracts data from a single card element"""
    try:
        title = card_element.find("span", class_="product-card__title")
        price = card_element.find("span", class_="product-card__market-price--value")
        set_name = card_element.find("div", class_="product-card__set-name__variant")
        product_link = card_element.find(
            "a",
            attrs={"data-testid": lambda x: x and x.startswith("product-card__image")},
        )

        logger.info(
            f"Found elements - Title: {bool(title)}, Price: {bool(price)}, "
            f"Set: {bool(set_name)}, Link: {bool(product_link)}"
        )

        if not all([title, price, set_name, product_link]):
            logger.warning("Missing required elements")
            return None

        logger.info(f"Title: {title.text if title else 'None'}")
        logger.info(f"Price: {price.text if price else 'None'}")
        logger.info(f"Set: {set_name.text if set_name else 'None'}")

        # Improved price validation
        price_text = price.text.strip().replace("$", "").replace(",", "")
        try:
            price_value = float(price_text)
            if price_value <= 0 or price_value > 100000:  # Reasonable bounds check
                logger.warning(f"Invalid price value: {price_value}")
                return None
        except (ValueError, AttributeError):
            return None

        title_text = title.text.strip()
        set_text = set_name.text.strip()

        # Improved card name matching
        if card_details.name:
            search_terms = card_details.name.lower().split()
            title_lower = title_text.lower()
            if not all(term in title_lower for term in search_terms):
                return None

        # Extract and validate product ID
        product_url = product_link.get("href", "")
        product_id = extract_product_id(product_url)
        if not product_id:
            return None

        return CardPriceData(
            card_name=title_text,
            set_name=set_text,
            language=card_details.language,
            rarity=rarity,
            tcgplayer_price=price_value,
            product_id=product_id,
            last_updated=datetime.now(),
        )
    except Exception as e:
        logger.error(f"Error extracting card data: {str(e)}")
        return None


def extract_product_id(url: str) -> Optional[str]:
    """Extracts the product ID from the TCGPlayer product URL using precompiled patterns"""
    if not url:
        return None
    try:
        match = PRODUCT_ID_PATTERN.search(url)
        if not match:
            match = PRODUCT_ID_ALT_PATTERN.search(url)
        return match.group(1) if match else None
    except (AttributeError, IndexError):
        logger.error(f"Failed to extract product ID from URL: {url}")
        return None


@sleep_and_retry
@limits(calls=Config.MAX_REQUESTS_EBAY, period=Config.ONE_MINUTE)
async def get_ebay_psa10_price_async(
    session: aiohttp.ClientSession, card_details: CardDetails
) -> Optional[float]:
    """Fetches PSA 10 prices from eBay with improved error handling and specific card matching"""
    search_query = f"{card_details.name} PSA 10"

    if card_details.language.lower() == "japanese":
        search_query += " Japanese"

    params = {
        "_nkw": search_query,
        "_sacat": 0,
        "_from": "R40",
        "rt": "nc",
        "LH_Sold": 1,
        "LH_Complete": 1,
        "Language": (
            "English" if card_details.language.lower() == "english" else "Japanese"
        ),
    }

    # Manually join parameters without URL encoding.
    query_string = urlencode(params)
    ebay_url = f"https://www.ebay.com/sch/i.html?{query_string}"
    logger.info(f"Accessing eBay URL: {ebay_url}")

    try:
        async with session.get(
            "https://www.ebay.com/sch/i.html",
            params=params,
            timeout=ClientTimeout(total=Config.TIMEOUT_SECONDS),
        ) as response:
            if response.status != 200:
                raise RequestError(f"eBay returned status code {response.status}")

            html = await response.text()
            prices = extract_ebay_prices(html, card_details)

            if not prices:
                logger.warning(f"No valid prices found for {card_details.name}")
                return None

            return calculate_average_price(prices)

    except Exception as e:
        logger.error(
            f"Error fetching eBay data for {card_details.name}: {str(e)}", exc_info=True
        )
        return None


def extract_ebay_prices(html: str, card_details: CardDetails) -> List[float]:
    """Extracts prices from eBay HTML with improved matching for PSA 10 listings."""
    soup = BeautifulSoup(html, "lxml")
    prices = []

    # Split the card name into tokens and remove hyphen-only tokens.
    tokens = [part for part in card_details.name.lower().split() if part != "-"]
    if not tokens:
        return prices

    # Extract card number if present.
    card_number = next((t for t in tokens if re.match(r"\d+/\d+", t)), None)
    # Build a card title by filtering out the card number.
    card_title = " ".join(t for t in tokens if t != card_number)

    # Optionally, continue to use rarity keywords based on the tokens.
    rarity_keywords = {
        word
        for word in tokens
        if word in {"illustration", "special", "hyper", "rare", "art", "super", "ultra"}
    }

    for li in soup.find_all("li", class_="s-item s-item__pl-on-bottom"):
        title_div = li.find("div", class_="s-item__title")
        if not title_div:
            continue

        title_text = title_div.get_text(strip=True).lower()
        logger.debug(f"Processing title: {title_text}")

        # Require that the listing has "psa 10".
        if "psa 10" not in title_text:
            continue
        # Use the entire card title (e.g. "counter gain") rather than only the first token.
        if card_title not in title_text:
            continue
        # If a card number is available, check that it is in the title.
        if card_number and card_number not in title_text:
            continue
        # Reject "japanese" keyword if our language isn’t Japanese.
        if card_details.language.lower() != "japanese" and "japanese" in title_text:
            continue
        # Optionally, check for rarity keywords if desired.
        if rarity_keywords and not any(
            keyword in title_text for keyword in rarity_keywords
        ):
            continue

        price_span = li.find("span", class_="s-item__price")
        if not price_span:
            continue

        price_str = price_span.get_text(strip=True)
        match = re.search(r"\$([\d,]+\.?\d*)", price_str)
        if match:
            try:
                price = float(match.group(1).replace(",", ""))
                prices.append(price)
                logger.debug(f"Matched price: {price} from '{price_str}'")
            except ValueError:
                logger.warning(f"Failed to convert price: {match.group(1)}")
                continue

    return prices


def calculate_average_price(prices: List[float]) -> Optional[float]:
    """Calculate average price using vectorized operations for speed"""
    if not prices:
        return None

    if len(prices) < 3:
        return sum(prices) / len(prices)

    # Use faster numpy operations with explicit dtype
    prices_array = np.array(prices, dtype=np.float32)
    q1, q3 = np.percentile(prices_array, [25, 75])  # Get both in one call
    iqr = q3 - q1

    # Vectorized filtering is much faster than list comprehension
    mask = (prices_array >= q1 - 1.5 * iqr) & (prices_array <= q3 + 1.5 * iqr)
    trimmed = prices_array[mask] if mask.any() else prices_array

    return float(trimmed.mean())


async def process_card_batch(
    card_details_list: List[CardDetails],
    browser_context,
    session: aiohttp.ClientSession,
) -> List[CardPriceData]:
    try:
        tcg_tasks = [
            fetch_tcgplayer_data(card_details, browser_context)
            for card_details in card_details_list
        ]
        tcg_results = await asyncio.gather(*tcg_tasks, return_exceptions=True)

        all_price_data = []
        for card_details, tcg_result in zip(card_details_list, tcg_results):
            for card_data in tcg_result:
                try:
                    ebay_price = await get_ebay_psa10_price_async(
                        session,
                        CardDetails(
                            name=card_data.card_name,
                            set_name=card_data.set_name,
                            language=card_data.language,
                            product_id=card_data.product_id,
                        ),
                    )

                    if ebay_price:
                        card_data.psa_10_price = ebay_price
                        card_data.calculate_metrics()  # Use new method to calculate formatted strings
                    all_price_data.append(card_data)
                except Exception as e:
                    logger.error(
                        f"Error processing eBay data for {card_data.card_name}: {str(e)}"
                    )
                    continue

        return all_price_data
    finally:
        pass


async def main(card_details_list: List[CardDetails]) -> List[CardPriceData]:
    await price_cache.async_load_cache()

    try:
        async with async_playwright() as p:
            # Configure browser with proper cleanup
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-gpu",
                    "--single-process",
                    "--no-zygote",
                    "--no-sandbox",
                ],
            )

            # Create persistent context with proper configuration
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )

            try:
                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(
                        limit=Config.CONCURRENCY, ttl_dns_cache=300, force_close=True
                    ),
                    timeout=ClientTimeout(
                        total=Config.TIMEOUT * 2,
                        sock_connect=Config.TIMEOUT,
                        sock_read=Config.TIMEOUT,
                    ),
                    trust_env=True,
                ) as session:
                    results = await process_card_batch(
                        card_details_list, context, session
                    )
                    return results
            finally:
                # Proper cleanup sequence
                await context.close()
                await browser.close()

    except Exception as e:
        safe_log(f"Critical error in main: {str(e)}")
        raise
    finally:
        await price_cache.save_cache()


if __name__ == "__main__":
    cards_to_fetch = [
        CardDetails(
            name="Charizard ex", set_name="Obsidian Flames", language="English"
        ),
        CardDetails(name="Mew ex", set_name="Pokemon Card 151", language="Japanese"),
    ]

    results = asyncio.run(main(cards_to_fetch))

    # Print results in a formatted waya
    for card in results:
        print("\nCard Details:")
        print(f"Name: {card.card_name}")
        print(f"Set: {card.set_name}")
        print(f"Rarity: {card.rarity}")
        print(f"TCGPlayer Price: ${card.tcgplayer_price:.2f}")
        print(f"TCGPlayer Product ID: {card.product_id}")
        print(f"PSA 10 Price: ${card.psa_10_price:.2f}")
        print(f"Potential Profit: {card.profit_potential:.1f}%")
