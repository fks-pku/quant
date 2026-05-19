import { currencyForMarket, detectCurrency, detectMarket } from './market';

test('detects all six digit mainland securities as CN', () => {
  expect(detectMarket('600519')).toBe('CN');
  expect(detectMarket('510300')).toBe('CN');
  expect(detectMarket('159915')).toBe('CN');
  expect(detectMarket('512880')).toBe('CN');
});

test('detects HK and US symbols', () => {
  expect(detectMarket('HK.00700')).toBe('HK');
  expect(detectMarket('00700')).toBe('HK');
  expect(detectMarket('AAPL')).toBe('US');
  expect(detectMarket('US.SPY')).toBe('US');
});

test('detects A-share basket currency as CNY', () => {
  expect(detectCurrency('600519,510300,159915,512880')).toBe('CNY');
});

test('falls back to USD for mixed currency display', () => {
  expect(detectCurrency(['600519', 'AAPL'])).toBe('USD');
});

test('maps markets to display currencies', () => {
  expect(currencyForMarket('CN')).toBe('CNY');
  expect(currencyForMarket('HK')).toBe('HKD');
  expect(currencyForMarket('US')).toBe('USD');
});
