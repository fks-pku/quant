# Agent-Friendly MD Documentation System Redesign

## Problem

Current root `AGENTS.md` is 275 lines — exceeds the DevClaw ~190 line guideline. It mixes routing/constraints with deep reference material (commission models, import paths, deprecated paths), wasting Agent context window. Missing navigation layers (`docs/AGENTS.md`, `ARCHITECTURE.md`) force Agent to scan entire files. Two disconnected `docs/` directories reduce discoverability.

## Design Principles

1. **Progressive Disclosure** — Agent gets a map, not an encyclopedia. L0→L4, max 3 hops to any information.
2. **Repository as Single Source of Truth** — If it's not in a markdown file in the repo, it doesn't exist for the Agent.
3. **AGENTS.md ≠ README.md** — AGENTS.md is for Agent task routing and constraints; README.md is for human project introduction.

## Target L0-L4 Hierarchy

| Level | File | Lines | Purpose |
|-------|------|-------|---------|
| L0 Root Entry | `AGENTS.md` | ~150 | Task routing, knowledge navigation table, core constraints |
| L0 Root Entry | `ARCHITECTURE.md` | ~200 | Code map, module boundaries, architecture invariants |
| L1 Index | `docs/AGENTS.md` | ~80 | Categorized navigation for all reference docs |
| L2 Module Entry | `quant/*/AGENTS.md` (13 files) | 30-60 | Module-local constraints & commands, no global rule repetition |
| L3 Reference | `docs/reference/*.md` | — | Stable factual docs (data architecture, import paths, commission models) |
| L3 Plans | `docs/superpowers/specs/`, `plans/` | — | Design specs & implementation plans |
| L4 Skills | `.agents/skills/` | — | Agent-callable capability packages |

## What Changes

### Root AGENTS.md (275 → ~150 lines)

Keep:
- Architecture overview (hexagonal + event-driven, 3 lines)
- Directory structure (tree diagram)
- Dependency rules (8 iron rules)
- Feature index table
- Commands
- Key conventions (condensed)

Extract to dedicated files:
- Domain Layer (models/events/ports tables) → `ARCHITECTURE.md`
- Data Architecture (two-port diagram, providers, storage, DuckDB rules) → `docs/reference/data-architecture.md`
- Import Path Reference → `docs/reference/import-paths.md`
- Deprecated Paths → `docs/reference/deprecated-paths.md`
- Commission Models → `docs/reference/commission-models.md`
- Symbol Registry → `docs/reference/symbol-registry.md`
- Tushare Configuration → `docs/reference/data-architecture.md`
- CN Market Notes → `docs/reference/commission-models.md`

### New ARCHITECTURE.md (~200 lines)

Contains:
- Hexagonal architecture description
- Module boundary diagram
- Domain layer: models, events, ports (the tables currently in root AGENTS.md)
- Provider/Storage separation diagram
- Architecture invariants (zero-dependency domain, DI pattern, event-driven communication)

### New docs/AGENTS.md (~80 lines)

Navigation index for Agent to discover:
- Reference docs list with 1-line descriptions
- Guidance docs list
- Superpowers specs/plans directories
- Skill directory reference

### New Module AGENTS.md Files (5 new)

| File | Content |
|------|---------|
| `quant/domain/AGENTS.md` | Models/events/ports contracts, zero-dependency rule, `Any` return type rule |
| `quant/infrastructure/AGENTS.md` | Adapter patterns, DuckDB connection rules (writer vs reader), provider conventions |
| `quant/api/AGENTS.md` | Flask route conventions, read-only storage rule, state management |
| `quant/frontend/AGENTS.md` | React conventions, strategy list display logic, component patterns |
| `quant/shared/AGENTS.md` | Re-export compatibility layer purpose, no independent model classes rule |

### Existing Module AGENTS.md (6 files, polish only)

Keep `quant/features/*/AGENTS.md` as-is. Minor polish:
- Ensure each references root AGENTS.md for global rules
- Verify 职责/对外契约/依赖/不变量/修改守则/Known Pitfalls structure

### Directory Consolidation

```
BEFORE:
  quant/docs/md/*.md          (6 files, 403-1337 lines each, some outdated)
  docs/superpowers/specs/     (existing specs)
  docs/superpowers/plans/     (existing plans)

AFTER:
  docs/reference/             (extracted from AGENTS.md + relevant quant/docs/md/)
  docs/superpowers/specs/     (keep)
  docs/superpowers/plans/     (keep)
  quant/docs/                 (remove after migration)
  rule.md                     (remove, principles absorbed into root AGENTS.md)
```

Historical `quant/docs/md/` files disposition:
- `system_architecture_0419.md` → keep in `docs/reference/` as historical reference
- `technical_manual.md` → extract still-relevant parts, remove outdated
- `quant-system-prd.md` → keep in `docs/reference/` as product spec
- `framework_0416.md`, `v1.md`, `evaluate_v1.md` → remove (superseded by current architecture)

## Module AGENTS.md Template

Every module AGENTS.md follows this structure (from rule.md best practices):

```markdown
# <Module Name>

## 职责
(One sentence)

## 对外契约
(Public API signatures)

## 依赖
(What this module imports)

## 不变量
(Business rules that must always hold)

## 修改守则
(Which file to change for what)

## Known Pitfalls
(Lessons learned, sharp edges)
```

## Implementation Steps

1. Create `docs/reference/` directory and extract reference docs from root AGENTS.md
2. Write `ARCHITECTURE.md` from extracted domain/data architecture sections
3. Slim down root `AGENTS.md` to ~150 lines (routing + constraints + navigation)
4. Write `docs/AGENTS.md` navigation index
5. Write 5 new module AGENTS.md files (domain, infrastructure, api, frontend, shared)
6. Migrate relevant `quant/docs/md/` files to `docs/reference/`
7. Remove outdated docs and `rule.md`
8. Clean up `quant/docs/` directory

## Success Criteria

- Root `AGENTS.md` ≤ 160 lines
- `ARCHITECTURE.md` ≤ 220 lines
- `docs/AGENTS.md` ≤ 90 lines
- Every module AGENTS.md follows the template (职责/契约/依赖/不变量/守则/Pitfalls)
- Agent can navigate from root to any reference doc in ≤ 3 hops
- No duplicate information across files (single source per fact)
- All existing knowledge preserved (nothing lost, only reorganized)
