import pandas as pd
import numpy as np
import yfinance as yf
import akshare as ak
from datetime import datetime

SOURCE_LOCAL = 0


def get_code_name_list(source: int = SOURCE_AKSHARE):
    ak.stock_info_a_code_name()

def get_daily_trade_data(code: str,
                         start_time: datetime,
                         end_time: datetime,
                         source: int = SOURCE_AKSHARE
                         ):


def get_minute_trade_data(code: str,
                          start_time: datetime,
                          end_time: datetime,
                          source: int = SOURCE_LOCAL):
    pass