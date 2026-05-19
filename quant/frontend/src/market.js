export const detectMarket = (symbol) => {
  const value = String(symbol || '').trim();
  if (value.startsWith('HK.') || /^\d{1,5}$/.test(value)) return 'HK';
  if (/^\d{6}$/.test(value)) return 'CN';
  return 'US';
};

export const currencyForMarket = (market) => {
  if (market === 'CN') return 'CNY';
  if (market === 'HK') return 'HKD';
  return 'USD';
};

export const detectCurrency = (symbols) => {
  const list = Array.isArray(symbols)
    ? symbols
    : String(symbols || '').split(',');
  const currencies = new Set(
    list
      .map(s => String(s || '').trim())
      .filter(Boolean)
      .map(s => currencyForMarket(detectMarket(s)))
  );
  if (currencies.size === 1) return [...currencies][0];
  return 'USD';
};

export const formatCurrencyValue = (value, currencyOrMarket = 'USD') => {
  const n = parseFloat(value) || 0;
  const currency = ['CN', 'HK', 'US'].includes(currencyOrMarket)
    ? currencyForMarket(currencyOrMarket)
    : currencyOrMarket;
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency || 'USD',
  }).format(n);
};
