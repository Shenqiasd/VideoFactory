# Project

## Purpose
This repository is a production-oriented video processing and publishing system. It covers task orchestration, translation and dubbing, secondary content generation, and multi-platform distribution.

## Working Principles
- Prefer the documented workflow under `workflow/`.
- Keep code changes scoped and verifiable.
- Do not commit local machine configuration, credentials, or personal agent settings.
- Treat `FlowPilot/`, `.flowpilot/`, `.claude/` local config, and other collaboration tooling as local development aids unless explicitly promoted into project documentation.

## Engineering Expectations
- Read the existing codepath before changing behavior.
- Preserve backward compatibility where possible, especially for task APIs and persisted task data.
- Add or update tests for scheduler behavior, API contracts, and page interactions when changing publish or task flows.
- Record meaningful project-level changes in `workflow/progress.md`, `workflow/architecture.md`, and release notes when appropriate.

## Key References
- `workflow/README.md`
- `workflow/COLLABORATION_GUIDE.md`
- `workflow/architecture.md`
- `workflow/testing-playbook.md`
