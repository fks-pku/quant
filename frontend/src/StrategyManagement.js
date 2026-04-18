import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import CIOAssessmentPanel from './CIOAssessmentPanel';
import StrategyDetailModal from './StrategyDetailModal';

const API_BASE = 'http://localhost:5000/api';

function PauseModal({ strategy, onConfirm, onCancel }) {
  const [flatten, setFlatten] = useState(true);
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-content pause-modal" onClick={e => e.stopPropagation()}>
        <h3>Pause {strategy.name}?</h3>
        <p className="pause-modal-desc">This strategy has open positions. Choose how to handle them:</p>
        <div className="pause-options">
          <label className={`pause-option ${flatten ? 'selected' : ''}`} onClick={() => setFlatten(true)}>
            <div className="pause-option-radio">
              {flatten && <span className="pause-option-dot" />}
            </div>
            <div>
              <div className="pause-option-title">Pause & Flatten Positions</div>
              <div className="pause-option-desc">Stop signals and close all positions at market price (Recommended)</div>
            </div>
          </label>
          <label className={`pause-option ${!flatten ? 'selected' : ''}`} onClick={() => setFlatten(false)}>
            <div className="pause-option-radio">
              {!flatten && <span className="pause-option-dot" />}
            </div>
            <div>
              <div className="pause-option-title">Pause Signals Only</div>
              <div className="pause-option-desc">Stop new signals but keep existing positions open</div>
            </div>
          </label>
        </div>
        <div className="pause-modal-actions">
          <button className="btn-cancel" onClick={onCancel}>Cancel</button>
          <button className="btn-pause-confirm" onClick={() => onConfirm(flatten)}>Pause Strategy</button>
        </div>
      </div>
    </div>
  );
}

function ConfirmAction({ message, onConfirm, onCancel }) {
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-content confirm-modal" onClick={e => e.stopPropagation()}>
        <h3>Confirm</h3>
        <p>{message}</p>
        <div className="pause-modal-actions">
          <button className="btn-cancel" onClick={onCancel}>Cancel</button>
          <button className="btn-danger" onClick={onConfirm}>Confirm</button>
        </div>
      </div>
    </div>
  );
}

const STATUS_LABELS = {
  active: { label: 'Active', color: 'var(--accent-green)', bg: 'var(--accent-green-dim)' },
  paused: { label: 'Paused', color: 'var(--accent-amber)', bg: 'rgba(255,170,0,0.15)' },
  retired: { label: 'Retired', color: 'var(--accent-red)', bg: 'var(--accent-red-dim)' },
};

