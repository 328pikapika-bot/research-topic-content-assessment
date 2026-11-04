# Content Assessment Agent

The Content Assessment Agent evaluates project materials against guidelines, reference material, and traceable external evidence. It produces an eight-stage, versioned assessment report and never treats a missing search result as proof of absolute originality.

Identifiers: `content_assessment`, `/v1/content-assessment`, `/api/service/nstl/ai/agent/content-assessment`, `/ai/agent/content_assessment/`, and MongoDB database `content_assessment_agent`.

Run `restart-dev.ps1` from the project root, then open `https://www.nstl.gov.cn/ai/agent/content_assessment/`. Port `18093` is an internal Vite HTTP port used by the local HTTPS gateway and should not be opened directly. See `README.md` and `docs/API设计.md` for operational and API details.
