"""Futu broker position/account mixin — positions, account info, push subscriptions."""

from typing import Any, Dict, List

from quant.infrastructure.execution.brokers.base import AccountInfo, Position


class FutuPositionMixin:
    """Position and account query methods for FutuBroker."""

    def get_positions(self) -> List[Position]:
        """
        Get current positions from Futu.

        Returns:
            List of Position dataclass objects
        """
        if not self._connected or not self._trd_api:
            self.logger.error("Not connected to Futu")
            return []

        if not self._unlocked:
            self.logger.error("Trading not unlocked")
            return []

        positions = []
        markets = ["HK", "US"] if len(self._acc_list) > 1 else [self._acc_list[0].get("market", "HK")] if self._acc_list else ["HK"]

        for market in markets:
            acc_id = self._get_acc_id(market)
            if acc_id == 0:
                continue

            try:
                ret, data = self._trd_api.position_list_query(acc_id=acc_id)
                if ret != 0:
                    self.logger.error(f"Failed to get positions for {market}: {data}")
                    continue

                if data is None or data.empty:
                    continue

                for _, row in data.iterrows():
                    symbol = row.get("code", "")
                    if not symbol:
                        continue

                    qty = float(row.get("position", 0))
                    if qty <= 0:
                        continue

                    avg_cost = float(row.get("cost_price", 0))
                    market_value = float(row.get("market_val", 0))
                    unrealized_pnl = float(row.get("unrealized_pl", 0))

                    positions.append(Position(
                        symbol=symbol,
                        quantity=qty,
                        avg_cost=avg_cost,
                        market_value=market_value,
                        unrealized_pnl=unrealized_pnl,
                    ))

            except Exception as e:
                self.logger.error(f"Error getting positions for {market}: {e}")

        self.logger.info(f"Retrieved {len(positions)} positions")
        return positions

    def get_account_info(self) -> AccountInfo:
        """
        Get account information (cash, buying power, equity).

        Args:
            market: Market to get info for ("HK" or "US"). Uses first available if None.

        Returns:
            AccountInfo dataclass
        """
        if not self._connected or not self._trd_api:
            self.logger.error("Not connected to Futu")
            return AccountInfo(
                account_id="",
                cash=0.0,
                buying_power=0.0,
                equity=0.0,
                margin_used=0.0,
            )

        if not self._unlocked:
            self.logger.error("Trading not unlocked")
            return AccountInfo(
                account_id="",
                cash=0.0,
                buying_power=0.0,
                equity=0.0,
                margin_used=0.0,
            )

        market = "HK"
        if self._acc_list:
            market = self._acc_list[0].get("market", "HK")

        acc_id = self._get_acc_id(market)
        if acc_id == 0:
            self.logger.error(f"No account found for market {market}")
            return AccountInfo(
                account_id="",
                cash=0.0,
                buying_power=0.0,
                equity=0.0,
                margin_used=0.0,
            )

        try:
            ret, data = self._trd_api.accinfo_query(acc_id=acc_id)
            if ret != 0:
                self.logger.error(f"Failed to get account info: {data}")
                return AccountInfo(
                    account_id=str(acc_id),
                    cash=0.0,
                    buying_power=0.0,
                    equity=0.0,
                    margin_used=0.0,
                )

            if data is None or data.empty:
                return AccountInfo(
                    account_id=str(acc_id),
                    cash=0.0,
                    buying_power=0.0,
                    equity=0.0,
                    margin_used=0.0,
                )

            row = data.iloc[0]
            cash = float(row.get("cash", 0))
            buying_power = float(row.get("buying_power", 0))
            equity = float(row.get("total_assets", 0))
            margin_used = float(row.get("margin_used", 0))

            return AccountInfo(
                account_id=str(acc_id),
                cash=cash,
                buying_power=buying_power,
                equity=equity,
                margin_used=margin_used,
            )

        except Exception as e:
            self.logger.error(f"Error getting account info: {e}")
            return AccountInfo(
                account_id=str(acc_id),
                cash=0.0,
                buying_power=0.0,
                equity=0.0,
                margin_used=0.0,
            )

    def get_positions_enriched(self) -> List[Dict[str, Any]]:
        if not self._connected or not self._trd_api:
            return []
        if not self._unlocked:
            return []

        holdings = []
        markets = ["HK", "US"] if len(self._acc_list) > 1 else [self._acc_list[0].get("market", "HK")] if self._acc_list else ["HK"]

        for market in markets:
            acc_id = self._get_acc_id(market)
            if acc_id == 0:
                continue
            try:
                ret, data = self._trd_api.position_list_query(acc_id=acc_id)
                if ret != 0 or data is None or (hasattr(data, 'empty') and data.empty):
                    continue
                for _, row in data.iterrows():
                    symbol = row.get("code", "")
                    if not symbol:
                        continue
                    qty = float(row.get("position", 0))
                    if qty <= 0:
                        continue
                    cost_price = float(row.get("cost_price", 0))
                    nominal_price = float(row.get("nominal_price", 0))
                    market_val = float(row.get("market_val", 0))
                    unrealized_pl = float(row.get("unrealized_pl", 0))
                    pnl_pct = ((nominal_price - cost_price) / cost_price * 100) if cost_price > 0 else 0.0
                    holdings.append({
                        "symbol": symbol,
                        "quantity": qty,
                        "avg_cost": cost_price,
                        "cost_price": cost_price,
                        "current_price": nominal_price,
                        "nominal_price": nominal_price,
                        "market_value": market_val,
                        "unrealized_pnl": unrealized_pl,
                        "pnl_pct": pnl_pct,
                        "market": market,
                        "stock_name": row.get("stock_name", ""),
                        "today_buy_qty": float(row.get("today_buy_qty", 0)),
                        "today_sell_qty": float(row.get("today_sell_qty", 0)),
                    })
            except Exception as e:
                self.logger.error(f"Error getting enriched positions for {market}: {e}")

        return holdings

    def get_account_detail(self) -> Dict[str, Any]:
        if not self._connected or not self._trd_api:
            return {}
        if not self._unlocked:
            return {}

        result = {
            "total_assets": 0.0,
            "cash": 0.0,
            "buying_power": 0.0,
            "market_val": 0.0,
            "unrealized_pl": 0.0,
            "realized_pl": 0.0,
            "power": 0.0,
            "available_funds": 0.0,
            "securities_assets": 0.0,
            "hk": {"cash": 0.0, "assets": 0.0, "buying_power": 0.0, "market_val": 0.0},
            "us": {"cash": 0.0, "assets": 0.0, "buying_power": 0.0, "market_val": 0.0},
        }

        markets = ["HK", "US"] if len(self._acc_list) > 1 else [self._acc_list[0].get("market", "HK")] if self._acc_list else ["HK"]

        for market in markets:
            acc_id = self._get_acc_id(market)
            if acc_id == 0:
                continue
            try:
                ret, data = self._trd_api.accinfo_query(acc_id=acc_id)
                if ret != 0 or data is None or (hasattr(data, 'empty') and data.empty):
                    continue
                row = data.iloc[0]
                mkt = {
                    "cash": float(row.get("cash", 0)),
                    "assets": float(row.get("total_assets", 0)),
                    "buying_power": float(row.get("buying_power", 0)),
                    "market_val": float(row.get("market_val", 0)),
                }
                if market == "HK":
                    result["hk"] = mkt
                else:
                    result["us"] = mkt
                result["total_assets"] += mkt["assets"]
                result["cash"] += mkt["cash"]
                result["buying_power"] += mkt["buying_power"]
                result["market_val"] += mkt["market_val"]
                result["power"] = result["buying_power"]
                result["available_funds"] = result["cash"]
                result["securities_assets"] = result["market_val"]
            except Exception as e:
                self.logger.error(f"Error getting account detail for {market}: {e}")

        return result

    def subscribe_acc_push(self) -> None:
        """Subscribe to account data push (positions, balance changes)."""
        if not self._connected or not self._trd_api:
            return

        try:
            ret = self._trd_api.sub_acc_push()
            if ret != 0:
                self.logger.error(f"Failed to subscribe to acc push")
            else:
                self.logger.info("Subscribed to account push data")

        except Exception as e:
            self.logger.error(f"Error subscribing to acc push: {e}")
