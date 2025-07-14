import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List

from dotenv import load_dotenv
from fastapi import Body, FastAPI, Query, HTTPException
from x10.perpetual.accounts import StarkPerpetualAccount
from x10.perpetual.configuration import TESTNET_CONFIG
from x10.perpetual.configuration import MAINNET_CONFIG
from x10.perpetual.orders import OrderSide, TimeInForce
from x10.perpetual.trading_client import PerpetualTradingClient
from pydantic import ValidationError
from aiohttp import ClientSession

load_dotenv()

app = FastAPI()

trading_client: PerpetualTradingClient = None


@app.on_event("startup")
async def startup_event():
    global trading_client
    stark_account = StarkPerpetualAccount(
        vault=int(os.getenv("VAULT_ID")),
        private_key=os.getenv("PRIVATE_KEY"),
        public_key=os.getenv("PUBLIC_KEY"),
        api_key=os.getenv("API_KEY"),
    )
    trading_client = PerpetualTradingClient(MAINNET_CONFIG, stark_account)


@app.post("/place_order")
async def place_order(data: Dict = Body(...)):
    market = data["market"]
    order_type = data["order_type"].lower()
    side_str = data["side"].lower()
    if ('amount' in data) and ('usd_value' in data):
        raise ValueError("Provide exactly one of 'amount' or 'usd_value'")
    if ('amount' not in data) and ('usd_value' not in data):
        raise ValueError("Provide exactly one of 'amount' or 'usd_value'")
    if 'usd_value' in data:
        usd_value = Decimal(str(data['usd_value']))
        if order_type == "market":
            stats = await trading_client.markets_info.get_market_statistics(market_name=market)
            mark_price = Decimal(stats.data.mark_price)
            amount = usd_value / mark_price
        elif order_type == "limit":
            if price_input is None:
                raise ValueError("Price required for limit order with usd_value")
            amount = usd_value / price_input
    else:
        amount = Decimal(str(data["amount"]))
    price_input = Decimal(str(data.get("price", "0"))) if "price" in data else None

    side = OrderSide.BUY if side_str == "buy" else OrderSide.SELL

    if order_type == "market":
        stats = await trading_client.markets_info.get_market_statistics(market_name=market)
        mark_price = Decimal(stats.data.mark_price)
        multiplier = Decimal("1.15") if side == OrderSide.BUY else Decimal("0.85")
        price = mark_price * multiplier
        tif = TimeInForce.IOC
        post_only = False
    elif order_type == "limit":
        if price_input is None:
            raise ValueError("Price required for limit order")
        price = price_input
        tif = TimeInForce.GTT
        post_only = data.get("post_only", False)
    else:
        raise ValueError("Unsupported order type")

    try:
        markets = await trading_client.markets_info.get_markets(market_names=[market])
        market_config = markets.data[0]
        min_order_size = Decimal(market_config.trading_config.min_order_size)
        min_change = Decimal(market_config.trading_config.min_order_size_change)
        amount = ((amount / min_change).to_integral_value(rounding=ROUND_HALF_UP)) * min_change
        if amount < min_order_size:
            raise ValueError(f"Adjusted amount {amount} is less than minimum order size {min_order_size} for {market}")
        min_price_change = Decimal(market_config.trading_config.min_price_change)
        price = ((price / min_price_change).to_integral_value(rounding=ROUND_HALF_UP) * min_price_change)
        max_leverage = Decimal(market_config.trading_config.max_leverage)
        leverage_value = Decimal(str(data.get('leverage', '15')))
        if leverage_value < Decimal('2') or leverage_value > max_leverage:
            raise ValueError(f"Leverage must be between 2 and {max_leverage} for {market}")
        await trading_client.account.update_leverage(market_name=market, leverage=leverage_value)
        placed_order = await trading_client.place_order(
            market_name=market,
            amount_of_synthetic=amount,
            price=price,
            side=side,
            time_in_force=tif,
            post_only=post_only,
        )
        return {"order_id": placed_order.data.id, "external_id": placed_order.data.external_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/account_details")
async def get_account_details():
    balance = await trading_client.account.get_balance()
  

    return {
        "balance": balance.data.model_dump(),
        
    }


@app.get("/leverage")
async def get_leverage(market_names: List[str] = Query(None)):
    leverage = await trading_client.account.get_leverage(market_names=market_names)
    return [l.model_dump() for l in leverage.data]

@app.get("/open_positions")
async def get_open_positions(market_names: List[str] = Query(None)):
    positions = await trading_client.account.get_positions(market_names=market_names)
    return [p.model_dump() for p in positions.data]

@app.get("/closed_positions")
async def get_closed_positions(market_names: List[str] = Query(None)):
    history = await trading_client.account.get_positions_history(market_names=market_names)
    return [p.model_dump() for p in history.data]

@app.get("/orders")
async def get_orders():
    try:
        orders = await trading_client.account.get_open_orders()
        return [o.model_dump() for o in orders.data]
    except ValidationError as e:
        async with ClientSession() as session:
            url = trading_client.account._get_url("/user/orders")
            headers = {"X-Api-Key": trading_client.account._get_api_key()}
            async with session.get(url, headers=headers) as resp:
                raw = await resp.json()
                return {"raw_response": raw} 