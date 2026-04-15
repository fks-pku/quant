"""Test Futu API connection and check accounts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "system"))

from quant.execution.brokers.futu import FutuBroker

def main():
    print("=" * 50)
    print("Futu API Connection Test")
    print("=" * 50)
    
    broker = FutuBroker(
        host="127.0.0.1",
        port=11111,
        acc_list={},
        password="",
        trade_mode="SIMULATE",
    )
    
    print("\n1. Connecting to OpenD...")
    try:
        broker.connect()
        print("   SUCCESS: Connected to OpenD")
    except Exception as e:
        print(f"   FAILED: {e}")
        return
    
    print("\n2. Checking accounts...")
    if broker._acc_list:
        for acc in broker._acc_list:
            print(f"   - Account ID: {acc.get('acc_id', acc.get('acc_id'))}")
            acc_type = acc.get("acc_type", "UNKNOWN") if isinstance(acc, dict) else "UNKNOWN"
            print(f"     Type: {acc_type}")
    
    print("\n3. Trying to unlock (SIMULATE mode)...")
    if broker.unlock_trade(password="", trade_mode="SIMULATE"):
        print("   SUCCESS: Trading unlocked")
        broker._unlocked = True
    else:
        print("   NOTE: Unlock failed - this is normal if OpenD GUI requires manual unlock")
        print("   If using GUI OpenD, please click the unlock button in OpenD interface first")
        print("   If using headless OpenD, provide password and use REAL mode")
        broker._unlocked = True
    
    print("\n4. Getting account info...")
    try:
        acc_info = broker.get_account_info()
        print(f"   Account ID: {acc_info.account_id}")
        print(f"   Cash: {acc_info.cash:,.2f}")
        print(f"   Buying Power: {acc_info.buying_power:,.2f}")
        print(f"   Equity: {acc_info.equity:,.2f}")
    except Exception as e:
        print(f"   Error: {e}")
    
    print("\n5. Getting positions...")
    try:
        positions = broker.get_positions()
        if positions:
            for pos in positions:
                print(f"   {pos.symbol}: {pos.quantity} shares, avg_cost={pos.avg_cost:.2f}")
        else:
            print("   No positions")
    except Exception as e:
        print(f"   Error: {e}")
    
    print("\n6. Getting market snapshot for HK.00700 (Tencent)...")
    try:
        from quant.data.providers.futu import FutuProvider
        provider = FutuProvider(host="127.0.0.1", port=11111)
        provider.connect()
        quote = provider.get_quote("HK.00700")
        print(f"   Last Price: {quote.get('last', 0):.2f}")
        print(f"   Bid: {quote.get('bid', 0):.2f} x {quote.get('bid_size', 0)}")
        print(f"   Ask: {quote.get('ask', 0):.2f} x {quote.get('ask_size', 0)}")
        print(f"   Change: {quote.get('change', 0):.2f} ({quote.get('change_pct', 0):.2f}%)")
        provider.disconnect()
    except Exception as e:
        print(f"   Error: {e}")
    
    print("\n7. Getting orderbook for HK.00700...")
    try:
        from quant.data.providers.futu import FutuProvider
        provider = FutuProvider(host="127.0.0.1", port=11111)
        provider.connect()
        ob = provider.get_orderbook("HK.00700", depth=5)
        if ob.get("bid_prices"):
            print("   Bid Prices | Bid Sizes | Ask Prices | Ask Sizes")
            for i in range(min(5, len(ob["bid_prices"]))):
                print(f"   {ob['bid_prices'][i]:>10.2f} | {ob['bid_sizes'][i]:>9} | {ob['ask_prices'][i]:>10.2f} | {ob['ask_sizes'][i]:>9}")
        else:
            print("   No orderbook data")
        provider.disconnect()
    except Exception as e:
        print(f"   Error: {e}")
    
    print("\n" + "=" * 50)
    print("Connection test complete!")
    print("=" * 50)
    
    broker.disconnect()

if __name__ == "__main__":
    main()