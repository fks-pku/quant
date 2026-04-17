import React, { useEffect } from 'react';
import ReactMarkdown from 'react-markdown';

export default function StrategyReadmeModal({ isOpen, onClose, readme }) {
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  if (!isOpen || !readme) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{readme.strategy_name} — README</div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <ReactMarkdown>{readme.content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
