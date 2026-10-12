import os
from unittest.mock import Mock, patch

import app


def test_search_maps_amazon_results():
    with (
        patch.dict(
            os.environ,
            {
                "SERPAPI_KEY": "test-key",
                "AMAZON_MARKETPLACE": "www.amazon.com",
            },
            clear=False,
        ),
        patch("app.save_picture", return_value="headphones.jpg"),
        patch("app.requests.get") as mock_get,
    ):
        response = Mock()
        response.json.return_value = {
            "organic_results": [
                {
                    "title": "Wireless headphones",
                    "extracted_price": 39.95,
                    "rating": 4.7,
                    "thumbnail": "https://images.example/headphones.jpg",
                },
                {"title": "Unpriced result"},
            ]
        }
        mock_get.return_value = response

        products = app.search_amazon("wireless headphones")

    assert len(products) == 1
    assert products[0]["Product_Name"] == "Wireless headphones"
    assert products[0]["Product_Price"] == 39.95
    assert products[0]["Commission_fee"] == 4.0
    assert products[0]["Total_price"] == 43.95
    assert products[0]["global_rating"] == "4.7 out of 5 stars"
    assert products[0]["Product_picture_name"] == "headphones.jpg"
    assert mock_get.call_args.kwargs["params"] == {
        "engine": "amazon",
        "amazon_domain": "amazon.com",
        "k": "wireless headphones",
        "api_key": "test-key",
    }
    response.raise_for_status.assert_called_once()


def test_serpapi_error_is_reported():
    with (
        patch.dict(os.environ, {"SERPAPI_KEY": "test-key"}, clear=False),
        patch("app.requests.get") as mock_get,
    ):
        response = Mock()
        response.json.return_value = {"error": "invalid API key"}
        mock_get.return_value = response

        try:
            app.search_amazon("headphones")
        except app.AmazonSearchError:
            return
    raise AssertionError("Expected SerpApi errors to be surfaced")
