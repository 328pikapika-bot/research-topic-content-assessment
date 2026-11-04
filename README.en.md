# Research Topic and Content Assessment Platform

English | [简体中文](README.md)

An agent workspace for researchers and research program managers with two distinct workflows: topic discovery and content assessment.

## Capabilities

- Topic discovery turns a research direction into a search plan, research gaps, and candidate questions.
- Content assessment extracts claims and indicators from papers or project material and analyzes them against literature evidence.
- Both workflows provide task creation, progress, history, file handling, and reports, with separate task states and outputs.

## Code and setup

`源码/backend/` contains the Python API and agents; `源码/frontend/web/` is the Vue UI; `源码/database/` holds initialization material; `源码/docker/` has deployment examples; `源码/tests/` contains tests. The backend starts at `源码/backend/app/main.py`. The local Compose example is `源码/docker/compose.local.yml`.

You need Python 3.11, Node.js 20, MongoDB, and your own model, literature search, full-text, and sensitive-content services. Python dependencies are in `源码/pyproject.toml`; frontend dependencies are in `源码/frontend/web/package.json`.

## Configuration

Set model keys, the MongoDB password, and external service URLs through environment variables; see `源码/.env.example`. `VITE_NSTL_DEMO_PASSWORD` is only for a frontend demo entry and is bundled into the frontend build. It is not production authentication. Example loopback addresses and `example.invalid` must be replaced for deployment.

## Verification and limits

A research-proposal agent appears in historical plans but is not a third verified workflow. Real task replay fixtures and reports are excluded, so some historical QA cases need replacement data. Configuration and mocked request tests passed; real model/search services and production deployment were not exercised. The public code was statically reviewed on 2026-11-04.

See the [publication notes](仓库发布说明.md) for the included files. Local archives remain outside the repository.
