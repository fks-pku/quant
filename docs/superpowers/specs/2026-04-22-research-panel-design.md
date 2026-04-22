# Research Panel Design

## Summary

Add a top-level RESEARCH tab to the frontend that displays strategy candidates discovered by the quant research pipeline. Users can browse candidates, view source/evaluation details and rendered README markdown, and promote or reject candidates.

## Architecture

New React component `ResearchPanel.js` rendered as a third tab in `App.js`.

### Layout

Two-pane horizontal layout:

- **Left pane (300px):** Scrollable list of candidate cards showing name, source badge, and suitability score bar.
- **Right pane (flex):** Detail view for the selected candidate with three sections: Source info, Evaluation scores, and rendered README markdown. Promote/Reject action buttons at the bottom.

### Data Flow

1. `GET /api/research/candidates` — list candidates with `research_meta`
2. Click candidate — `GET /api/strategies/<id>/readme` for rendered markdown
3. Source/scores rendered from `research_meta` in candidate data
4. Promote calls `POST /api/research/promote/<id>`
5. Reject calls `POST /api/research/reject/<id>` with optional reason

### API Endpoints (all existing)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/research/candidates` | GET | List candidates |
| `/api/strategies/<id>/readme` | GET | Get README markdown |
| `/api/research/promote/<id>` | POST | Promote to paused |
| `/api/research/reject/<id>` | POST | Reject candidate |
| `/api/research/run` | POST | Trigger new research run |
| `/api/research/status/<id>` | GET | Poll research job status |

### candidate data shape

```json
{
  "id": "momentum_reversal_on_high_volume",
  "name": "Momentum Reversal on High Volume",
  "description": "...",
  "status": "candidate",
  "research_meta": {
    "source": "arxiv",
    "source_url": "https://arxiv.org/abs/...",
    "suitability_score": 7.5,
    "complexity_score": 4.0,
    "data_requirement": "daily OHLCV",
    "daily_adaptable": true,
    "estimated_edge": 0.012
  }
}
```

## Files

| File | Action | Description |
|------|--------|-------------|
| `frontend/src/ResearchPanel.js` | Create | New component: candidate list + detail view |
| `frontend/src/App.js` | Modify | Add RESEARCH tab button + conditional render |
| `frontend/src/App.css` | Modify | Add `.rs-*` styles matching existing dark theme |

## Components

### ResearchPanel

State:
- `candidates[]` — from API
- `selectedId` — which candidate is selected
- `readme` — markdown content of selected candidate
- `loading` — fetch state
- `runningResearch` — whether a research run is in progress
- `researchJobId` — current running job ID
- `rejectReason` — text input for rejection

Sections:
1. Header with title + "Run Research" button
2. Left pane: candidate cards list
3. Right pane: detail with Source, Scores, README tabs/sections, actions

### Styling

Follows existing BEM-ish `.rs-*` prefix pattern with the dark navy/cyan/green theme. Uses CSS custom properties from `App.css`.

## Implementation Steps

1. Create `ResearchPanel.js` with full component
2. Add CSS styles to `App.css`
3. Add RESEARCH tab to `App.js`
4. Test in browser
