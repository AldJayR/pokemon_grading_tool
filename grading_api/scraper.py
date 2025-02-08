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

class BrowserInitError(Exception):
    """Custom exception for browser initialization errors"""
    pass

from html import escape
import urllib.parse
from dataclasses import dataclass
from aiohttp import ClientTimeout
import aiofiles
import json

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
    price_delta: Optional[float] = None
    profit_potential: Optional[float] = None
    last_updated: Optional[datetime] = None

# Configuration
@dataclass(frozen=True)
class Config:
    # Rate limiting settings
    ONE_MINUTE: int = 60
    MAX_REQUESTS_TCG: int = 30
    MAX_REQUESTS_EBAY: int = 20
    
    # Cache settings
    CACHE_HOURS: int = 24
    CACHE_FILE: str = "price_cache.json"
    CACHE_SAVE_INTERVAL: int = 300  # 5 minutes
    
    # Retry settings
    MAX_RETRIES: int = 3
    RETRY_DELAYS: tuple = (5, 10, 20)
    
    # Timeout settings
    TIMEOUT: int = 60
    TIMEOUT_SECONDS: int = 60
    PAGE_TIMEOUT: int = 30000  # 30 seconds for page operations
    NETWORK_TIMEOUT: int = 30000  # 30 seconds for network requests
    
    # Concurrency settings
    CONCURRENCY: int = 5
    MAX_PARALLEL_REQUESTS: int = 10
    
    # Price validation
    MIN_PRICE: float = 0.01
    MAX_PRICE: float = 100000.0
    
    def __post_init__(self):
        """Validate configuration values"""
        validations = {
            'MAX_REQUESTS_TCG': self.MAX_REQUESTS_TCG > 0,
            'MAX_REQUESTS_EBAY': self.MAX_REQUESTS_EBAY > 0,
            'CACHE_HOURS': self.CACHE_HOURS > 0,
            'TIMEOUT': self.TIMEOUT > 0,
            'CONCURRENCY': self.CONCURRENCY > 0,
            'MIN_PRICE': self.MIN_PRICE >= 0,
            'MAX_PRICE': self.MAX_PRICE > self.MIN_PRICE
        }
        
        failed = [key for key, valid in validations.items() if not valid]
        if failed:
            raise ValueError(f"Invalid configuration values for: {', '.join(failed)}")

    WEBSITE_SELECTORS = {
        "TCGPlayer": {
            "card_element": "div.search-result",
            "title": "span.product-card__title",
            "price": "span.product-card__market-price--value",
            "set_name": "div.product-card__set-name__variant",
            "product_link": "a[data-testid^='product-card__image']",
            "wait_selector": ".search-result, .blank-slate"
        }
    }

    RARITY_MAPPING = {
        "English": [
            "Special Illustration Rare",
            "Illustration Rare",
            "Hyper Rare"
        ],
        "Japanese": [
            "Art Rare",
            "Super Rare",
            "Special Art Rare",
            "Ultra Rare"
        ]
    }

# Configure logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
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
    def __init__(self, filename: str = Config.CACHE_FILE, save_interval: int = Config.CACHE_SAVE_INTERVAL):
        self.cache = {}
        self.filename = filename
        self.save_interval = save_interval
        self._lock = asyncio.Lock()
        self._last_save = time.time()
        self._dirty = False

    async def async_load_cache(self):
        try:
            async with aiofiles.open(self.filename, 'r') as f:
                content = await f.read()
                cache_data = json.loads(content)
                self.cache = {
                    k: (v['data'], datetime.fromisoformat(v['timestamp']))
                    for k, v in cache_data.items()
                }
        except FileNotFoundError:
            self.cache = {}

    async def save_cache(self):
        async with self._lock:
            cache_data = {
                k: {
                    'data': v[0],
                    'timestamp': v[1].isoformat()
                }
                for k, v in self.cache.items()
            }
            async with aiofiles.open(self.filename, 'w') as f:
                await f.write(json.dumps(cache_data))

    async def maybe_save_cache(self):
        """Save cache if dirty and enough time has passed"""
        if self._dirty and time.time() - self._last_save >= self.save_interval:
            await self.save_cache()
            self._dirty = False
            self._last_save = time.time()

    async def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < timedelta(hours=Config.CACHE_HOURS):
                return data
            del self.cache[key]
        return None

    async def set(self, key: str, value: Any):
        async with self._lock:
            self.cache[key] = (value, datetime.now())
            self._dirty = True

