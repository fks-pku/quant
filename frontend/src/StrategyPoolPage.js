import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import CIOAssessmentPanel from './CIOAssessmentPanel';
import StrategyCard from './StrategyCard';
import StrategyWeightBar from './StrategyWeightBar';
import StrategyReadmeModal from './StrategyReadmeModal';

const API_BASE = 'http://localhost:5000/api';

export default function StrategyPoolPage() {
  const [cioAssessment, setCioAssessment] = useState(null);
  const [strategyPool, setStrategyPool] = useState({ total_capital: 100000, strategies: [] });
  const [loading, setLoading] = useState(false);
  const [readme, setReadme] = useState(null);
  const [readmeOpen, setReadmeOpen] = useState(false);

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

  useEffect(() => {
    fetchCIO();
    fetchStrategyPool();
  }, [fetchCIO, fetchStrategyPool]);

  const handleRefresh = async () => {
    setLoading(true);
    try {
      await axios.post(`${API_BASE}/cio/refresh`);
      await fetchCIO();
    } catch (e) { console.error('CIO refresh error', e); }
    setLoading(false);
  };

  const handleReadme = async (strategyId) => {
    try {
      const res = await axios.get(`${API_BASE}/strategies/${strategyId}/readme`);
      setReadme(res.data);
      setReadmeOpen(true);
    } catch (e) { console.error('README fetch error', e); }
  };

  const weights = cioAssessment?.weights || {};

  return (
    <div className="strategy-pool-page">
      <div className="sp-header">
        <h2 className="sp-title">Strategy Pool Management</h2>
        <div className="sp-subtitle">
          Total Capital: ${strategyPool.total_capital?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
        </div>
      </div>

      <CIOAssessmentPanel assessment={cioAssessment} onRefresh={handleRefresh} loading={loading} />

      {Object.keys(weights).length > 0 && (
        <div className="sp-weights-section">
          <div className="sp-section-title">CIO Weight Allocation</div>
          <StrategyWeightBar weights={weights} />
        </div>
      )}

      <div className="sp-section-title">Strategy Cards</div>
      <div className="sp-cards-grid">
        {strategyPool.strategies.map((s) => (
          <StrategyCard key={s.id} strategy={s} onReadme={handleReadme} />
        ))}
      </div>

      <StrategyReadmeModal
        isOpen={readmeOpen}
        onClose={() => setReadmeOpen(false)}
        readme={readme}
      />
    </div>
  );
}
