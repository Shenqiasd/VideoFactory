# YouTube Cover And Description Copy Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make cover artifacts use the real YouTube thumbnail when available, keep only one cover output, and add a copyable translated description block to task detail.

**Architecture:** Extend the cover generator to prefer remote source thumbnails for YouTube URLs and to collapse fallback screenshot generation into a single final cover file. Update the task detail template and client-side script so translated descriptions render in the page and can be copied directly.

**Tech Stack:** FastAPI, Jinja2, vanilla JavaScript, pytest, httpx

---

### Task 1: Cover Generator Red-Green

**Files:**
- Create: `tests/test_cover_generator.py`
- Modify: `src/factory/cover.py`

**Step 1: Write the failing test**

- Add a test that calls `CoverGenerator.process(..., source_url=<youtube-url>)` and expects it to return one `horizontal` cover path without invoking frame extraction.
- Add a test that exercises the non-YouTube fallback and expects the output directory to contain only the final cover image, not multiple extracted frames.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_cover_generator.py -q`

**Step 3: Write minimal implementation**

- Add YouTube URL detection and remote thumbnail download in `src/factory/cover.py`.
- Clean the output directory before cover generation.
- Move fallback frame extraction into a temporary directory and write only one final cover file back to `output_dir`.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_cover_generator.py -q`

### Task 2: Pipeline Integration

**Files:**
- Modify: `src/factory/pipeline.py`
- Test: `tests/test_factory_pipeline_creation.py`

**Step 1: Write/adjust failing test**

- Update the product-recording expectations so a single cover product is recorded from the returned cover map.

**Step 2: Run test to verify behavior**

Run: `./.venv/bin/pytest tests/test_factory_pipeline_creation.py -q`

**Step 3: Write minimal implementation**

- Pass `task.source_url` into cover generation.
- Keep one cover product entry in task products.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_factory_pipeline_creation.py -q`

### Task 3: Task Detail Description Copy

**Files:**
- Modify: `web/templates/task_detail.html`
- Modify: `tests/web/test_pages_http.py`

**Step 1: Write the failing test**

- Add a page test asserting the task detail HTML contains translated-description rendering and a copy button hook.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/web/test_pages_http.py -q`

**Step 3: Write minimal implementation**

- Add a translated description block and copy button markup to the task detail template.
- Extend the existing client-side script to populate the description and copy it via `navigator.clipboard`.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/web/test_pages_http.py -q`

### Task 4: Final Verification

**Files:**
- Verify only

**Step 1: Run focused regression suite**

Run: `./.venv/bin/pytest tests/test_cover_generator.py tests/test_factory_pipeline_creation.py tests/web/test_pages_http.py -q`

**Step 2: Run related API/runtime checks**

Run: `./.venv/bin/pytest tests/web/test_api_contract.py -k 'inline_cover or task_detail' -q`
