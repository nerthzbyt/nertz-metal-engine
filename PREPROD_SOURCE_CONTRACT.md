# Pre-production architecture source contract

Pinned baseline: `main` at `732768ef5d0d4a3ff3f7374b6741e9c4434a6b83` (2026-06-23).
Preparation branch: `preprod/architecture-freeze-2026-09-17`.

## Purpose

This repository is retained as an **experimental/architectural source** for Nertz. Its useful patterns include monitoring, FastAPI control/observability, agent orchestration, historical metrics and deployment concepts.

It is not the authoritative execution/lifecycle baseline for current pre-production work.

## Allowed extraction targets

- observability/health patterns;
- API diagnostics and operator tooling;
- historical formula implementations for comparison;
- deterministic utilities that can be isolated and tested;
- deployment documentation;
- monitoring/alerting patterns.

## Explicitly non-authoritative areas

Do not transplant these areas into the current runtime without a dedicated audit and tests:

- wallet/account-state ownership;
- strategy inventory accounting;
- order ACK/fill semantics;
- TP/SL ownership or lifecycle behavior;
- environment routing;
- sizing and instrument filters;
- AI-generated live execution decisions;
- self-modifying/adaptive thresholds.

## Integration rule

The current execution authority is `nerthzbyt/RECUPERADO_` on its modernization/pre-production branches. Any useful component from this repository must enter that runtime through a narrow, reviewable change with source commit/path, deterministic tests and rollback information.

## Current disposition

**CONTROLLED ARCHITECTURE SOURCE — NOT EXECUTION BASELINE.**
