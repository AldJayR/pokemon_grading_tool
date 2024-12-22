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
from dataclasses import dataclass
from aiohttp import ClientTimeout
import json

# Data classes for type safety and better structure
@dataclass
class CardDetails:
    name: str
    set_name: str
    language: str = "English"

@dataclass
class CardPriceData:
    card_name: str
    set_name: str
    language: str
    rarity: str
    tcgplayer_price: float
    psa_10_price: Optional[float] = None
    price_delta: Optional[float] = None
    profit_potential: Optional[float] = None

# Configuration
class Config:
    ONE_MINUTE = 60
    MAX_REQUESTS_PER_MINUTE_TCG = 30
    MAX_REQUESTS_PER_MINUTE_EBAY = 20
    CACHE_DURATION_HOURS = 24
    MAX_RETRIES = 3
    TIMEOUT_SECONDS = 30
    
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

class PriceCache:
    def __init__(self):
        self.cache = {}
        self.filename = "price_cache.json"
        self.load_cache()

    def load_cache(self):
        try:
            with open(self.filename, 'r') as f:
                cache_data = json.load(f)
                self.cache = {
                    k: (v['data'], datetime.fromisoformat(v['timestamp']))
                    for k, v in cache_data.items()
                }
        except (FileNotFoundError, json.JSONDecodeError):
            self.cache = {}

    def save_cache(self):
        cache_data = {
            k: {
                'data': v[0],
                'timestamp': v[1].isoformat()
            }
            for k, v in self.cache.items()
        }
        with open(self.filename, 'w') as f:
            json.dump(cache_data, f)

    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < timedelta(hours=Config.CACHE_DURATION_HOURS):
                return data
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value: Any):
        self.cache[key] = (value, datetime.now())
        self.save_cache()

price_cache = PriceCache()

def cache_results(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
        cached_result = price_cache.get(key)
        
        if cached_result:
            logger.info(f"Cache hit for {key}")
            return cached_result

        result = await func(*args, **kwargs)
        if result:
            price_cache.set(key, result)
        return result

    return wrapper

class RequestError(Exception):
    pass

@sleep_and_retry
@limits(calls=Config.MAX_REQUESTS_PER_MINUTE_TCG, period=Config.ONE_MINUTE)
async def fetch_tcgplayer_data(card_details: CardDetails, context) -> List[CardPriceData]:
    """
    Enhanced TCGPlayer data fetching with better error handling and typing
    """
    if card_details.language not in Config.RARITY_MAPPING:
        raise ValueError(f"Unsupported language: {card_details.language}")

    all_card_data = []
    page = await context.new_page()
    await page.route("**/*.{png,jpg,jpeg}", lambda route: route.abort())

    for rarity in Config.RARITY_MAPPING[card_details.language]:
        try:
            url = build_tcgplayer_url(card_details, rarity)
            await fetch_and_process_page(page, url, card_details, rarity, all_card_data)
        except Exception as e:
            logger.error(f"Error processing rarity {rarity}: {str(e)}", exc_info=True)
        finally:
            await asyncio.sleep(1)  # Gentle delay between requests

    await page.close()
    return all_card_data

def build_tcgplayer_url(card_details: CardDetails, rarity: str) -> str:
    """Constructs the TCGPlayer URL based on card details"""
    base = "https://www.tcgplayer.com/search/pokemon"
    if card_details.language == "Japanese":
        base += "-japan"
    
    params = {
        "productLineName": "pokemon" if card_details.language == "English" else "pokemon-japan",
        "q": card_details.name.replace(" ", "+"),
        "view": "grid",
        "page": "1",
        "ProductTypeName": "Cards",
        "Rarity": rarity.replace(" ", "+")
    }
    
    if card_details.set_name and card_details.set_name.lower() != card_details.name.lower():
        params["setName"] = card_details.set_name.replace(" ", "-").lower()
    
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}/product?{query_string}"

async def fetch_and_process_page(page, url: str, card_details: CardDetails, rarity: str, all_card_data: List[CardPriceData]):
    """Fetches and processes a single TCGPlayer page"""
    for attempt in range(Config.MAX_RETRIES):
        try:
            await page.goto(url, timeout=Config.TIMEOUT_SECONDS * 1000)
            await page.wait_for_selector(".search-result, .blank-slate", timeout=10000)
            
            html = await page.content()
            soup = BeautifulSoup(html, 'lxml')
            
            if cards := process_card_elements(soup, card_details, rarity):
                all_card_data.extend(cards)
                break
            
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt == Config.MAX_RETRIES - 1:
                raise
            await asyncio.sleep(2 ** (attempt + 1))

