import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import PositionOverview from './PositionOverview';
import StrategyManagement from './StrategyManagement';

const API_BASE = 'http://localhost:5000/api';

export default function LiveTradingPage() {
  const [orders, setOrders] = useState([]);

  const fetchOrders = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/orders`);
      setOrders(res.data.orders || res.data || []);
    } catch (e) { console.error('Orders fetch error', e); }
  }, []);

  useEffect(() => {
    fetchOrders();
    const interval = setInterval(fetchOrders, 5000);
    return () => clearInterval(interval);
  }, [fetchOrders]);

  return (
    <div className="live-trading-page">
      <PositionOverview />
      <StrategyManagement />
      <div className="lt-orders-section">
        <div className="sp-section-title">Recent Orders</div>
        <div className="strategy-table-container">
          <table className="po-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Price</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {orders.length === 0 ? (
                <tr>
                  <td colSpan="6" className="empty-row">No recent orders</td>
                </tr>
              ) : (
                orders.slice(0, 10).map((o, i) => (
                  <tr key={i}>
                    <td>{o.time ? new Date(o.time).toLocaleString() : '-'}</td>
                    <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{o.symbol}</td>
                    <td style={{ color: o.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 600 }}>{o.side}</td>
                    <td>{o.quantity}</td>
                    <td>${o.price || '-'}</td>
                    <td style={{ color: 'var(--accent-green)' }}>{o.status || '-'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
