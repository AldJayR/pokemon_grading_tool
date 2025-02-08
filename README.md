# Pokemon Grading Tool API

A Django-based API for tracking Pokemon card values by scraping TCGPlayer and eBay data using Playwright.

## Features

- Real-time price scraping from TCGPlayer and eBay
- RESTful API endpoints for card data access
- Automated price delta calculations
- Docker containerization support
- Playwright browser automation
- Environment configuration (.env)
- Price caching system (price_cache.json)
- pytest testing framework

## Installation

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Setup Playwright browsers
playwright install

# Run migrations
python manage.py migrate

# Start development server
python manage.py runserver
```

### Docker Setup
```bash
# Build and start container
docker-compose up --build

# Run migrations in container
docker-compose exec web python manage.py migrate
```

## Configuration

Create `.env` file with these variables:
```ini
DEBUG=False
SECRET_KEY=your-django-secret-key

```

## API Documentation

### Endpoints
- `GET /api/cards/` - List all cards
- `GET /api/cards/scrape_and_save/` - Scrape new cards
  - Parameters:
    - `searchQuery`: Card name to search
    - `language`: English/Japanese
- `GET /api/cards/fetch_card/` - Fetch a single card from the database
    - Parameters:
      - `card_name`: Card name to fetch
- `GET /api/cards/scrape_all_sets/` - Scrapes all sets


### Example Response
```json
{
    "id": 3,
    "card_name": "Pikachu ex - 247/191",
    "set_name": "SV08: Surging Sparks",
    "language": "English",
    "rarity": "Hyper Rare",
    "tcgplayer_price": "151.79",
    "psa_10_price": "124.39",
    "price_delta": "-27.40",
    "profit_potential": "-18.05",
    "last_updated": "2024-12-19T11:38:24.616024Z"
}
```

## Usage Example
```javascript

fetch(`http://localhost:8000/api/cards/fetch_card/?card_name=Pikachu`)
  .then(response => response.json())
  .then(data => console.log(data));
```
