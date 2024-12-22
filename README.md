# Pokemon Django API

An app that allows you to scrape card data from TCGPlayer using Playwright as well as EBay data.


## How to use

First, install the dependencies from **requirements.txt**

`pip install -r requirements.txt`

Next, install the browser dependencies for Playwright

`playwright install`

And now run the following command to start the Django server:

`python manage.py runserver`

### API

The API looks like this

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
        "last_updated": "2024-12-19T11:38:24.616024Z",
        "created_at": "2024-12-19T11:35:54.012028Z"
    },
```

In order to access all the saved cards, you can simply go to your Django server and at the route **/api/cards/**

### Fetch Requests

In order to fetch the searched cards using a Front-end like React, here is a following example.

```javascript
      try {
           const params = new URLSearchParams({
                searchQuery: searchQuery,
               language: language
           }).toString()
          const response = await fetch(`http://127.0.0.1:8000/api/cards/scrape_and_save/?${params}`);
```


The API is expecting a search query input (card name) and the language (Japanese or English).