export default function StrategyManagement({ onStrategySelect }) {
  const [cioAssessment, setCioAssessment] = useState(null);
  const [strategyPool, setStrategyPool] = useState({ total_capital: 100000, strategies: [] });
  const [allStrategies, setAllStrategies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [pauseModal, setPauseModal] = useState(null);
  const [confirmAction, setConfirmAction] = useState(null);
  const [showRetired, setShowRetired] = useState(false);

  const fetchCIO = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/cio/assessment`);
      setCioAssessment(res.data);
    } catch (e) { console.error('CIO fetch error', e); }
  }, []);

  const fetchStrategyPool = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategy-pool`);
      setStrategyPool(res.data);
    } catch (e) { console.error('Strategy pool fetch error', e); }
  }, []);

  const fetchAllStrategies = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategies`);
      setAllStrategies(res.data.strategies || []);
    } catch (e) { console.error('All strategies fetch error', e); }
  }, []);

  const refresh = async () => {
    await Promise.all([fetchStrategyPool(), fetchAllStrategies()]);
  };

  useEffect(() => {
    fetchCIO();
    fetchStrategyPool();
    fetchAllStrategies();
    const cioInterval = setInterval(fetchCIO, 60000);
    const poolInterval = setInterval(fetchStrategyPool, 5000);
    return () => {
      clearInterval(cioInterval);
      clearInterval(poolInterval);
    };
  }, [fetchCIO, fetchStrategyPool, fetchAllStrategies]);

  const handleRefresh = async () => {
    setLoading(true);
    try {
      await axios.post(`${API_BASE}/cio/refresh`);
      await fetchCIO();
    } catch (e) { console.error('CIO refresh error', e); }
    setLoading(false);
  };

  const handleSelectStrategy = async (strategyId) => {
    try {
      await axios.post(`${API_BASE}/strategies/select`, { strategy_id: strategyId });
      if (onStrategySelect) onStrategySelect(strategyId);
      await refresh();
    } catch (e) { console.error('Strategy select error', e); }
  };

  const handlePause = async (strategyId, flatten) => {
    try {
      await axios.post(`${API_BASE}/strategies/${strategyId}/pause`, { flatten });
      setPauseModal(null);
      await refresh();
    } catch (e) { console.error('Pause error', e); }
  };

  const handleResume = async (strategyId) => {
    try {
      await axios.post(`${API_BASE}/strategies/${strategyId}/resume`);
      await refresh();
    } catch (e) { console.error('Resume error', e); }
  };

  const handleRetire = async (strategyId) => {
    try {
      await axios.post(`${API_BASE}/strategies/${strategyId}/retire`);
      setConfirmAction(null);
      await refresh();
    } catch (e) { console.error('Retire error', e); }
  };

  const handleRestore = async (strategyId) => {
    try {
      await axios.post(`${API_BASE}/strategies/${strategyId}/restore`);
      await refresh();
    } catch (e) { console.error('Restore error', e); }
  };

  const handleDelete = async (strategyId) => {
    try {
      await axios.delete(`${API_BASE}/strategies/${strategyId}`);
      setConfirmAction(null);
      await refresh();
    } catch (e) { console.error('Delete error', e); }
  };

  const handleRowClick = (strategy) => {
    setSelectedStrategy(strategy);
    setDetailOpen(true);
  };

  const weights = cioAssessment?.weights || {};
  const activatedStrategies = strategyPool.strategies
    .filter(s => s.enabled)
    .sort((a, b) => (b.allocated_capital || 0) - (a.allocated_capital || 0));

  const activeStrategies = allStrategies.filter(s => s.status === 'active');
  const pausedStrategies = allStrategies.filter(s => s.status === 'paused');
  const retiredStrategies = allStrategies.filter(s => s.status === 'retired');

  return (
    <div className="strategy-management">
      {pauseModal && (
        <PauseModal
          strategy={pauseModal}
          onConfirm={(flatten) => handlePause(pauseModal.id, flatten)}
          onCancel={() => setPauseModal(null)}
        />
      )}
      {confirmAction && (
        <ConfirmAction
          message={confirmAction.message}
          onConfirm={confirmAction.onConfirm}
          onCancel={() => setConfirmAction(null)}
        />
      )}

      <CIOAssessmentPanel assessment={cioAssessment} onRefresh={handleRefresh} loading={loading} />

      <div className="sm-strategy-section">
        <div className="sm-section-header">
          <div className="sp-section-title">Strategies</div>
        </div>

        <div className="strategy-table-container">
          <table className="strategy-table">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Status</th>
                <th>Weight</th>
                <th>Allocated</th>
                <th>P&L</th>
                <th>Sharpe</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {activeStrategies.length === 0 && pausedStrategies.length === 0 ? (
                <tr>
                  <td colSpan="7" className="empty-row">
                    No strategies. Use the backtest page to explore strategies.
                  </td>
                </tr>
              ) : (
                <>
                  {activeStrategies.map((s) => {
                    const poolData = activatedStrategies.find(p => p.id === s.id);
                    return (
                      <tr key={s.id} className="strategy-row" onClick={() => handleRowClick(s)}>
                        <td className="strategy-name-cell">{s.name}</td>
                        <td>
                          <span className="status-badge active">Active</span>
                        </td>
                        <td>
                          {poolData ? (
                            <div className="weight-cell">
                              <div className="weight-bar-mini">
                                <div className="weight-bar-mini-fill" style={{ width: `${(poolData.weight * 100).toFixed(1)}%` }} />
                              </div>
                              <span>{(poolData.weight * 100).toFixed(1)}%</span>
                            </div>
                          ) : <span className="text-muted">-</span>}
                        </td>
                        <td className="capital-cell">
                          ${poolData?.allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}
                        </td>
                        <td className={`pnl-cell ${poolData?.current_pnl >= 0 ? 'positive' : 'negative'}`}>
                          {poolData ? `${poolData.current_pnl >= 0 ? '+' : ''}$${poolData.current_pnl?.toFixed(2) || '0.00'}` : '-'}
                        </td>
                        <td className="sharpe-cell">{s.backtest?.test_sharpe?.toFixed(2) || '-'}</td>
                        <td className="action-cell" onClick={e => e.stopPropagation()}>
                          <button className="btn-action pause" onClick={() => setPauseModal(s)} title="Pause strategy">Pause</button>
                          <button className="btn-action retire" onClick={() => setConfirmAction({
                            message: `Retire "${s.name}"? It will stop trading but can be restored later.`,
                            onConfirm: () => handleRetire(s.id),
                          })} title="Retire strategy">Retire</button>
                        </td>
                      </tr>
                    );
                  })}
                  {pausedStrategies.map((s) => (
                    <tr key={s.id} className="strategy-row paused-row" onClick={() => handleRowClick(s)}>
                      <td className="strategy-name-cell">{s.name}</td>
                      <td><span className="status-badge paused">Paused</span></td>
                      <td><span className="text-muted">-</span></td>
                      <td><span className="text-muted">-</span></td>
                      <td><span className="text-muted">-</span></td>
                      <td className="sharpe-cell">{s.backtest?.test_sharpe?.toFixed(2) || '-'}</td>
                      <td className="action-cell" onClick={e => e.stopPropagation()}>
                        <button className="btn-action resume" onClick={() => handleResume(s.id)} title="Resume strategy">Resume</button>
                        <button className="btn-action retire" onClick={() => setConfirmAction({
                          message: `Retire "${s.name}"? It will stop trading but can be restored later.`,
                          onConfirm: () => handleRetire(s.id),
                        })} title="Retire strategy">Retire</button>
                      </td>
                    </tr>
                  ))}
                </>
              )}
            </tbody>
          </table>
        </div>

        {retiredStrategies.length > 0 && (
          <div className="retired-section">
            <div className="retired-toggle" onClick={() => setShowRetired(!showRetired)}>
              <span>{showRetired ? '▾' : '▸'} Retired Strategies ({retiredStrategies.length})</span>
            </div>
            {showRetired && (
              <div className="retired-list">
                {retiredStrategies.map(s => (
                  <div key={s.id} className="retired-row">
                    <div className="retired-info">
                      <span className="retired-name">{s.name}</span>
                      <span className="status-badge retired">Retired</span>
                    </div>
                    <div className="retired-actions">
                      <button className="btn-action restore" onClick={() => handleRestore(s.id)}>Restore</button>
                      <button className="btn-action delete" onClick={() => setConfirmAction({
                        message: `Permanently delete "${s.name}"? This cannot be undone. All related backtest data will be removed.`,
                        onConfirm: () => handleDelete(s.id),
                      })}>Delete</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <StrategyDetailModal
        isOpen={detailOpen}
        onClose={() => { setDetailOpen(false); setSelectedStrategy(null); }}
        strategy={selectedStrategy}
      />
    </div>
  );
}