# 4. Add these utility functions for security:

def safe_log(message: str):
    logger.info(escape(message))
    
def sanitize_url(url: str) -> str:
    return urllib.parse.quote(url, safe=':/?&=')

price_cache = PriceCache(save_interval=5)

def cache_results(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
        cached_result = await price_cache.get(key)

        if cached_result:
            logger.info(f"Cache hit for {key}")
            return cached_result

        try:
            result = await func(*args, **kwargs)
            if result:
                await price_cache.set(key, result)  
            return result
        except Exception as e:
            logger.error(f"Error in cached function {func.__name__}: {e}")
            return None

    return wrapper

class RequestError(Exception):
    pass

class AsyncBrowserContext:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser = None
        self.context = None

    async def __aenter__(self):
        try:
            async with async_playwright() as p:
                self.browser = await p.chromium.launch(
                    headless=self.headless,
                    args=['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage']
                )
                self.context = await self.browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
                return self.context
        except Exception as e:
            await self.cleanup()
            raise BrowserInitError(f"Failed to initialize browser: {str(e)}")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()

    async def cleanup(self):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()

async def fetch_tcgplayer_data(card_details: CardDetails, context) -> List[CardPriceData]:
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
                        
                        if results := await fetch_and_process_page(page, card_details, rarity):
                            all_card_data.extend(results)
                        break
                    except Exception as e:
                        safe_log(f"Rarity {rarity} attempt {attempt+1} failed: {str(e)}")
                        if attempt == Config.MAX_RETRIES - 1:
                            safe_log(f"Failed all {Config.MAX_RETRIES} attempts for {rarity}")
                        await asyncio.sleep(Config.RETRY_DELAYS[attempt])

        await page.close()

    return all_card_data

def build_tcgplayer_url(card_details: CardDetails, rarity: str) -> str:
    """Constructs the TCGPlayer URL based on card details"""
    base = "https://www.tcgplayer.com/search/pokemon"
    if card_details.language == "Japanese":
        base += "-japan"

    params = {
        "productLineName": "pokemon" if card_details.language == "English" else "pokemon-japan",
        "view": "grid",
        "page": "1",
        "ProductTypeName": "Cards",
        "Rarity": rarity.replace(" ", "+")
    }

    if card_details.name:
        params["q"] = card_details.name.replace(" ", "+")

    # Add set name if provided
    if card_details.set_name:
        set_name = card_details.set_name
        if ": " in set_name:
            set_name = set_name.split(": ")[1]
        params["setName"] = set_name.replace(" ", "-").lower()

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}/product?{query_string}"

async def fetch_and_process_page(page, card_details: CardDetails, rarity: str) -> List[CardPriceData]:
    """Fetches and processes a single TCGPlayer page"""
    try:
        logger.info(f"Waiting for search results or blank slate for {card_details.name}")
        
        # Wait for either search results or blank slate
        await page.wait_for_selector(".search-result, .blank-slate", timeout=30000)
        
        # Check which selector is present
        has_results = await page.locator(".search-result").count() > 0
        has_blank = await page.locator(".blank-slate").count() > 0
        
        if has_blank:
            logger.info(f"No results found for {card_details.name} with rarity {rarity}")
            return []
            
        if has_results:
            logger.info(f"Found search results for {card_details.name} with rarity {rarity}")
            html = await page.content()
            logger.info(f"HTML content length: {len(html)}")
            
            soup = BeautifulSoup(html, 'lxml')
            results = process_card_elements(soup, card_details, rarity)
            
            if results:
                logger.info(f"Successfully processed {len(results)} cards for {card_details.name}")
            else:
                logger.warning(f"No valid cards found after processing for {card_details.name}")
            
            return results
        
        logger.warning(f"Neither search results nor blank slate found for {card_details.name}")
        return []

    except Exception as e:
        logger.error(f"Error processing page for {card_details.name}: {str(e)}")
        return []

