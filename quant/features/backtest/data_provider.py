"""In-memory data provider for backtesting — pre-indexed bar and dividend lookup."""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class DataFrameProvider:
    """In-memory data provider for backtesting, with pre-indexed lookup."""

    def __init__(self, data: pd.DataFrame, dividends: Optional[pd.DataFrame] = None):
        self.data = data
        self.dividends = dividends if dividends is not None else pd.DataFrame()
        self._bar_map: Dict[Tuple, Dict] = {}
        self._bars_by_date: Dict[object, List[Dict]] = {}
        self._trading_dates: set = set()
        self._dividend_map: Dict[Tuple, Dict] = {}
        self._build_index()
        self._build_dividend_index()

    def _build_index(self) -> None:
        if self.data.empty:
            return
        df = self.data
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df = df.copy()
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        for col in ('open', 'high', 'low', 'close', 'volume'):
            if col not in df.columns:
                logger.warning("DataFrameProvider: missing '%s' column — index not built", col)
                return
        records = df.to_dict('records')
        symbols = df['symbol'].tolist()
        timestamps = df['timestamp'].tolist()
        buf: Dict[Tuple, Dict] = {}
        for rec, sym, ts in zip(records, symbols, timestamps):
            key = ts.date() if hasattr(ts, 'date') else ts
            dict_key = (sym, key)
            existing = buf.get(dict_key)
            if existing is None:
                buf[dict_key] = rec
            else:
                existing_vol = existing.get('volume', 0) or 0
                new_vol = rec.get('volume', 0) or 0
                if new_vol > existing_vol:
                    buf[dict_key] = rec
        dup_count = len(records) - len(buf)
        self._bar_map = buf
        bars_by_date: Dict[object, List[Dict]] = {}
        for (_, dt), rec in buf.items():
            bars_by_date.setdefault(dt, []).append(rec)
        self._bars_by_date = bars_by_date
        for ts in timestamps:
            dt = ts.date() if hasattr(ts, 'date') else ts
            self._trading_dates.add(dt)
        if dup_count > 0:
            logger.warning(
                "DataFrameProvider._build_index: resolved %d duplicate (symbol, date) rows (kept highest volume)",
                dup_count,
            )

    def _build_dividend_index(self) -> None:
        if self.dividends.empty:
            return
        df = self.dividends.copy()
        if 'ex_date' in df.columns and not pd.api.types.is_datetime64_any_dtype(df['ex_date']):
            df['ex_date'] = pd.to_datetime(df['ex_date'])
        for _, row in df.iterrows():
            if 'ex_date' not in row or pd.isna(row['ex_date']):
                continue
            sym = row.get('symbol', '')
            ex_dt = row['ex_date']
            key = ex_dt.date() if hasattr(ex_dt, 'date') else ex_dt
            self._dividend_map[(sym, key)] = row.to_dict()

    @property
    def trading_dates(self) -> set:
        return self._trading_dates

    def get_bars(self, symbol: str, start: datetime, end: datetime, timeframe: str) -> pd.DataFrame:
        start_key = start.date() if hasattr(start, 'date') else start
        end_key = end.date() if hasattr(end, 'date') else end
        rows = []
        for d in sorted(set(k[1] for k in self._bar_map if k[0] == symbol)):
            if start_key <= d < end_key:
                rec = self._bar_map.get((symbol, d))
                if rec is not None:
                    rows.append(rec)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def get_bar_for_date(self, symbol: str, date) -> Optional[Dict]:
        """O(1) lookup for a single bar by symbol + date."""
        key = date.date() if hasattr(date, 'date') else date
        return self._bar_map.get((symbol, key))

    def get_bars_for_date(self, date) -> List[Dict]:
        """Return all indexed bars for one trading date."""
        key = date.date() if hasattr(date, 'date') else date
        return self._bars_by_date.get(key, [])

    def get_dividend_for_date(self, symbol: str, date) -> Optional[Dict]:
        """O(1) lookup for dividend by symbol + ex_date."""
        key = date.date() if hasattr(date, 'date') else date
        return self._dividend_map.get((symbol, key))

    def validate(self) -> List[str]:
        """Check data quality. Delegates to DataValidator, returns all messages for backward compat."""
        from quant.features.backtest.data_validator import DataValidator
        report = DataValidator.validate(self.data)
        return report.errors + report.warnings
