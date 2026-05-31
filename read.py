import requests

# Get series information for KXHIGHNY
url = "https://external-api.kalshi.com/trade-api/v2/series/KXHIGHNY"
response = requests.get(url)
series_data = response.json()

print(f"Series Title: {series_data['series']['title']}")
print(f"Frequency: {series_data['series']['frequency']}")
print(f"Category: {series_data['series']['category']}")

# Get all open markets for the KXHIGHNY series
markets_url = f"https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXHIGHNY&status=open"
markets_response = requests.get(markets_url)
markets_data = markets_response.json()

print(f"\nActive markets in KXHIGHNY series:")
for market in markets_data['markets']:
    print(f"- {market['ticker']}: {market['title']}")
    print(f"  Event: {market['event_ticker']}")
    print(f"  Yes Price: ${market['yes_bid_dollars']} | Volume: {market['volume_fp']}")
    print()

# Get details for a specific event if you have its ticker
if markets_data['markets']:
    # Let's get details for the first market's event
    event_ticker = markets_data['markets'][0]['event_ticker']
    event_url = f"https://external-api.kalshi.com/trade-api/v2/events/{event_ticker}"
    event_response = requests.get(event_url)
    event_data = event_response.json()

    print(f"Event Details:")
    print(f"Title: {event_data['event']['title']}")
    print(f"Category: {event_data['event']['category']}")
    
# Get orderbook for a specific market
# Replace with an actual market ticker from the markets list
if not markets_data['markets']:
    raise ValueError("No open markets found. Try removing status=open or choose another series.")

market_ticker = markets_data['markets'][0]['ticker']
orderbook_url = f"https://external-api.kalshi.com/trade-api/v2/markets/{market_ticker}/orderbook"

orderbook_response = requests.get(orderbook_url)
orderbook_data = orderbook_response.json()

print(f"\nOrderbook for {market_ticker}:")
print("YES BIDS:")
for price_dollars, count_fp in orderbook_data['orderbook_fp']['yes_dollars'][:5]:  # Show top 5
    print(f"  Price: ${price_dollars}, Quantity: {count_fp}")

print("\nNO BIDS:")
for price_dollars, count_fp in orderbook_data['orderbook_fp']['no_dollars'][:5]:  # Show top 5
    print(f"  Price: ${price_dollars}, Quantity: {count_fp}")