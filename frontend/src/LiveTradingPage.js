import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import AccountOverview from './AccountOverview';
import StrategyManagement from './StrategyManagement';
import StrategyPositionCards from './StrategyPositionCards';

const API_BASE = 'http://localhost:5000/api';

export default function LiveTradingPage({ broker, systemRunning }) {
  const [orders, setOrders] = useState([]);

  const fetchOrders = useCallback(async () => {
    try {
      if (broker === 'futu') {
        if (!systemRunning) { setOrders([]); return; }
        const res = await axios.get(`${API_BASE}/futu/orders`);
        if (res.data && !res.data.error) { setOrders(res.data.orders || []); return; }
      } else {
        const res = await axios.get(`${API_BASE}/orders`);
        setOrders(res.data.orders || res.data || []);
      }
    } catch { /* ignore */ }
  }, [broker, systemRunning]);

  useEffect(() => {
    fetchOrders();
    const interval = setInterval(fetchOrders, 5000);
    return () => clearInterval(interval);
  }, [fetchOrders]);

  const isFutu = broker === 'futu';
  const showData = !isFutu || systemRunning;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <section>
        <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--accent-cyan)', marginBottom: '10px', letterSpacing: '0.5px' }}>账户总览</div>
        <AccountOverview broker={broker} futuReady={systemRunning} />
      </section>

      {showData && (
        <section>
          <StrategyPositionCards broker={broker} futuReady={systemRunning} />
        </section>
      )}

      {showData && (
        <section>
          <StrategyManagement />
        </section>
      )}

      {showData && (
        <section>
          <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--accent-cyan)', marginBottom: '10px', letterSpacing: '0.5px' }}>近期交易记录</div>
          <div style={{ background: 'var(--bg-secondary)', borderRadius: '10px', padding: '14px' }}>
            <table className="po-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>策略</th>
                  <th>标的</th>
                  <th>方向</th>
                  <th>数量</th>
                  <th>成交</th>
                  <th>价格</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {orders.length === 0 ? (
                  <tr><td colSpan="8" className="empty-row">暂无交易记录</td></tr>
                ) : (
                  orders.slice(0, 30).map((o, i) => {
                    const strategyName = o.strategy || (isFutu ? '手动交易' : '-');
                    const isManual = strategyName === '手动交易' || strategyName === 'default';
                    return (
                      <tr key={i}>
                        <td>{o.time ? new Date(o.time).toLocaleString() : '-'}</td>
                        <td>
                          <span style={{
                            fontSize: '11px', padding: '1px 6px', borderRadius: '3px',
                            background: isManual ? 'rgba(255,255,255,0.06)' : 'rgba(0,200,255,0.1)',
                            color: isManual ? 'var(--text-muted)' : 'var(--accent-cyan)',
                          }}>
                            {strategyName}
                          </span>
                        </td>
                        <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{o.symbol}</td>
                        <td style={{ color: o.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 600 }}>{o.side === 'BUY' ? '买入' : '卖出'}</td>
                        <td>{o.quantity}</td>
                        <td>{o.filled_qty || '-'}</td>
                        <td>{o.price || '-'}</td>
                        <td style={{ color: o.status === 'FILLED' || o.status === 'DEAL' ? 'var(--accent-green)' : 'var(--accent-amber)' }}>{o.status || '-'}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