def process_card_elements(soup: BeautifulSoup, card_details: CardDetails, rarity: str) -> List[CardPriceData]:
    cards = []
    search_results = soup.find_all('div', class_='search-result')
    logger.info(f"Found {len(search_results)} search results")
    
    for card in search_results:
        try:
            if card_data := extract_card_data(card, card_details, rarity):
                cards.append(card_data)
        except Exception as e:
            logger.error(f"Error processing card element: {str(e)}", exc_info=True)
    
    logger.info(f"Processed {len(cards)} valid cards")
    return cards

def extract_card_data(card_element: BeautifulSoup, card_details: CardDetails, rarity: str) -> Optional[CardPriceData]:
    """Extracts data from a single card element"""
    try:
        title = card_element.find('span', class_='product-card__title')
        price = card_element.find('span', class_='product-card__market-price--value')
        set_name = card_element.find('div', class_='product-card__set-name__variant')
        product_link = card_element.find('a', attrs={'data-testid': lambda x: x and x.startswith('product-card__image')})

        logger.info(f"Found elements - Title: {bool(title)}, Price: {bool(price)}, "
                   f"Set: {bool(set_name)}, Link: {bool(product_link)}")

        if not all([title, price, set_name, product_link]):
            logger.warning("Missing required elements")
            return None
        
        logger.info(f"Title: {title.text if title else 'None'}")
        logger.info(f"Price: {price.text if price else 'None'}")
        logger.info(f"Set: {set_name.text if set_name else 'None'}")

        # Improved price validation
        price_text = price.text.strip().replace('$', '').replace(',', '')
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
        product_url = product_link.get('href', '')
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
            last_updated=datetime.now()
        )
    except Exception as e:
        logger.error(f"Error extracting card data: {str(e)}")
        return None

def extract_product_id(url: str) -> Optional[str]:
    """Extracts the product ID from the TCGPlayer product URL"""
    if not url:
        return None
    try:
        # Updated pattern to match the new URL format
        match = re.search(r'/product/(\d+)/', url)
        if not match:
            # Alternative pattern for the new format
            match = re.search(r'/product/(\d+)/pokemon', url)
        return match.group(1) if match else None
    except (AttributeError, IndexError):
        logger.error(f"Failed to extract product ID from URL: {url}")
        return None

