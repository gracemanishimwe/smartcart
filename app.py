import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, abort, render_template, request, send_from_directory
from openpyxl import Workbook
from pymongo import MongoClient
from pymongo.errors import PyMongoError


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
PICTURES_DIR = BASE_DIR / "Online_Pictures"
DESKTOP_DIR = Path.home() / "Desktop"
CACHE_MINUTES = int(os.getenv("CACHE_MINUTES", "30"))
PRODUCT_FIELDS = (
    "Product_Name",
    "Product_Price",
    "Commission_fee",
    "Total_price",
    "global_rating",
    "retailer_name",
    "Retailer_email",
    "Product_picture_name",
)


class AmazonPricesUnavailable(Exception):
    pass


class AmazonCreatorsNotConfigured(Exception):
    pass


class AmazonSearchError(Exception):
    pass


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
_mongo_client = None
_mongo_db = None
_creators_access_token = None
_creators_token_expires_at = 0


def get_database():
    global _mongo_client, _mongo_db
    if _mongo_db is None:
        _mongo_client = MongoClient(
            os.getenv("MONGODB_URI", "mongodb://localhost:27017/"),
            serverSelectionTimeoutMS=2000,
        )
        _mongo_db = _mongo_client[os.getenv("MONGODB_DATABASE", "smartcart")]
        _mongo_db.products_cache.create_index("expires_at", expireAfterSeconds=0)
        _mongo_db.search_history.create_index("timestamp")
        _mongo_db.commission_log.create_index("timestamp")
    return _mongo_db


def calculate_product(
    name,
    price,
    rating,
    picture_url,
    index,
    retailer_name="Amazon",
):
    commission = round(price * 0.10, 2)
    picture_name = save_picture(picture_url, name, index)
    return {
        "Product_Name": name,
        "Product_Price": round(price, 2),
        "Commission_fee": commission,
        "Total_price": round(price + commission, 2),
        "global_rating": rating or "Not listed",
        "retailer_name": retailer_name,
        "Retailer_email": "Not publicly listed",
        "Product_picture_name": picture_name or "Not downloaded",
    }


def save_picture(url, product_name, index):
    if not url:
        return None
    PICTURES_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", product_name).strip("_").lower()[:45]
    filename = f"{slug or 'product'}_{index}.jpg"
    try:
        
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 SmartCart/1.0"},
            timeout=8,
        )  
        response.raise_for_status()
        if not response.headers.get("Content-Type", "").startswith("image/"):
            return None
        (PICTURES_DIR / filename).write_bytes(response.content)
        return filename
    except requests.RequestException:
        return None


