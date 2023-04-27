
import numpy as np
from typing import Optional
import pandas as pd
from datetime import datetime


class BasicTradingInfo:
    def __int__(self,
                t: datetime,
                buy_orders: list[(float, int)],
                sell_orders: list[(float, int)]):
        self.t = datetime
        # n档报价
        self.buy_orders = buy_orders
        self.sell_orders = sell_orders

class SingleTradeLog:
    def __init__(self,
                 t: datetime,
                 side: str,
                 code: str,
                 target_price: float,
                 achieve_price: float,
                 slippage_cost: float = 0.0,
                 commission_cost: float = 0.0,
                 transfer_cost: float = 0.0):
        self.t = t
        self.side = side
        self.code = code
        self.target_price = target_price
        self.achive_price = achieve_price
        self.slippage_cost = slippage_cost
        self.commission_cost = commission_cost
        self.transfer_cost = transfer_cost

class Stock:
    def __int__(self,
                code: str,
                slippage: float = 5e-3,
                commission: float = 3e-4,
                ):
        self.code = code
        self.commission = commission
        self.slippage = slippage

        # 当前持仓状态
        self.cost = 0
        self.num = 0

    def get_value(self):
        return self.cost * self.num

    def get_trading_info(self,
                         start_time: datetime,
                         end_time: datetime,
                         query_api: int = 1):
        """更新证券价格当前信息, return DataFrame"""

        # 实现具体function
        # current_info = trading_info_query_api(self.code, start_time, end_time, query_api)
        current_info = None
        return current_info


class Account:
    def __int__(self,
                initial_capital: float = 10000.0,
                broker_name: str = 'default',
                stocks: Optional[dict[str,Stock]] = dict(),
                ):
        self.total_value = initial_capital
        self.cash = initial_capital
        self.stocks = stocks
        self.broker_name = broker_name


class SingleTrader:
    def __init__(self,
                 accounts: set[Account],
                 ):
        self.accounts = set()
        self.total_cash, self.total_equity = self.get_cash_and_holding_value()

    def get_cash_and_holding_value(self):
        total_cash = 0.0
        total_equity = 0.0

        for account in self.accounts:
            cash = account.cash
            for stock in account.stocks:
                stock_value = stock.caculate_holding_value()
                total_equity += stock_value
            total_cash += cash
        return total_cash, total_equity

    def buy(self,
            t: datetime,
            account: Account,
            code: str,
            num: int,
            bid_ask_info: dict[(str, float)],
            is_backtest: bool = True,
            target_price: float = None
            ):
        stock = account.stocks.get(code, Stock(code))
        trade_log = None
        if is_backtest:
            available_num = bid_ask_info['volume']
            avg_price = (bid_ask_info['open'] + bid_ask_info['close']) / 2.0
            achive_price = avg_price * (1+stock.slippage)
            buy_num = min(num, available_num)

            stock_cost = buy_num * avg_price
            slippage_cost = buy_num * avg_price * stock.slippage
            commission_cost = buy_num * avg_price * stock.commission
            transfer_cost = 5  # 单笔5元
            total_cost = stock_cost + slippage_cost + commission_cost + transfer_cost

            available_cash = account.cash
            if available_cash >= total_cost:
                account.cash -= total_cost

                previous_cost, previous_num = stock.cost, stock.num
                stock.cost, stock.num = (previous_cost*previous_num + total_cost) / (previous_num + buy_num), \
                    previous_num + buy_num

                trade_log = SingleTradeLog(t,"buy",code,target_price,achive_price,slippage_cost,commission_cost,
                                           transfer_cost)
        else:
            assert target_price > 0
        return trade_log


    def sell(self,
             t: datetime,
             account: Account,
             code: str,
             num: int,
             bid_ask_info: dict[(str, float)],
             is_backtest: bool = True,
             target_price: float = None
             ):
        stock_cash = 0.0
        slippage_cost = 0.0
        commission_cost = 0.0
        transfer_cost = 0
        assert code in account.stocks
        stock = account.stocks[code]
        assert num <= stock.num
        trade_log = None
        if is_backtest:
            available_num = bid_ask_info['volume']
            avg_price = (bid_ask_info['open'] + bid_ask_info['close']) / 2.0
            sell_num = min(num, available_num)

            stock_cash = sell_num * avg_price
            slippage_cost = sell_num * avg_price * stock.slippage
            achieve_price = avg_price * (1-stock.slippage)
            commission_cost = sell_num * avg_price * stock.commission
            transfer_cost = 5  # 单笔5元
            total_cash = stock_cash - slippage_cost - commission_cost - transfer_cost

            account.cash += total_cash
            previous_cost, previous_num = stock.cost, stock.num
            stock.num = previous_num - sell_num

            trade_log = SingleTradeLog(t, "sell", code, target_price,
                                       achieve_price, slippage_cost, commission_cost,
                                       transfer_cost)
        else:
            assert target_price > 0
        return trade_log











