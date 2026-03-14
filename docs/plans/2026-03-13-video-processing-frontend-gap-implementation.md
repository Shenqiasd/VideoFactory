# Video Processing Frontend Gap Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make VideoFactory’s existing video-processing capabilities visible and operable in the frontend by adding creation configuration, creation result rendering, and creation review actions.

**Architecture:** Keep `scope` as the user-facing quick preset, but move all detailed video-processing controls into `creation_config`. Add a frontend-friendly creation summary view so task detail can render selected highlight segments, crop/fallback state, platform variants, and review actions without rebuilding relationships ad hoc from raw artifacts.

**Tech Stack:** FastAPI, Jinja2, Alpine.js / vanilla JavaScript, pytest, Playwright

---

### Task 1: Lock API Contract For Creation Config Input

**Files:**
- Modify: `api/routes/tasks.py`
- Modify: `tests/web/test_api_contract.py`

**Step 1: Write the failing test**

- Add an API contract test that posts to `POST /api/tasks/` with a populated `creation_config` payload.
- Assert the created task detail returns the same normalized `creation_config` fields.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py -k creation_config`

**Step 3: Write minimal implementation**

- Ensure `TaskCreateRequest.creation_config` is fully normalized and persisted.
- If needed, extend detail serialization to always expose normalized values.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py -k creation_config`

### Task 2: Add Creation Summary API

**Files:**
- Modify: `api/routes/tasks.py`
- Test: `tests/web/test_api_contract.py`

**Step 1: Write the failing test**

- Add a test for `GET /api/tasks/{task_id}/creation-summary`.
- Assert the response groups `creation_config`, `creation_state`, `creation_status`, short-clip variants, and cover products into a stable shape.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py -k creation_summary`

**Step 3: Write minimal implementation**

- Add a route that builds:
  - `config`
  - `status`
  - `segments`
  - `variants_by_segment`
  - `covers`
  - `actions`
- Reuse existing task data as the source of truth; do not invent a second persistence model.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py -k creation_summary`

### Task 3: Render Creation Config In New Task Page

**Files:**
- Modify: `web/templates/new_task.html`
- Modify: `tests/web/test_pages_http.py`

**Step 1: Write the failing test**

- Add a page test asserting `/tasks/new` contains creation configuration fields for clip count, duration range, crop mode, review mode, platforms, BGM, intro/outro, and transition.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/web/test_pages_http.py -k new_task`

**Step 3: Write minimal implementation**

- Extend the new task page with a creation configuration panel.
- Keep `scope` as the high-level preset and bind panel defaults accordingly.
- Add a compact summary card describing what will be generated.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/web/test_pages_http.py -k new_task`

### Task 4: Switch New Task Submission To JSON Creation Payload

**Files:**
- Modify: `web/templates/new_task.html`
- Modify: `api/routes/tasks.py`
- Test: `tests/web/test_api_contract.py`

**Step 1: Write the failing test**

- Add a contract test for the new page submission path that exercises JSON task creation with `creation_config`.
- Preserve existing HTMX/browser form redirect tests for compatibility routes.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py -k 'task_create and creation'`

**Step 3: Write minimal implementation**

- Update the page script to submit to `POST /api/tasks/` with JSON.
- Keep compatibility behavior intact for existing `/api/tasks/create` consumers.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py -k 'task_create and creation'`

### Task 5: Add Creation Result Section To Task Detail

**Files:**
- Modify: `web/templates/task_detail.html`
- Modify: `tests/web/test_pages_http.py`

**Step 1: Write the failing test**

- Add a page test asserting task detail includes a creation summary area with segment list, crop mode/fallback info, and variant grouping placeholders.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/web/test_pages_http.py -k task_detail`

**Step 3: Write minimal implementation**

- Add a “创作结果” section.
- Fetch `creation-summary` from the client-side script.
- Render segment cards and grouped platform variants.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/web/test_pages_http.py -k task_detail`

### Task 6: Add Creation Review Actions To Task Detail