def search_amazon(term):
    global _creators_access_token, _creators_token_expires_at
    serpapi_key = os.getenv("SERPAPI_KEY", "").strip()
    if serpapi_key:
        marketplace = os.getenv("AMAZON_MARKETPLACE", "www.amazon.com").strip()
        amazon_domain = (
            marketplace[4:] if marketplace.startswith("www.") else marketplace
        )
        response = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "amazon",
                "amazon_domain": amazon_domain,
                "k": term,
                "api_key": serpapi_key,
            },
            timeout=20,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise AmazonSearchError("SerpApi returned an invalid response.") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise AmazonSearchError(
                "SerpApi could not complete the Amazon search. Verify the API key "
                "and SerpApi account."
            )

        organic_results = payload.get("organic_results", [])
        if not isinstance(organic_results, list):
            raise AmazonSearchError("SerpApi returned an invalid product list.")

        products = []
        for item in organic_results[:20]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            raw_price = item.get("extracted_price")
            if raw_price is None:
                raw_price = item.get("price")
            if isinstance(raw_price, str):
                match = re.search(r"[0-9][0-9,]*(?:\.[0-9]{1,2})?", raw_price)
                raw_price = match.group(0).replace(",", "") if match else None
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                continue
            if not title or not math.isfinite(price) or price <= 0:
                continue

            rating = item.get("rating")
            rating_text = (
                f"{rating} out of 5 stars"
                if isinstance(rating, (int, float))
                else str(rating or "Not listed")
            )
            products.append(
                calculate_product(
                    title,
                    price,
                    rating_text,
                    item.get("thumbnail"),
                    len(products) + 1,
                    retailer_name="Amazon",
                )
            )
        return products

    credential_id = os.getenv("CREATORS_API_CREDENTIAL_ID", "").strip()
    credential_secret = os.getenv("CREATORS_API_CREDENTIAL_SECRET", "").strip()
    credential_version = os.getenv("CREATORS_API_VERSION", "").strip()
    partner_tag = os.getenv("AMAZON_PARTNER_TAG", "").strip()
    marketplace = os.getenv("AMAZON_MARKETPLACE", "www.amazon.com").strip()
    token_endpoints = {
        "3.1": "https://api.amazon.com/auth/o2/token",
        "3.2": "https://api.amazon.co.uk/auth/o2/token",
        "3.3": "https://api.amazon.co.jp/auth/o2/token",
    }
    token_endpoint = token_endpoints.get(credential_version)
    if not all((credential_id, credential_secret, partner_tag, marketplace, token_endpoint)):
        raise AmazonCreatorsNotConfigured(
            "Amazon Creators API is not configured. Complete Amazon Associates and "
            "Creators API approval, then add your credential ID, secret, version, "
            "and partner tag to the local .env file."
        )

    if not _creators_access_token or time.monotonic() >= _creators_token_expires_at:
        token_response = requests.post(
            token_endpoint,
            json={
                "grant_type": "client_credentials",
                "client_id": credential_id,
                "client_secret": credential_secret,
                "scope": "creatorsapi::default",
            },
            timeout=15,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        _creators_access_token = token_data["access_token"]
        _creators_token_expires_at = time.monotonic() + int(
            token_data.get("expires_in", 3600)
        ) - 60

    response = requests.post(
        "https://creatorsapi.amazon/catalog/v1/searchItems",
        headers={
            "Authorization": f"Bearer {_creators_access_token}",
            "Content-Type": "application/json",
            "x-marketplace": marketplace,
        },
        json={
            "keywords": term,
            "itemCount": 10,
            "partnerTag": partner_tag,
            "marketplace": marketplace,
            "resources": [
                "images.primary.medium",
                "itemInfo.title",
                "offersV2.listings.price",
                "offersV2.listings.merchantInfo",
            ],
        },
        timeout=20,
    )
    response.raise_for_status()
    items = response.json().get("searchResult", {}).get("items", [])
    products = []
    for item in items:
        title = (
            item.get("itemInfo", {}).get("title", {}).get("displayValue")
            or "Amazon product"
        )
        listings = item.get("offersV2", {}).get("listings", []) or []
        if not listings:
            continue
        price_data = listings[0].get("price", {}).get("money", {})
        price = price_data.get("amount")
        if price is None:
            continue
        image_url = (
            item.get("images", {})
            .get("primary", {})
            .get("medium", {})
            .get("url")
        )
        merchant_name = (
            listings[0].get("merchantInfo", {}).get("name") or "Amazon"
        )
        products.append(
            calculate_product(
                title,
                float(price),
                None,
                image_url,
                len(products) + 1,
                retailer_name=merchant_name,
            )
        )
    if items and not products:
        raise AmazonPricesUnavailable(
            f"The Amazon Creators API returned {len(items)} listings without offer prices. "
            "No commissions were calculated for these products."
        )
    return products


def search_demo_catalog(term):
    response = requests.get(
        "https://dummyjson.com/products/search",
        params={"q": term, "limit": 12},
        timeout=15,
    )
    response.raise_for_status()
    products = []
    for item in response.json().get("products", []):
        products.append(
            calculate_product(
                item.get("title", "Untitled product"),
                float(item["price"]),
                item.get("rating"),
                item.get("thumbnail"),
                len(products) + 1,
                retailer_name="Demo catalog",
            )
        )
    return products


def export_products(products, first_name, last_name, registration_number):
    safe_parts = [
        re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
        for value in (first_name, last_name, registration_number)
    ]
    filename = "_".join(safe_parts) + ".xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SmartCart Products"
    sheet.append(list(PRODUCT_FIELDS))
    for product in products:
        sheet.append([product.get(field, "") for field in PRODUCT_FIELDS])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        width = min(max(max(len(str(cell.value or "")) for cell in column) + 2, 14), 42)
        sheet.column_dimensions[column[0].column_letter].width = width
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    workbook.save(DESKTOP_DIR / filename)
    return filename


def record_search(db, term, products, cache_hit, source):
    now = datetime.now(timezone.utc)
    db.search_history.insert_one(
        {
            "search_term": term,
            "source": source,
            "timestamp": now,
            "result_count": len(products),
            "cache_hit": cache_hit,
        }
    )
    db.commission_log.insert_one(
        {
            "search_term": term,
            "source": source,
            "timestamp": now,
            "products": products,
            "product_count": len(products),
            "total_commission": round(sum(p["Commission_fee"] for p in products), 2),
        }
    )


@app.route("/", methods=["GET", "POST"])
def index():
    products = []
    error = None
    term = ""
    export_filename = None
    cache_hit = False
    source = "amazon"
    identity = {"first_name": "", "last_name": "", "registration_number": ""}

    if request.method == "POST":
        term = request.form.get("product_name", "").strip()
        source = request.form.get("source", "amazon")
        if source not in {"amazon", "demo"}:
            source = "amazon"
        identity = {
            key: request.form.get(key, "").strip()
            for key in identity
        }
        if not term:
            error = "Enter a product name to search."
        elif not all(identity.values()):
            error = "Enter your first name, last name, and registration number for the Excel filename."
        else:
            try:
                db = get_database()
                now = datetime.now(timezone.utc)
                fallback_enabled = os.getenv(
                    "DEMO_FALLBACK", "false"
                ).strip().lower() in {"true", "1", "yes", "on"}
                cache_entry = db.products_cache.find_one(
                    {
                        "term": term.casefold(),
                        "source": source,
                        "expires_at": {"$gt": now},
                    }
                )
                if cache_entry:
                    products = cache_entry["products"]
                    cache_hit = True
                else:
                    if source == "demo":
                        products = search_demo_catalog(term)
                    else:
                        try:
                            products = search_amazon(term)
                        except (
                            AmazonCreatorsNotConfigured,
                            AmazonPricesUnavailable,
                            AmazonSearchError,
                            requests.RequestException,
                        ) as exc:
                            if not fallback_enabled:
                                raise
                            app.logger.warning(
                                "Amazon search failed; using the demo catalog (%s)",
                                type(exc).__name__,
                            )
                            products = []

                if source == "amazon" and not products and fallback_enabled:
                    products = search_demo_catalog(term)
                    source = "demo"
                    cache_hit = False

                if not cache_hit and products:
                    db.products_cache.update_one(
                        {"term": term.casefold(), "source": source},
                        {
                            "$set": {
                                "products": products,
                                "cached_at": now,
                                "expires_at": now + timedelta(minutes=CACHE_MINUTES),
                            }
                        },
                        upsert=True,
                    )
                record_search(db, term, products, cache_hit, source)
                export_filename = export_products(products, **identity)
                if not products:
                    source_name = "demo catalog" if source == "demo" else "Amazon"
                    error = f"No priced products were found in the {source_name} for this search."
            except AmazonPricesUnavailable as exc:
                error = str(exc)
                try:
                    record_search(db, term, [], False, source)
                except PyMongoError:
                    error += " MongoDB was unavailable, so the attempt could not be logged."
            except AmazonCreatorsNotConfigured as exc:
                error = str(exc)
                record_search(db, term, [], False, source)
            except AmazonSearchError as exc:
                error = str(exc)
                record_search(db, term, [], False, source)
            except requests.RequestException:
                error = "The product search request failed. Check your API credentials, " \
                    "marketplace, network connection, and provider access."
                try:
                    db = get_database()
                    record_search(db, term, [], False, source)
                except PyMongoError:
                    pass
            except PyMongoError:
                error = "MongoDB is unavailable. Start MongoDB and verify MONGODB_URI, then search again."
            except OSError:
                error = "The Excel file could not be saved to your Desktop. Check the folder permissions."

    return render_template(
        "index.html",
        products=products,
        error=error,
        term=term,
        cache_hit=cache_hit,
        export_filename=export_filename,
        identity=identity,
        source=source,
    )


@app.get("/pictures/<path:filename>")
def product_picture(filename):
    return send_from_directory(PICTURES_DIR, filename)


@app.get("/download/<path:filename>")
def download_export(filename):
    if Path(filename).name != filename or not filename.endswith(".xlsx"):
        abort(404)
    return send_from_directory(DESKTOP_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")