def process_card_elements(soup: BeautifulSoup, card_details: CardDetails, rarity: str) -> List[CardPriceData]:
    """Processes card elements from the page HTML"""
    cards = []
    for card in soup.find_all('div', class_='search-result'):
        try:
            if card_data := extract_card_data(card, card_details, rarity):
                cards.append(card_data)
        except Exception as e:
            logger.error(f"Error processing card element: {str(e)}", exc_info=True)
    return cards

def extract_card_data(card_element: BeautifulSoup, card_details: CardDetails, rarity: str) -> Optional[CardPriceData]:
    """Extracts data from a single card element"""
    title = card_element.find('span', class_='product-card__title')
    price = card_element.find('span', class_='product-card__market-price--value')
    set_name = card_element.find('div', class_='product-card__set-name__variant')
    
    if not all([title, price, set_name]):
        return None
        
    try:
        price_value = float(price.text.strip().replace('$', ''))
    except (ValueError, AttributeError):
        return None
        
    if card_details.name.lower() not in title.text.strip().lower():
        return None
        
    return CardPriceData(
        card_name=title.text.strip(),
        set_name=set_name.text.strip(),
        language=card_details.language,
        rarity=rarity,
        tcgplayer_price=price_value
    )

@sleep_and_retry
@limits(calls=Config.MAX_REQUESTS_PER_MINUTE_EBAY, period=Config.ONE_MINUTE)
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

def calculate_average_price(prices: List[float]) -> float:
    """Calculates average price with optional outlier removal"""
    if len(prices) < 3:
        return sum(prices) / len(prices)
        
    # Remove outliers using IQR method
    prices.sort()
    q1 = prices[len(prices)//4]
    q3 = prices[3*len(prices)//4]
    iqr = q3 - q1
    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr
    
    filtered_prices = [p for p in prices if lower_bound <= p <= upper_bound]
    return sum(filtered_prices) / len(filtered_prices)

async def process_card_batch(
    card_details_list: List[CardDetails],
    browser_context,
    session: aiohttp.ClientSession
) -> List[CardPriceData]:
    """Processes a batch of cards concurrently"""
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
        
        # Process each rarity variant separately
        for card_data in tcg_result:
            # Create card details specific to this rarity variant
            variant_details = CardDetails(
                name=card_data.card_name,
                set_name=card_data.set_name,
                language=card_data.language
            )
            
            # Get eBay price for this specific variant
            # Extract card number if present
            card_number_match = re.search(r'[-\s](\d+/\d+)', card_data.card_name)
            card_number = card_number_match.group(1) if card_number_match else ""
            
            # Create a more focused but not overly specific search query
            base_name = variant_details.name.split(' - ')[0].strip()  # Get the base card name
            search_query = f"{base_name} {card_number} {card_data.rarity} PSA 10"
            variant_details.name = search_query
            ebay_price = await get_ebay_psa10_price_async(session, variant_details)
            logger.info(f"eBay prices for {search_query}: {ebay_price}")
            
            if ebay_price:
                card_data.psa_10_price = ebay_price
                card_data.price_delta = ebay_price - card_data.tcgplayer_price
                card_data.profit_potential = (card_data.price_delta / card_data.tcgplayer_price) * 100
            all_price_data.append(card_data)
                
    return all_price_data

async def main(card_details_list: List[CardDetails]) -> List[CardPriceData]:
    """Main entry point with improved error handling and performance monitoring"""
    start_time = time.perf_counter()
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=10),
                timeout=ClientTimeout(total=Config.TIMEOUT_SECONDS)
            ) as session:
                results = await process_card_batch(card_details_list, context, session)
                
            await browser.close()
            
    except Exception as e:
        logger.error(f"Critical error in main: {str(e)}", exc_info=True)
        raise
        
    finally:
        elapsed_time = time.perf_counter() - start_time
        logger.info(f"Script completed in {elapsed_time:.2f} seconds")
        
    return results

if __name__ == "__main__":
    cards_to_fetch = [
        CardDetails(name="Charizard ex", set_name="Obsidian Flames", language="English"),
        CardDetails(name="Mew ex", set_name="Pokemon Card 151", language="Japanese")
    ]
    
    results = asyncio.run(main(cards_to_fetch))
    
    # Print results in a formatted way
    for card in results:
        print("\nCard Details:")
        print(f"Name: {card.card_name}")
        print(f"Set: {card.set_name}")
        print(f"Rarity: {card.rarity}")
        print(f"TCGPlayer Price: ${card.tcgplayer_price:.2f}")
        print(f"PSA 10 Price: ${card.psa_10_price:.2f}")
        print(f"Potential Profit: {card.profit_potential:.1f}%")