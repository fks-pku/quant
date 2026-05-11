import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';

const API_BASE = 'http://localhost:5000/api';
const API_ORIGIN = API_BASE.replace(/\/api$/, '');

const SOURCE_COLORS = {
  arxiv: '#ff6b6b',
  ssrn: '#ffaa00',
  springer: '#00ff88',
  nber: '#9be7ff',
  blog: '#d7ff66',
  default: '#00d4ff',
};

const IDEA_FILTERS = ['discovered', 'research_queue', 'candidate', 'rejected', 'all'];

function fmtScore(value) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toFixed(1) : '0.0';
}

function fmtNum(value, digits = 1) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : '-';
}

function fmtElapsed(seconds) {
  const n = Number(seconds || 0);
  if (!Number.isFinite(n) || n <= 0) return '0s';
  if (n < 60) return `${Math.floor(n)}s`;
  const minutes = Math.floor(n / 60);
  const rest = Math.floor(n % 60);
  return `${minutes}m ${rest}s`;
}

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
  const [ideas, setIdeas] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedIdeaId, setSelectedIdeaId] = useState(null);
  const [ideaFilter, setIdeaFilter] = useState('discovered');
  const [readme, setReadme] = useState(null);
  const [readmeLoading, setReadmeLoading] = useState(false);
  const [schedule, setSchedule] = useState(null);
  const [latestReport, setLatestReport] = useState(null);
  const [runningResearch, setRunningResearch] = useState(false);
  const [researchJobId, setResearchJobId] = useState(null);
  const [researchStatus, setResearchStatus] = useState(null);
  const [rejectReason, setRejectReason] = useState('');
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [researchMode, setResearchMode] = useState(null);

  const fetchCandidates = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/research/candidates`);
      setCandidates(res.data.candidates || []);
    } catch (e) {
      console.error('Fetch candidates error', e);
    }
  }, []);

  const fetchIdeas = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/research/ideas`);
      setIdeas(res.data.ideas || []);
    } catch (e) {
      console.error('Fetch idea bank error', e);
    }
  }, []);

  const reportHref = useCallback((report) => {
    if (!report?.url) return null;
    return report.url.startsWith('http') ? report.url : `${API_ORIGIN}${report.url}`;
  }, []);

  const fetchResearchMeta = useCallback(async () => {
    try {
      const [scheduleRes, reportRes] = await Promise.all([
        axios.get(`${API_BASE}/research/schedule`),
        axios.get(`${API_BASE}/research/report`),
      ]);
      setSchedule(scheduleRes.data || null);
      setLatestReport(reportRes.data.report || null);
    } catch (e) {
      console.error('Fetch research metadata error', e);
    }
  }, []);

  useEffect(() => {
    fetchCandidates();
    fetchIdeas();
    fetchResearchMeta();
    const interval = setInterval(() => {
      fetchCandidates();
      fetchIdeas();
    }, 10000);
    return () => clearInterval(interval);
  }, [fetchCandidates, fetchIdeas, fetchResearchMeta]);

  const selected = candidates.find(c => c.id === selectedId);
  const visibleIdeas = ideaFilter === 'all' ? ideas : ideas.filter(i => i.status === ideaFilter);
  const selectedIdea = ideas.find(i => i.idea_id === selectedIdeaId);
  const ideaCounts = ideas.reduce((acc, idea) => {
    acc[idea.status] = (acc[idea.status] || 0) + 1;
    return acc;
  }, {});

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
          setResearchMode(null);
          if (res.data.status === 'completed') {
            if (res.data.report) {
              setLatestReport(res.data.report);
            } else {
              fetchResearchMeta();
            }
            fetchCandidates();
            fetchIdeas();
          }
        }
      } catch (e) {
        clearInterval(poll);
        setRunningResearch(false);
        setResearchMode(null);
        setResearchStatus({
          status: 'error',
          error: e?.message || 'Polling research status failed',
        });
      }
    }, 2000);
    return () => clearInterval(poll);
  }, [researchJobId, fetchCandidates, fetchIdeas, fetchResearchMeta]);

  const handleRunResearch = async () => {
    setRunningResearch(true);
    setResearchMode('full');
    setResearchStatus(null);
    try {
      const res = await axios.post(`${API_BASE}/research/run`, { mode: 'full' });
      setResearchJobId(res.data.research_id);
      setResearchStatus({ status: 'running', research_id: res.data.research_id, mode: 'full' });
    } catch (e) {
      console.error('Run research error', e);
      setRunningResearch(false);
      setResearchMode(null);
    }
  };

  const handleDiscoverIdeas = async () => {
    setRunningResearch(true);
    setResearchMode('discover');
    setResearchStatus(null);
    try {
      const res = await axios.post(`${API_BASE}/research/run`, { mode: 'discover' });
      setResearchJobId(res.data.research_id);
      setResearchStatus({ status: 'running', research_id: res.data.research_id, mode: 'discover' });
    } catch (e) {
      console.error('Discover ideas error', e);
      setRunningResearch(false);
      setResearchMode(null);
    }
  };

  const handleResearchIdea = async (idea = selectedIdea) => {
    if (!idea?.idea_id) return;
    setSelectedIdeaId(idea.idea_id);
    setRunningResearch(true);
    setResearchMode('formal');
    setResearchStatus(null);
    try {
      const res = await axios.post(`${API_BASE}/research/run`, {
        mode: 'formal',
        idea_ids: [idea.idea_id],
        idea_statuses: [idea.status || 'discovered'],
        max_ideas: 1,
      });
      setResearchJobId(res.data.research_id);
      setResearchStatus({ status: 'running', research_id: res.data.research_id, mode: 'formal' });
    } catch (e) {
      console.error('Run formal research error', e);
      setRunningResearch(false);
      setResearchMode(null);
    }
  };

  const handleOpenReport = () => {
    const href = reportHref(latestReport);
    if (href) {
      window.open(href, '_blank', 'noopener,noreferrer');
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
  const latestReportHref = reportHref(latestReport);
  const lastProgress = researchStatus?.result?.log?.length
    ? researchStatus.result.log[researchStatus.result.log.length - 1]
    : null;
  const activeRunningLabel = researchMode === 'discover'
    ? 'Discovering...'
    : researchMode === 'formal'
      ? 'Researching idea...'
      : 'Researching...';

  return (
    <div className="rs-panel">
      <div className="rs-header">
        <div>
          <div className="rs-title">Research Pipeline</div>
          <div className="rs-subtitle">
            {candidates.length} candidate{candidates.length !== 1 ? 's' : ''} tracked
          </div>
        </div>
        <div className="rs-header-actions">
          <div className={`rs-schedule-pill ${schedule?.auto_run ? 'rs-schedule-on' : ''}`}>
            <span>Schedule</span>
            <b>{schedule?.auto_run ? `Every ${schedule.interval_days}d` : 'Paused'}</b>
          </div>
          <button
            className="rs-run-btn"
            onClick={handleDiscoverIdeas}
            disabled={runningResearch}
          >
            {runningResearch && researchMode === 'discover' ? (
              <>
                <span className="rs-spinner" />
                Discovering...
              </>
            ) : (
              'Discover Ideas'
            )}
          </button>
          <button
            className="rs-run-btn rs-run-btn-formal"
            onClick={() => handleResearchIdea()}
            disabled={runningResearch || !selectedIdea}
          >
            {runningResearch && researchMode === 'formal' ? (
              <>
                <span className="rs-spinner" />
                Running...
              </>
            ) : (
              'Research Selected'
            )}
          </button>
          <button
            className="rs-run-btn rs-run-btn-muted"
            onClick={handleRunResearch}
            disabled={runningResearch}
          >
            {runningResearch && researchMode === 'full' ? (
              <>
                <span className="rs-spinner" />
                Researching...
              </>
            ) : (
              'Run End-to-End'
            )}
          </button>
        </div>
      </div>

      <div className="rs-idea-pool">
        <div className="rs-idea-head">
          <div>
            <div className="rs-section-heading">Idea Bank</div>
            <div className="rs-subtitle">
              {ideas.length} local ideas, {ideaCounts.discovered || 0} waiting
            </div>
          </div>
          <div className="rs-idea-filters">
            {IDEA_FILTERS.map(filter => (
              <button
                key={filter}
                className={`rs-filter-btn ${ideaFilter === filter ? 'rs-filter-active' : ''}`}
                onClick={() => setIdeaFilter(filter)}
              >
                {filter}
                {filter !== 'all' && <span>{ideaCounts[filter] || 0}</span>}
              </button>
            ))}
          </div>
        </div>
        <div className="rs-idea-table">
          {visibleIdeas.length === 0 ? (
            <div className="rs-empty-row">No ideas in this status.</div>
          ) : (
            visibleIdeas.map(idea => {
              const quality = idea.metadata?.discovery_quality || {};
              const color = SOURCE_COLORS[idea.source] || SOURCE_COLORS.default;
              const active = selectedIdeaId === idea.idea_id;
              return (
                <div
                  key={idea.idea_id}
                  className={`rs-idea-row ${active ? 'rs-idea-active' : ''}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedIdeaId(idea.idea_id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') setSelectedIdeaId(idea.idea_id);
                  }}
                >
                  <span className="rs-idea-main">
                    <b>{idea.title}</b>
                    <em>{idea.reason || 'queued'}</em>
                  </span>
                  <span className="rs-source-badge" style={{ background: `${color}22`, color }}>
                    {idea.source || 'unknown'}
                  </span>
                  <span className={`rs-idea-status rs-idea-status-${idea.status || 'unknown'}`}>
                    {idea.status || 'unknown'}
                  </span>
                  <span className="rs-idea-date">{idea.published_date || 'n/a'}</span>
                  <span className="rs-idea-score">{fmtScore(quality.score)}/10</span>
                  <button
                    type="button"
                    className="rs-idea-action"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleResearchIdea(idea);
                    }}
                    disabled={runningResearch}
                  >
                    Research
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>

      <div className="rs-pipeline-strip">
        <div className="rs-pipeline-step">
          <span>01</span>
          <b>Idea Scout</b>
          <em>{(schedule?.sources || []).join(' / ') || 'arxiv / ssrn / nber / blog'}</em>
        </div>
        <div className="rs-pipeline-step">
          <span>02</span>
          <b>Signal Gate</b>
          <em>admission, HFQ, IC/FDR</em>
        </div>
        <div className="rs-pipeline-step">
          <span>03</span>
          <b>Framework Test</b>
          <em>costs, limits, significance</em>
        </div>
        <div className={`rs-pipeline-step ${latestReport?.available ? 'rs-pipeline-ready' : ''}`}>
          <span>04</span>
          <b>HTML Report</b>
          {latestReport?.available ? (
            <button className="rs-inline-link" onClick={handleOpenReport}>Open latest</button>
          ) : (
            <em>waiting for first run</em>
          )}
        </div>
      </div>

      {researchStatus && (
        <div className={`rs-job-bar ${researchStatus.status === 'error' ? 'rs-job-error' : ''}`}>
          {researchStatus.status === 'running' && (
            <div className="rs-progress-header rs-progress-running">
              <span>
                <span className="rs-spinner" /> {activeRunningLabel}
                {researchStatus.elapsed_seconds != null && (
                  <em className="rs-progress-elapsed">{fmtElapsed(researchStatus.elapsed_seconds)}</em>
                )}
              </span>
              {lastProgress && (
                <span className="rs-progress-step">
                  {lastProgress.phase}: {lastProgress.reason}
                </span>
              )}
            </div>
          )}
          {researchStatus.status === 'completed' && researchStatus.result && (
            <div className="rs-progress-header rs-progress-complete">
              <span>
                Done: {researchStatus.result.discovered} discovered, {researchStatus.result.evaluated} evaluated, {researchStatus.result.integrated} integrated
                {researchStatus.result.errors.length > 0 && ` (${researchStatus.result.errors.length} errors)`}
              </span>
              {latestReportHref && (
                <a className="rs-report-link" href={latestReportHref} target="_blank" rel="noopener noreferrer">
                  Open HTML report
                </a>
              )}
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
                      S:{fmtNum(entry.scores.suitability)} C:{fmtNum(entry.scores.complexity)} E:{entry.scores.edge != null ? fmtNum(Number(entry.scores.edge) * 100) + '%' : '-'}
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
