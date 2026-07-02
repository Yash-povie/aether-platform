# Aether — Multimodal Intelligence Platform

> **"AI that sees, hears, reads, and thinks — all at once."** A production-grade multi-agent platform for ingesting, analysing, and extracting intelligence from mixed-modality data: PDFs, video, audio, images, and sensor streams — with a mandatory Human-in-the-Loop review gate.

[![CI](https://github.com/your-username/aether-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/your-username/aether-platform/actions)
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://python.org)
[![Next.js](https://img.shields.io/badge/Next.js-14-black.svg)](https://nextjs.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What Problem This Solves

Real-world intelligence analysis does not come in a single file format. A single investigation might involve:

- A PDF report with embedded charts
- A video recording with spoken content
- Satellite or sensor telemetry streams
- Images with visual anomalies
- Unstructured text documents

Current tools handle one modality at a time. Aether ingests all of them simultaneously, routes each through specialised workers, runs a 7-agent analysis pipeline, and gates every finding behind a human reviewer before finalising — because in high-stakes domains, AI should propose, humans should decide.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Next.js 14 Frontend                                                │
│  Upload · HITL Review Queue · Reports · Login                       │
└────────────────────────┬────────────────────────────────────────────┘
                         │ HTTPS + RS256 JWT
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  API Gateway  (FastAPI)                                             │
│  RS256 JWT auth · RBAC (analyst/admin) · slowapi rate limiting      │
│  Correlation-ID middleware · Prometheus /metrics · RabbitMQ fanout  │
│                                                                     │
│  POST /api/v1/ingest          — upload files, create job            │
│  GET  /api/v1/jobs/{id}       — poll job status                     │
│  GET  /api/v1/jobs/{id}/report — fetch completed analysis           │
│  GET  /api/v1/hitl/queue      — pending HITL items (analyst+)       │
│  POST /api/v1/hitl/{id}/approve|reject — resolve HITL item          │
│  GET  /api/v1/audit/events    — audit log (admin only)              │
└──────────────────────┬──────────────────────────────────────────────┘
                       │ RabbitMQ (AMQP)
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Ingest Workers  (parallel, per-modality)                          │
│  ├─ pdf_worker.py      — PyMuPDF text extraction + OCR             │
│  ├─ video_worker.py    — Whisper transcription + keyframe extract   │
│  ├─ vision_worker.py   — Claude Vision / GPT-4V image analysis      │
│  ├─ sensor_worker.py   — Time-series parsing + anomaly pre-filter   │
│  └─ embedding_worker.py — OpenAI embeddings → pgvector store        │
│                                                                     │
│  Workers store artifacts in MinIO, metadata in PostgreSQL           │
└──────────────────────┬──────────────────────────────────────────────┘
                       │ Artifacts ready → triggers Agent Engine
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Agent Engine  (LangGraph)                                         │
│                                                                     │
│  Coordinator → reads artifacts from MinIO, builds task description │
│      │                                                              │
│      ├─► AnomalyDetector    — flags statistical + semantic outliers │
│      ├─► ConfidenceScorer   — assigns 0–1 confidence per finding    │
│      ├─► EvidenceReconciler — cross-modal evidence synthesis        │
│      ├─► PIIRedactor        — removes PII before reporting          │
│      ├─► Finalizer          — consolidates all agent outputs        │
│      └─► ReportWriter       — structured intelligence report        │
│                                                                     │
│  Low-confidence findings (< 0.7) → HITL queue                      │
│  High-confidence findings → directly to Report                     │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
         ┌─────────────┴──────────────┐
         ▼                            ▼
┌─────────────────┐       ┌──────────────────────────┐
│  HITL Service   │       │  Audit Service            │
│  WebSocket push │       │  Immutable event log      │
│  to analyst UI  │       │  every job, decision,     │
│  approve/reject │       │  ingest event recorded    │
└─────────────────┘       └──────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Data Layer                                                        │
│  PostgreSQL (jobs, users, reports, HITL items, audit events)       │
│  MinIO S3-compatible (PDFs, video, images, artifacts, reports)     │
│  pgvector extension (semantic search over ingested content)        │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Observability                                                      │
│  Prometheus /metrics on all services                                │
│  OpenTelemetry traces — correlation IDs propagated end-to-end       │
│  Structured JSON logging (python-json-logger)                       │
│  Helm chart for production Kubernetes deployment                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## The 7 Agents

| Agent | Trigger | What it does |
|---|---|---|
| **Coordinator** | First — always | Downloads all artifacts from MinIO, builds unified task description |
| **AnomalyDetector** | After Coordinator | Identifies statistical and semantic outliers across all modalities |
| **ConfidenceScorer** | After AnomalyDetector | Assigns a 0.0–1.0 confidence score to each finding |
| **EvidenceReconciler** | After ConfidenceScorer | Cross-modal synthesis — does the video confirm what the PDF states? |
| **PIIRedactor** | After EvidenceReconciler | Strips PII (names, emails, IDs) from findings before they leave the pipeline |
| **Finalizer** | After PIIRedactor | Consolidates all agent outputs into a single structured finding set |
| **ReportWriter** | Last — always | Produces the final intelligence report stored in PostgreSQL + MinIO |

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Frontend | Next.js 14, TypeScript, TailwindCSS, shadcn/ui | Upload, HITL review queue, report viewer, login |
| API Gateway | FastAPI, RS256 JWT, slowapi, python-json-logger | Auth, RBAC, rate limiting, correlation IDs, Prometheus |
| Agents | LangGraph, Anthropic Claude API | 7-agent multimodal analysis pipeline |
| Ingest workers | FastAPI, RabbitMQ (aio-pika), PyMuPDF, Whisper | Per-modality parallel processing |
| Message queue | RabbitMQ | Decoupled ingest → agent-engine fanout |
| Storage | PostgreSQL 15, MinIO (S3-compatible) | Structured metadata + binary artifact storage |
| Vector search | pgvector | Semantic search over embedded artifact content |
| Auth | RS256 JWT (asymmetric), RBAC | `analyst` and `admin` roles; JWKS endpoint |
| Audit | PostgreSQL `audit_events` table | Immutable log of every action |
| HITL | FastAPI WebSocket + frontend queue UI | Human review gate for low-confidence findings |
| Observability | Prometheus, OpenTelemetry, structured JSON logs | Metrics, traces, correlation IDs |
| Deployment | Kubernetes + Helm | Production chart with configurable replicas, secrets |
| Migrations | Alembic | PostgreSQL schema versioning |
| CI/CD | GitHub Actions | test → lint → build → push to registry |

---

## Repository Structure

```
aether-platform/
│
├── frontend/                        # Next.js 14 — TypeScript + TailwindCSS
│   └── src/
│       ├── app/
│       │   ├── page.tsx             # Dashboard / job list
│       │   ├── upload/page.tsx      # Multi-file upload with progress
│       │   ├── hitl/page.tsx        # HITL review queue + approve/reject UI
│       │   └── login/page.tsx       # JWT login
│       ├── components/
│       │   ├── layout/              # Sidebar, nav
│       │   └── ui/                  # shadcn/ui components
│       └── lib/
│           ├── api.ts               # Typed API client
│           └── hitl-ws.ts           # WebSocket client for HITL push
│
├── services/
│   ├── api-gateway/                 # FastAPI — main entry point
│   │   ├── main.py                  # App, middleware, all route handlers
│   │   ├── config.py                # Settings (pydantic-settings)
│   │   ├── routers/ingest.py        # POST /api/v1/ingest
│   │   └── utils/
│   │       ├── db.py                # Async SQLAlchemy engine
│   │       ├── minio_client.py      # MinIO upload helper
│   │       └── rabbitmq_client.py   # aio-pika publisher
│   │
│   ├── agent-engine/                # LangGraph 7-agent pipeline
│   │   ├── worker.py                # RabbitMQ consumer — triggers pipeline
│   │   ├── graph.py                 # LangGraph StateGraph definition
│   │   ├── schemas.py               # PipelineState TypedDict
│   │   └── agents/
│   │       ├── coordinator.py       # MinIO artifact loader
│   │       ├── anomaly_detector.py  # Statistical + semantic anomaly detection
│   │       ├── confidence_scorer.py # 0–1 confidence per finding
│   │       ├── evidence_reconciler.py # Cross-modal synthesis
│   │       ├── pii_redactor.py      # PII removal before reporting
│   │       ├── finalizer.py         # Output consolidation
│   │       └── report_writer.py     # Final intelligence report
│   │
│   ├── ingest-workers/              # Per-modality parallel processors
│   │   ├── main.py                  # RabbitMQ consumer entry point
│   │   ├── pdf_worker.py            # PyMuPDF + OCR
│   │   ├── video_worker.py          # Whisper transcription + frame extraction
│   │   ├── vision_worker.py         # Claude Vision / GPT-4V
│   │   ├── sensor_worker.py         # Time-series parsing
│   │   └── embedding_worker.py      # Embed + store in pgvector
│   │
│   ├── hitl-service/                # Human-in-the-Loop WebSocket service
│   │   └── main.py                  # Pushes low-confidence items to analysts
│   │
│   └── audit-service/               # Immutable audit trail
│       └── main.py                  # Writes all events to audit_events table
│
├── shared/                          # Shared modules across services
│   ├── auth/
│   │   ├── jwt_handler.py           # RS256 sign/verify, JWKS export
│   │   └── rbac.py                  # get_current_user, require_role decorator
│   ├── models/
│   │   ├── database.py              # SQLAlchemy models (Job, User, Report, HitlItem, AuditEvent)
│   │   └── schemas.py               # Pydantic request/response schemas
│   └── observability/
│       └── telemetry.py             # OpenTelemetry setup (traces + metrics)
│
├── infra/
│   ├── helm/aether/                 # Helm chart for production
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   └── templates/               # Deployment, Service, Ingress, HPA
│   ├── k8s/                         # Raw Kubernetes manifests
│   └── prometheus/prometheus.yml
│
├── alembic/                         # Database migrations
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py    # Jobs, users, reports, HITL, audit
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_agents.py           # Agent logic unit tests
│   │   └── test_sensor_worker.py    # Sensor parsing tests
│   └── integration/
│       └── test_api_gateway.py      # Full API flow integration tests
│
├── docker-compose.yml               # Full local stack
├── Makefile
├── alembic.ini
└── .env.example
```

---

## Services & Ports

| Service | Port | Description |
|---|---|---|
| `frontend` | **3000** | Next.js UI — upload, HITL queue, reports, login |
| `api-gateway` | **8000** | Main REST API — all client-facing endpoints |
| `agent-engine` | — | Internal worker — triggered by RabbitMQ, no public port |
| `ingest-workers` | — | Internal worker — triggered by RabbitMQ, no public port |
| `hitl-service` | **8001** | WebSocket — pushes HITL items to analyst UI |
| `audit-service` | — | Internal — consumes RabbitMQ events, writes audit log |
| `postgres` | 5432 | Jobs, users, reports, HITL, audit events |
| `rabbitmq` | 5672 (AMQP), 15672 (UI) | Message queue (admin/admin) |
| `minio` | 9000 (API), 9001 (UI) | Object storage (minioadmin/minioadmin) |
| `prometheus` | 9090 | Metrics |
| `grafana` | **3001** | Dashboards |

---

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/your-username/aether-platform
cd aether-platform
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY + generate RS256 keypair (see below)

# 2. Generate RS256 keypair for JWT
make keygen   # writes JWT_PRIVATE_KEY + JWT_PUBLIC_KEY to .env

# 3. Start full stack
docker-compose up -d

# 4. Run database migrations
docker-compose exec api-gateway alembic upgrade head

# 5. Upload a file and create a job
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "Authorization: Bearer <token>" \
  -F "files=@report.pdf"

# 6. Poll job status
curl http://localhost:8000/api/v1/jobs/<job_id> \
  -H "Authorization: Bearer <token>"

# 7. Review HITL queue (log in as analyst role)
open http://localhost:3000/hitl
```

---

## Authentication & RBAC

Aether uses **RS256 asymmetric JWT** — the private key signs tokens, the public key verifies them. This allows any service to verify tokens without access to the private key.

```
POST /api/v1/auth/login   → returns JWT (1 hour)
GET  /api/v1/auth/jwks    → public key in JWKS format
```

Two roles:
- **`analyst`** — can view jobs, submit files, review and resolve HITL items
- **`admin`** — all analyst permissions + access to audit log + user management

---

## Human-in-the-Loop (HITL) Design

Every agent finding has a confidence score. If confidence < 0.7:

1. Finding is written to the `hitl_items` table with status `pending`
2. HITL service pushes it via WebSocket to connected analyst sessions
3. Analyst sees it in the review queue with supporting evidence
4. Analyst clicks **Approve** or **Reject**
5. Decision is published to `hitl.decisions` RabbitMQ queue
6. Agent engine updates the report based on the decision

This design means Aether **never makes unilateral decisions** on uncertain findings in production. A human is always in the loop.

---

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Powers all 7 agents (claude-3-5-sonnet) |
| `JWT_PRIVATE_KEY` | RS256 private key PEM (generate with `make keygen`) |
| `JWT_PUBLIC_KEY` | RS256 public key PEM |
| `DATABASE_URL` | PostgreSQL async connection string |
| `RABBITMQ_URL` | AMQP connection string |
| `MINIO_ENDPOINT` | MinIO endpoint |
| `MINIO_ACCESS_KEY` | MinIO access key |
| `MINIO_SECRET_KEY` | MinIO secret key |
| `MINIO_BUCKET` | Artifact bucket name |
| `CORS_ORIGINS` | Comma-separated allowed origins |

---

## Kubernetes / Helm Deployment

```bash
# Helm install (production)
helm install aether infra/helm/aether \
  --set image.tag=latest \
  --set secrets.anthropicApiKey="sk-ant-..." \
  --set secrets.databaseUrl="postgresql://..." \
  -n aether

# Or raw manifests
kubectl apply -f infra/k8s/
```

---

## Running Tests

```bash
pip install -r tests/requirements.txt
pytest tests/ -v --tb=short
```

---

## What Makes This Different

| Typical LLM project | Aether |
|---|---|
| Single modality (text only) | PDF, video, audio, images, sensor streams |
| One LLM call | 7-agent pipeline with specialised cognitive roles |
| No auth | RS256 JWT + RBAC with two roles |
| No human oversight | Mandatory HITL for low-confidence findings |
| No audit trail | Immutable audit log of every action |
| No message queue | RabbitMQ for decoupled, fault-tolerant ingest |
| No observability | Prometheus + OTel + correlation IDs across all services |
| No deployment | Helm chart + raw K8s manifests |
| Basic logging | Structured JSON with correlation ID propagation |