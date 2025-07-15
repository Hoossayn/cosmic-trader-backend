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
    # Check for amount and usd_value - handle null values
    has_amount = 'amount' in data and data['amount'] is not None
    has_usd_value = 'usd_value' in data and data['usd_value'] is not None
    
    if has_amount and has_usd_value:
        raise ValueError("Provide exactly one of 'amount' or 'usd_value'")
    if not has_amount and not has_usd_value:
        raise ValueError("Provide exactly one of 'amount' or 'usd_value'")
    
    if has_usd_value:
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
    price_input = Decimal(str(data["price"])) if "price" in data and data["price"] is not None else None

    side = OrderSide.BUY if side_str == "buy" else OrderSide.SELL

    if order_type == "market":
        stats = await trading_client.markets_info.get_market_statistics(market_name=market)
        mark_price = Decimal(stats.data.mark_price)
        # Use a more conservative multiplier for market orders
        multiplier = Decimal("1.05") if side == OrderSide.BUY else Decimal("0.95")
        price = mark_price * multiplier
        tif = TimeInForce.IOC
        post_only = False
        print(f"DEBUG: Market order - mark_price: {mark_price}, multiplier: {multiplier}, calculated_price: {price}")
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
        price = ((price / min_price_change).to_integral_value(rounding=ROUND_HALF_UP)) * min_price_change
        print(f"DEBUG: After precision adjustment - min_price_change: {min_price_change}, final_price: {price}")
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
        
        # Handle TP/SL if provided
        result = {"order_id": placed_order.data.id, "external_id": placed_order.data.external_id}
        
        # Set Take Profit if provided
        if 'take_profit_price' in data and data['take_profit_price'] is not None:
            tp_price = Decimal(str(data['take_profit_price']))
            tp_price = ((tp_price / min_price_change).to_integral_value(rounding=ROUND_HALF_UP) * min_price_change)
            try:
                tp_result = await trading_client.account.set_take_profit(market_name=market, price=tp_price)
                result["take_profit"] = {"price": str(tp_price), "success": True}
            except Exception as tp_error:
                result["take_profit"] = {"price": str(tp_price), "success": False, "error": str(tp_error)}
        
        # Set Stop Loss if provided
        if 'stop_loss_price' in data and data['stop_loss_price'] is not None:
            sl_price = Decimal(str(data['stop_loss_price']))
            sl_price = ((sl_price / min_price_change).to_integral_value(rounding=ROUND_HALF_UP) * min_price_change)
            try:
                sl_result = await trading_client.account.set_stop_loss(market_name=market, price=sl_price)
                result["stop_loss"] = {"price": str(sl_price), "success": True}
            except Exception as sl_error:
                result["stop_loss"] = {"price": str(sl_price), "success": False, "error": str(sl_error)}
        
        return result
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
    return {"data": [p.model_dump() for p in positions.data]}

@app.get("/closed_positions")
async def get_closed_positions(market_names: List[str] = Query(None)):
    history = await trading_client.account.get_positions_history(market_names=market_names)
    return {"data": [p.model_dump() for p in history.data]}

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

@app.post("/set_take_profit")
async def set_take_profit(data: Dict = Body(...)):
    try:
        market_name = data["market_name"]
        price = Decimal(str(data["price"]))
        
        # Get market config for price precision
        markets = await trading_client.markets_info.get_markets(market_names=[market_name])
        market_config = markets.data[0]
        min_price_change = Decimal(market_config.trading_config.min_price_change)
        price = ((price / min_price_change).to_integral_value(rounding=ROUND_HALF_UP) * min_price_change)
        
        result = await trading_client.account.set_take_profit(market_name=market_name, price=price)
        return {"success": True, "message": f"Take profit set at ${price} for {market_name}", "data": result.data.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/set_stop_loss")
async def set_stop_loss(data: Dict = Body(...)):
    try:
        market_name = data["market_name"]
        price = Decimal(str(data["price"]))
        
        # Get market config for price precision
        markets = await trading_client.markets_info.get_markets(market_names=[market_name])
        market_config = markets.data[0]
        min_price_change = Decimal(market_config.trading_config.min_price_change)
        price = ((price / min_price_change).to_integral_value(rounding=ROUND_HALF_UP) * min_price_change)
        
        result = await trading_client.account.set_stop_loss(market_name=market_name, price=price)
        return {"success": True, "message": f"Stop loss set at ${price} for {market_name}", "data": result.data.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/markets")
async def get_markets(market_names: List[str] = Query(None)):
    """
    Get market information including configurations and statistics
    
    Args:
        market_names: Optional list of specific market names to filter. If None, returns all markets.
    
    Returns:
        List of markets with their configurations and current statistics
    """
    try:
        # Get market configurations
        markets_response = await trading_client.markets_info.get_markets(market_names=market_names)
        markets_data = []
        
        for market in markets_response.data:
            # Get market statistics for each market
            try:
                stats_response = await trading_client.markets_info.get_market_statistics(market_name=market.name)
                market_stats = stats_response.data.model_dump()
            except Exception as stats_error:
                # If stats fail for individual market, continue with others
                market_stats = {"error": f"Failed to get statistics: {str(stats_error)}"}
            
            # Combine market config with statistics
            market_info = {
                "name": market.name,
                "config": market.model_dump(),
                "statistics": market_stats
            }
            markets_data.append(market_info)
        
        return {"data": markets_data, "count": len(markets_data)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/markets/{market_name}/statistics")
async def get_market_statistics(market_name: str):
    """
    Get detailed statistics for a specific market
    
    Args:
        market_name: Name of the market (e.g., "BTC-USD", "ETH-USD")
        
    Returns:
        Market statistics including prices, volume, funding rates, etc.
    """
    try:
        stats = await trading_client.markets_info.get_market_statistics(market_name=market_name)
        return {"market": market_name, "statistics": stats.data.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/markets/{market_name}/config")
async def get_market_config(market_name: str):
    """
    Get configuration details for a specific market
    
    Args:
        market_name: Name of the market (e.g., "BTC-USD", "ETH-USD")
        
    Returns:
        Market configuration including trading limits, precision, leverage, etc.
    """
    try:
        markets = await trading_client.markets_info.get_markets(market_names=[market_name])
        if not markets.data:
            raise HTTPException(status_code=404, detail=f"Market {market_name} not found")
        
        market_config = markets.data[0]
        return {"market": market_name, "config": market_config.model_dump()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) 