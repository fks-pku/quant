import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';

const API_BASE = 'http://localhost:5000/api';

const SOURCE_COLORS = {
  arxiv: '#ff6b6b',
  ssrn: '#ffaa00',
  default: '#00d4ff',
};

function ScoreBar({ value, max = 10, label, color = 'var(--accent-cyan)' }) {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div className="rs-score-row">
      <span className="rs-score-label">{label}</span>
      <div className="rs-score-track">
        <div className="rs-score-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="rs-score-value">{value.toFixed(1)}</span>
    </div>
  );
}

export default function ResearchPanel() {
  const [candidates, setCandidates] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [readme, setReadme] = useState(null);
  const [readmeLoading, setReadmeLoading] = useState(false);
  const [runningResearch, setRunningResearch] = useState(false);
  const [researchJobId, setResearchJobId] = useState(null);
  const [researchStatus, setResearchStatus] = useState(null);
  const [rejectReason, setRejectReason] = useState('');
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  const fetchCandidates = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/research/candidates`);
      setCandidates(res.data.candidates || []);
    } catch (e) {
      console.error('Fetch candidates error', e);
    }
  }, []);

  useEffect(() => {
    fetchCandidates();
    const interval = setInterval(fetchCandidates, 10000);
    return () => clearInterval(interval);
  }, [fetchCandidates]);

  const selected = candidates.find(c => c.id === selectedId);

  useEffect(() => {
    if (!selectedId) {
      setReadme(null);
      return;
    }
    setReadmeLoading(true);
    setReadme(null);
    axios.get(`${API_BASE}/strategies/${selectedId}/readme`)
      .then(res => setReadme(res.data))
      .catch(() => setReadme(null))
      .finally(() => setReadmeLoading(false));
  }, [selectedId]);

  useEffect(() => {
    if (!researchJobId) return;
    const poll = setInterval(async () => {
      try {
        const res = await axios.get(`${API_BASE}/research/status/${researchJobId}`);
        setResearchStatus(res.data);
        if (res.data.status === 'completed' || res.data.status === 'error') {
          clearInterval(poll);
          setRunningResearch(false);
          if (res.data.status === 'completed') {
            fetchCandidates();
          }
        }
      } catch {
        clearInterval(poll);
        setRunningResearch(false);
      }
    }, 2000);
    return () => clearInterval(poll);
  }, [researchJobId, fetchCandidates]);

  const handleRunResearch = async () => {
    setRunningResearch(true);
    setResearchStatus(null);
    try {
      const res = await axios.post(`${API_BASE}/research/run`, {});
      setResearchJobId(res.data.research_id);
      setResearchStatus({ status: 'running', research_id: res.data.research_id });
    } catch (e) {
      console.error('Run research error', e);
      setRunningResearch(false);
    }
  };

  const handlePromote = async () => {
    if (!selectedId) return;
    setActionLoading(true);
    try {
      await axios.post(`${API_BASE}/research/promote/${selectedId}`);
      setSelectedId(null);
      await fetchCandidates();
    } catch (e) {
      console.error('Promote error', e);
    }
    setActionLoading(false);
  };

  const handleReject = async () => {
    if (!selectedId) return;
    setActionLoading(true);
    try {
      await axios.post(`${API_BASE}/research/reject/${selectedId}`, { reason: rejectReason });
      setSelectedId(null);
      setRejectReason('');
      setShowRejectInput(false);
      await fetchCandidates();
    } catch (e) {
      console.error('Reject error', e);
    }
    setActionLoading(false);
  };

  const meta = selected?.research_meta || {};
  const sourceColor = SOURCE_COLORS[meta.source] || SOURCE_COLORS.default;

  return (
    <div className="rs-panel">
      <div className="rs-header">
        <div>
          <div className="rs-title">Research Candidates</div>
          <div className="rs-subtitle">
            {candidates.length} candidate{candidates.length !== 1 ? 's' : ''} discovered
          </div>
        </div>
        <button
          className="rs-run-btn"
          onClick={handleRunResearch}
          disabled={runningResearch}
        >
          {runningResearch ? (
            <>
              <span className="rs-spinner" />
              Researching...
            </>
          ) : (
            'Run Research'
          )}
        </button>
      </div>

      {researchStatus && (
        <div className={`rs-job-bar ${researchStatus.status === 'error' ? 'rs-job-error' : ''}`}>
          {researchStatus.status === 'running' && (
            <div className="rs-progress-header">
              <span className="rs-spinner" /> Scanning sources and evaluating strategies...
            </div>
          )}
          {researchStatus.status === 'completed' && researchStatus.result && (
            <div className="rs-progress-header">
              Done: {researchStatus.result.discovered} discovered, {researchStatus.result.evaluated} evaluated, {researchStatus.result.integrated} integrated
              {researchStatus.result.errors.length > 0 && ` (${researchStatus.result.errors.length} errors)`}
            </div>
          )}
          {researchStatus.status === 'error' && `Error: ${researchStatus.error}`}
          {researchStatus.result && researchStatus.result.log && researchStatus.result.log.length > 0 && (
            <div className="rs-log">
              {researchStatus.result.log.map((entry, i) => (
                <div key={i} className={`rs-log-entry rs-log-${entry.verdict}`}>
                  <span className="rs-log-phase">{entry.phase.toUpperCase()}</span>
                  <span className={`rs-log-verdict rs-log-verdict-${entry.verdict}`}>
                    {entry.verdict === 'pass' ? 'PASS' : entry.verdict === 'fail' ? 'FAIL' : entry.verdict === 'error' ? 'ERR' : entry.verdict === 'info' ? 'INFO' : entry.verdict}
                  </span>
                  <span className="rs-log-title" title={entry.title}>
                    {entry.title.length > 80 ? entry.title.slice(0, 77) + '...' : entry.title}
                  </span>
                  {entry.source && (
                    <span className="rs-log-source" style={{ color: SOURCE_COLORS[entry.source] || SOURCE_COLORS.default }}>
                      {entry.source}
                    </span>
                  )}
                  <span className="rs-log-reason">{entry.reason}</span>
                  {entry.scores && entry.scores.suitability != null && (
                    <span className="rs-log-scores">
                      S:{entry.scores.suitability.toFixed(1)} C:{entry.scores.complexity?.toFixed(1) || '-'} E:{entry.scores.edge != null ? (entry.scores.edge * 100).toFixed(1) + '%' : '-'}
                    </span>
                  )}
                  {entry.scores && entry.scores.sharpe != null && (
                    <span className="rs-log-scores">
                      Sharpe:{entry.scores.sharpe} DD:{entry.scores.max_dd}% WR:{entry.scores.win_rate}%
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="rs-body">
        <div className="rs-list">
          {candidates.length === 0 ? (
            <div className="rs-empty">
              <div className="rs-empty-icon">&#9881;</div>
              <div>No candidates yet. Run research to discover strategies.</div>
            </div>
          ) : (
            candidates.map(c => {
              const m = c.research_meta || {};
              const active = c.id === selectedId;
              return (
                <div
                  key={c.id}
                  className={`rs-card ${active ? 'rs-card-active' : ''}`}
                  onClick={() => { setSelectedId(c.id); setShowRejectInput(false); setRejectReason(''); }}
                >
                  <div className="rs-card-title">{c.name}</div>
                  <div className="rs-card-meta">
                    <span
                      className="rs-source-badge"
                      style={{ background: `${SOURCE_COLORS[m.source] || SOURCE_COLORS.default}22`, color: SOURCE_COLORS[m.source] || SOURCE_COLORS.default }}
                    >
                      {m.source || 'unknown'}
                    </span>
                    {m.suitability_score != null && (
                      <span className="rs-card-score">
                        {m.suitability_score.toFixed(1)}/10
                      </span>
                    )}
                  </div>
                  <div className="rs-card-desc">{c.description}</div>
                </div>
              );
            })
          )}
        </div>

        <div className="rs-detail">
          {!selected ? (
            <div className="rs-empty-detail">
              <div className="rs-empty-icon">&#128269;</div>
              <div>Select a candidate to view details</div>
            </div>
          ) : (
            <div className="rs-detail-inner">
              <div className="rs-detail-header">
                <h2 className="rs-detail-title">{selected.name}</h2>
                <span className="rs-source-badge rs-source-lg" style={{ background: `${sourceColor}22`, color: sourceColor }}>
                  {meta.source || 'unknown'}
                </span>
              </div>

              <div className="rs-detail-section">
                <div className="rs-section-label">Source</div>
                <div className="rs-source-grid">
                  {meta.source_url && (
                    <div className="rs-source-item">
                      <span className="rs-source-key">URL</span>
                      <a href={meta.source_url} target="_blank" rel="noopener noreferrer" className="rs-source-link">
                        {meta.source_url.length > 60 ? meta.source_url.slice(0, 57) + '...' : meta.source_url}
                      </a>
                    </div>
                  )}
                  <div className="rs-source-item">
                    <span className="rs-source-key">Data</span>
                    <span className="rs-source-val">{meta.data_requirement || '-'}</span>
                  </div>
                  <div className="rs-source-item">
                    <span className="rs-source-key">Daily</span>
                    <span className={`rs-source-val ${meta.daily_adaptable ? 'rs-yes' : 'rs-no'}`}>
                      {meta.daily_adaptable ? 'Yes' : 'No'}
                    </span>
                  </div>
                </div>
              </div>

              <div className="rs-detail-section">
                <div className="rs-section-label">Evaluation</div>
                <div className="rs-scores">
                  <ScoreBar label="Suitability" value={meta.suitability_score || 0} max={10} color="var(--accent-green)" />
                  <ScoreBar label="Complexity" value={meta.complexity_score || 0} max={10} color="var(--accent-amber)" />
                  <ScoreBar label="Est. Edge" value={(meta.estimated_edge || 0) * 100} max={5} color="var(--accent-cyan)" />
                </div>
              </div>

              <div className="rs-detail-section rs-readme-section">
                <div className="rs-section-label">Logic (README)</div>
                {readmeLoading ? (
                  <div className="rs-readme-loading">
                    <span className="rs-spinner" /> Loading...
                  </div>
                ) : readme && readme.content ? (
                  <div className="rs-readme-content">
                    <ReactMarkdown>{readme.content}</ReactMarkdown>
                  </div>
                ) : (
                  <div className="rs-readme-empty">README not available for this candidate.</div>
                )}
              </div>

              <div className="rs-actions">
                <button
                  className="rs-btn rs-btn-promote"
                  onClick={handlePromote}
                  disabled={actionLoading}
                >
                  Promote to Strategy
                </button>
                <div className="rs-reject-zone">
                  {!showRejectInput ? (
                    <button
                      className="rs-btn rs-btn-reject"
                      onClick={() => setShowRejectInput(true)}
                      disabled={actionLoading}
                    >
                      Reject
                    </button>
                  ) : (
                    <div className="rs-reject-form">
                      <input
                        className="rs-reject-input"
                        placeholder="Reason (optional)"
                        value={rejectReason}
                        onChange={e => setRejectReason(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handleReject(); if (e.key === 'Escape') setShowRejectInput(false); }}
                        autoFocus
                      />
                      <button className="rs-btn rs-btn-reject-confirm" onClick={handleReject} disabled={actionLoading}>Confirm</button>
                      <button className="rs-btn rs-btn-cancel" onClick={() => { setShowRejectInput(false); setRejectReason(''); }}>Cancel</button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
