# SmartCart

A Flask product search app for the Formative Assessment 4 brief. It searches Amazon through SerpApi (or the Amazon Creators API when no SerpApi key is configured), can use a clearly labeled public demo catalog as a fallback, adds a 10% commission, caches results in MongoDB, logs searches and commission totals, saves product pictures in `Online_Pictures`, and writes an Excel workbook to the user's Desktop.

## Run locally

1. Install Python 3.10 or newer and MongoDB Community Server. Start the MongoDB service.
2. In this folder, install dependencies with `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env`. For Amazon searches, add your SerpApi key as `SERPAPI_KEY`. Set `DEMO_FALLBACK=true` to show clearly labeled demo-catalog results if the Amazon search fails or returns no priced products; use `false` to disable the fallback. If `SERPAPI_KEY` is empty, the app can use the Amazon Creators API credentials below instead. Set `MONGODB_URI` if MongoDB uses another address.
4. Run `python app.py`, then open `http://127.0.0.1:5000`.
5. Enter a product name and your first name, last name, and registration number. The Excel file is saved to the Desktop and can also be downloaded from the results page.

## Amazon Creators API setup

SerpApi is the default provider when `SERPAPI_KEY` is set. The app queries SerpApi's Amazon engine and uses returned product titles, prices, ratings, and thumbnails. Keep the key in your local `.env`; never commit or share it. If no SerpApi key is configured, the app can use Amazon's Creators API instead.

Amazon has deprecated PA-API 5. The Creators API requires an accepted Associates account, qualifying sales, and API approval before credentials are available. See [Amazon's onboarding guide](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/onboarding). After approval, configure the Credential ID, Credential Secret, credential version, and Partner Tag in `.env`. Credential versions map to token endpoints: `3.1` North America, `3.2` Europe, and `3.3` Far East.

Choose **Demo catalog (sample data)** and search for `phone` to test priced results, commission calculations, caching, MongoDB logging, pictures, and Excel export without Amazon credentials. Demo items are explicitly labeled and are not Amazon product results.

## MongoDB collections

- `products_cache`: recent result lists, with a 30-minute expiry by default.
- `search_history`: one document per search, including the term, selected source, UTC timestamp, result count, and whether it used cache.
- `commission_log`: one document per search with the selected source, returned products, and aggregate commission.

The retailer email field is marked `Not publicly listed` because product API responses do not expose a retailer email address. Returned Amazon products and prices depend on the configured provider, marketplace, and item availability.