@sleep_and_retry
@limits(calls=Config.MAX_REQUESTS_EBAY, period=Config.ONE_MINUTE)
async def get_ebay_psa10_price_async(session: aiohttp.ClientSession, card_details: CardDetails) -> Optional[float]:
    """Fetches PSA 10 prices from eBay with improved error handling and specific card matching"""
    search_query = f"{card_details.name} {card_details.set_name} PSA 10"
    if card_details.language == "Japanese":
        search_query += " Japanese"

    params = {
        "_nkw": search_query,
        "_sacat": 0,
        "_from": "R40",
        "rt": "nc",
        "LH_Sold": 1,
        "LH_Complete": 1
    }

    try:
        async with session.get(
            "https://www.ebay.com/sch/i.html",
            params=params,
            timeout=ClientTimeout(total=Config.TIMEOUT_SECONDS)
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
        logger.error(f"Error fetching eBay data for {card_details.name}: {str(e)}", exc_info=True)
        return None

def extract_ebay_prices(html: str, card_details: CardDetails) -> List[float]:
    """Extracts prices from eBay HTML with improved card matching"""
    soup = BeautifulSoup(html, "lxml")
    prices = []

    # Extract key components from the search query
    search_parts = card_details.name.lower().split()
    card_name = search_parts[0]  # Base card name (e.g., "pikachu")

    # Look for card number pattern (e.g., "123/456")
    card_number = next((part for part in search_parts if re.match(r'\d+/\d+', part)), None)

    # Extract rarity keywords
    rarity_keywords = [word.lower() for word in search_parts if word.lower() in
                      ['illustration', 'special', 'hyper', 'rare', 'art', 'super', 'ultra']]

    for item in soup.find_all("li", class_="s-item s-item__pl-on-bottom"):
        title = item.find("div", class_="s-item__title")
        if not title:
            continue

        title_text = title.text.strip().lower()

        # Must have the base card name and PSA 10
        if card_name not in title_text or "psa 10" not in title_text:
            continue

        # If we have a card number, it should be present
        if card_number and card_number not in title_text:
            continue

        # Check for at least one rarity keyword match if rarity keywords exist
        if rarity_keywords and not any(keyword in title_text for keyword in rarity_keywords):
            continue

        # Skip if it's not a PSA 10
        if "psa 10" not in title_text:
            continue

        if price_span := item.find("span", class_="s-item__price"):
            if price_match := re.search(r'\$([\d,]+\.?\d*)', price_span.text.strip()):
                try:
                    price = float(price_match.group(1).replace(',', ''))
                    prices.append(price)
                except ValueError:
                    continue

    return prices

def calculate_average_price(prices: List[float]) -> Optional[float]:  # Changed return type to Optional[float]
    """Calculates average price with optional outlier removal"""
    if not prices:
        logger.warning("No prices available to calculate average.")
        return None
    
    if len(prices) < 3:
        return sum(prices) / len(prices)
        
    prices.sort()
    q1_idx = max(0, len(prices) // 4)
    q3_idx = min(len(prices) - 1, 3 * len(prices) // 4)
    
    q1 = prices[q1_idx]
    q3 = prices[q3_idx]
    iqr = q3 - q1
    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr
    
    filtered_prices = [p for p in prices if lower_bound <= p <= upper_bound]
    if not filtered_prices:
        return sum(prices) / len(prices)
    return sum(filtered_prices) / len(filtered_prices)

async def process_card_batch(
    card_details_list: List[CardDetails],
    browser_context,
    session: aiohttp.ClientSession
) -> List[CardPriceData]:
    """Processes a batch of cards concurrently"""
    try:
        tcg_tasks = [
            fetch_tcgplayer_data(card_details, browser_context)
            for card_details in card_details_list
        ]
        tcg_results = await asyncio.gather(*tcg_tasks, return_exceptions=True)
        
        all_price_data = []
        for card_details, tcg_result in zip(card_details_list, tcg_results):
            if isinstance(tcg_result, Exception):
                logger.error(f"Error processing {card_details.name}: {str(tcg_result)}")
                continue
                
            if not tcg_result:
                continue

            for card_data in tcg_result:
                try:
                    ebay_price = await get_ebay_psa10_price_async(session, 
                        CardDetails(
                            name=card_data.card_name,
                            set_name=card_data.set_name,
                            language=card_data.language,
                            product_id=card_data.product_id
                        )
                    )
                    
                    if ebay_price:
                        card_data.psa_10_price = ebay_price
                        card_data.price_delta = ebay_price - card_data.tcgplayer_price
                        card_data.profit_potential = (card_data.price_delta / card_data.tcgplayer_price) * 100
                    all_price_data.append(card_data)
                except Exception as e:
                    logger.error(f"Error processing eBay data for {card_data.card_name}: {str(e)}")
                    continue
                    
        return all_price_data
    finally:
        pass

async def process_card_batch_chunked(
    card_details_list: List[CardDetails],
    browser_context,
    session: aiohttp.ClientSession,
    chunk_size: int = 5
) -> List[CardPriceData]:
    """Process cards in chunks to prevent memory issues"""
    all_results = []
    for i in range(0, len(card_details_list), chunk_size):
        chunk = card_details_list[i:i + chunk_size]
        results = await process_card_batch(chunk, browser_context, session)
        all_results.extend(results)
        await asyncio.sleep(1)  # Prevent rate limiting
    return all_results

async def main(card_details_list: List[CardDetails]) -> List[CardPriceData]:
    await price_cache.async_load_cache()  # Initialize cache asynchronously
    
    try:
        async with AsyncBrowserContext() as context:
            try:
                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(limit=Config.CONCURRENCY),
                    timeout=ClientTimeout(total=Config.TIMEOUT)
                ) as session:
                    results = await process_card_batch_chunked(card_details_list, context, session)
                    return results
            finally:
                await context.close()
                
    except Exception as e:
        safe_log(f"Critical error in main: {str(e)}")
        raise
    finally:
        await price_cache.save_cache()


if __name__ == "__main__":
    cards_to_fetch = [
        CardDetails(name="Charizard ex", set_name="Obsidian Flames", language="English"),
        CardDetails(name="Mew ex", set_name="Pokemon Card 151", language="Japanese")
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