**Files:**
- Modify: `web/templates/task_detail.html`
- Modify: `tests/web/test_api_contract.py`
- Modify: `tests/e2e/test_frontend_playwright.py`

**Step 1: Write the failing test**

- Add a page/API/E2E path asserting a pending-review task renders approve/reject controls and updates status after the action.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py tests/e2e/test_frontend_playwright.py -k 'review and factory'`

**Step 3: Write minimal implementation**

- Add approve/reject buttons bound to `/api/factory/review/approve` and `/api/factory/review/reject`.
- Refresh the task detail state after mutation.
- Show clear success/error messages and rejected reasons.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py tests/e2e/test_frontend_playwright.py -k 'review and factory'`

### Task 7: Surface Creation Status In Task Lists

**Files:**
- Modify: `web/templates/partials/task_list.html`
- Modify: `web/templates/partials/recent_completed.html`
- Modify: `api/routes/pages.py`
- Test: `tests/web/test_pages_http.py`

**Step 1: Write the failing test**

- Add page tests asserting task list rows show compact creation badges such as clip count, pending review, and cover availability where applicable.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/web/test_pages_http.py -k 'task_list or recent_completed'`

**Step 3: Write minimal implementation**

- Enrich list context with creation summary fields.
- Render lightweight badges without turning list rows into full detail cards.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/web/test_pages_http.py -k 'task_list or recent_completed'`

### Task 8: Make Highlight Strategy Honest

**Files:**
- Modify: `src/creation/pipeline.py`
- Modify: `src/creation/highlight_detector.py`
- Test: `tests/test_creation_pipeline.py` or `tests/test_highlight_detector.py`

**Step 1: Write the failing test**

- Add a unit test proving `highlight_strategy` changes the detection path or normalization result.
- If strategy support is intentionally deferred, replace the UI exposure and keep the field internal.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/test_creation_pipeline.py tests/test_highlight_detector.py -k strategy`

**Step 3: Write minimal implementation**

- Either wire `highlight_strategy` into the creation pipeline behavior, or explicitly remove it from user-facing controls until supported.
- Keep behavior deterministic and fallback-safe.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/test_creation_pipeline.py tests/test_highlight_detector.py -k strategy`

### Task 9: Connect Vertical Cover Output

**Files:**
- Modify: `src/factory/cover.py`
- Modify: `src/factory/pipeline.py`
- Modify: `tests/test_cover_generator.py`
- Modify: `tests/test_factory_pipeline_creation.py`

**Step 1: Write the failing test**

- Add a test that expects `CoverGenerator.process(..., generate_vertical=True)` to return both horizontal and vertical outputs when generation is enabled.
- Update pipeline product tests to assert the extra cover product is recorded correctly.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/test_cover_generator.py tests/test_factory_pipeline_creation.py -k cover`

**Step 3: Write minimal implementation**

- Honor `generate_vertical` in `src/factory/cover.py`.
- Ensure returned cover map and recorded cover products remain stable and easy for the frontend to consume.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/test_cover_generator.py tests/test_factory_pipeline_creation.py -k cover`

### Task 10: Final Verification And Documentation Sync

**Files:**
- Modify: `workflow/progress.md`
- Modify: `workflow/architecture.md`
- Verify: related frontend/API/tests

**Step 1: Run focused regression suite**

Run: `./.venv/bin/python -m pytest -q tests/test_cover_generator.py tests/test_factory_pipeline_creation.py tests/test_creation_review_gate.py tests/web/test_api_contract.py tests/web/test_pages_http.py`

**Step 2: Run E2E coverage for the new path**

Run: `./.venv/bin/python -m pytest -q tests/e2e/test_frontend_playwright.py -k 'new_task or task_detail or review'`

**Step 3: Update docs**

- Record the implementation and verification evidence in `workflow/progress.md`.
- Update `workflow/architecture.md` to document the new frontend creation configuration and creation summary/review flow.

**Step 4: Run full minimum gate**

Run: `./.venv/bin/python -m pytest -q`

