
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

class Stock:
    def __int__(self,
                code: str,
                history_trading_info: Optional[pd.DataFrame],
                history_research_docs: Optional[(datetime, str)],
                history_financial_reports: Optional[(datetime, str)],
                slippage: float = 5e-3,
                commision: float = 3e-4,
                ):
        self.code = code
        self.history_trading_info = history_trading_info
        self.history_research_docs = history_research_docs
        self.history_financial_reports = history_financial_reports
        self.commision = commision
        self.slippage = slippage

        # 当前持仓状态
        self.current_info = None
        self.num = 0

    def caculate_holding_value(self):
        current_sell_orders = self.current_info.sell_orders
        sell_1_price = current_sell_orders[0][0]
        return sell_1_price * self.num

    def update_trading_info(self,
                            t: datetime,
                            trading_info_query_func):
        """更新证券价格当前信息"""
        current_info = trading_info_query_func(self.code, t)
        return current_info




class Account:
    def __int__(self,
                initial_capital: float = 10000.0,
                broker_name: str = 'default',
                stocks: Optional[set[Stock]] = set(),
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
            account: Account,
            code: str,
            stock: Stock,
            num: int,
            target_price: float,
            is_backtest: bool = True,
            ):
        current_stock = [i for i in account.stocks if stock.code == i.code]


        total_comission_spend = 0.0
        total_slippage_spend = 0.0
        total_cash_spend = 0.0
        total_buy_num = 0
        if is_backtest:
            if current_stock:
                stock = current_stock[0]
            else:
                stock = Stock(code)
            stock.update_trading_info(t, query_api)

            current_info = stock.current_info
            # 尝试购买
            current_sell_orders = current_info.sell_orders
            for offer in current_sell_orders:
                sell_price, sell_num = offer
                if target_price > sell_price and num > 0:
                    buy_num = min(num, sell_num)
                    cash_need = buy_num * sell_price
                    slippage_need = buy_num * sell_price * stock.slippage
                    comission_need = buy_num * stock.commision

                    if account.cash >= cash_need + slippage_need + comission_need + 5:
                        account.cash -= cash_need + slippage_need + comission_need
                        num -= buy_num
                        stock.num += buy_num

                        total_buy_num += buy_num
                        total_cash_spend += cash_need
                        total_slippage_spend += slippage_need
                        total_comission_spend += comission_need
            # 手续费
            total_comission_spend += 5
            account.cash -= 5

            if not current_stock and total_buy_num > 0:
                account.stocks.add(stock)


    def sell(self,
             account: Account,
             code: str,
             t: datetime,
             num: int,
             target_price: float,
             query_api: int = 0,
             is_backtest: bool = True,
             ):
        current_stock = [stock for stock in account.stocks if stock.code == code]
        assert current_stock

        total_comission_spend = 0.0
        total_slippage_spend = 0.0
        total_cash_spend = 0.0
        total_buy_num = 0
        if is_backtest:
            if current_stock:
                stock = current_stock[0]
            else:
                stock = Stock(code)
            stock.update_trading_info(t, query_api)

            current_info = stock.current_info
            # 尝试购买
            current_sell_orders = current_info.sell_orders
            for offer in current_sell_orders:
                sell_price, sell_num = offer
                if target_price > sell_price and num > 0:
                    buy_num = min(num, sell_num)
                    cash_need = buy_num * sell_price
                    slippage_need = buy_num * sell_price * stock.slippage
                    comission_need = buy_num * stock.commision

                    if account.cash >= cash_need + slippage_need + comission_need + 5:
                        account.cash -= cash_need + slippage_need + comission_need
                        num -= buy_num
                        stock.num += buy_num

                        total_buy_num += buy_num
                        total_cash_spend += cash_need
                        total_slippage_spend += slippage_need
                        total_comission_spend += comission_need
            # 手续费
            total_comission_spend += 5
            account.cash -= 5

            if not current_stock and total_buy_num > 0:
                account.stocks.add(stock)











