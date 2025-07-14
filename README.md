# Cosmic Trader Backend

This is a Python backend using FastAPI and the x10-python-trading SDK to interact with the Extended exchange.

## Setup

1. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

2. Create a .env file in the root directory with the following variables (obtain from https://testnet.extended.exchange/api-management):

   ```
   API_KEY=your_api_key
   PUBLIC_KEY=your_public_key
   PRIVATE_KEY=your_private_key
   VAULT_ID=your_vault_id
   ```

3. Run the server:
   ```
   uvicorn main:app --reload
   ```

## Endpoints

- POST /place_order: Place a market or limit order. Body: {"market": "BTC-USD", "order_type": "limit" or "market", "side": "buy" or "sell", "amount": float, "price": float (for limit), "post_only": bool (optional for limit)}
- GET /account_details: Get balance, positions, and leverage
- GET /orders: Get open orders
