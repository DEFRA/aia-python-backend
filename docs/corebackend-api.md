# AIA CoreBackend — API Reference

**Version:** 1.1 (POC)  
**Last Updated:** 2026-05-12  
**Audience:** Frontend Development Team

---

## Base URLs

| Environment | URL |
|-------------|-----|
| Local dev | `http://localhost:8086` |
| POC / Staging | TBD — confirm with Backend team |
| Production | `https://{alb-dns}` — confirm with Backend team |

**Interactive docs (Swagger UI):** `{base-url}/docs`  
**OpenAPI schema:** `{base-url}/openapi.json`

---

## Authentication

Every endpoint except `GET /health` requires **both** of the following headers on every request:

| Header | Format | Example |
|--------|--------|---------|
| `Authorization` | `Bearer <jwt>` | `Bearer eyJhbGciOiJIUzI1NiJ9...` |
| `x-user-id` | UUID string | `x-user-id: 3fa85f64-5717-4562-b3fc-2c963f66afa6` |

The backend cross-validates: the value in `x-user-id` must match the `sub` claim inside the JWT. A mismatch returns `403`.

**Token algorithm:** HS256  
**Identity claim in token:** `sub`

> **POC behaviour:** A seeded guest user (`guest@aia.local`, `userId: 00000000-0000-0000-0000-000000000001`) is used for all requests. When EntraID SSO is integrated later, only the JWT token content changes — the header names and flow remain the same.

---

## Error Envelope

All error responses follow FastAPI's standard format:

```json
{ "detail": "Human-readable error description" }
```

---

## HTTP Status Codes

| Code | When used |
|------|-----------|
| `200` | Successful GET |
| `202` | Upload accepted — processing started asynchronously |
| `400` | Bad request (duplicate file, missing field) |
| `401` | Missing or invalid token |
| `403` | Token valid but `x-user-id` does not match token `sub` |
| `404` | Document not found, or not owned by this user |
| `422` | Validation error — query/path parameter out of range |
| `500` | Unexpected server error |

---

## Document Status Values

| Value | Terminal | What to show |
|-------|----------|--------------|
| `PROCESSING` | No | "Processing..." spinner |
| `COMPLETE` | **Yes** | "View Result" button — full assessment ready in `resultMd` |
| `PARTIAL_COMPLETE` | **Yes** | "View Partial Result" — assessment ready in `resultMd`; `errorMessage` lists agents that did not respond in time |
| `ERROR` | **Yes** | Error message from `errorMessage` field; `resultMd` is `null` |

**Rule:** Keep showing "Processing..." for any non-terminal status. Update the UI when `COMPLETE`, `PARTIAL_COMPLETE`, or `ERROR` arrives.

---

## Endpoints

### 1. Health Check

```
GET /health
```

No auth required. Used by infrastructure probes.

**Response `200`:**
```json
{ "status": "ok" }
```

---

### 2. Upload Document

```
POST /api/v1/documents/upload
Content-Type: multipart/form-data
Authorization: Bearer <jwt>
x-user-id: <userId>
```

Saves document metadata to the database, uploads the binary file to S3 in the background, and returns immediately. Processing starts automatically.

**Request — multipart/form-data fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | File (binary) | Yes | DOCX file |
| `templateType` | string | Yes | Assessment template identifier — e.g. `SDA` |
| `fileName` | string | Yes | Original filename including extension — e.g. `architecture-v2.docx` |

**Response `202`:**
```json
{
  "documentId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "PROCESSING"
}
```

**`status` field:** String value (`"PROCESSING"` | `"COMPLETE"` | `"PARTIAL_COMPLETE"` | `"ERROR"`). Capture `documentId` — it is the key used by all subsequent calls.

**Timestamps:** All `createdAt` and `completedAt` fields in responses are serialized as ISO 8601 UTC strings (e.g. `"2026-04-27T10:00:00Z"`).

**Error responses:**

| Scenario | Code | `detail` value |
|----------|------|----------------|
| Same `fileName` already uploaded by this user | `400` | `"A file named 'x.pdf' has already been uploaded. Please rename the file and try again"` |
| Database error | `500` | `"Failed to record document metadata."` |

---

### 3. Get Processing Status

```
GET /api/v1/documents/status
Authorization: Bearer <jwt>
x-user-id: <userId>
```

Returns the list of document IDs that are still in `PROCESSING` state for the authenticated user. Poll this endpoint repeatedly; when the list is empty (or no longer contains a document you care about), call **Fetch Upload History** to get the final status of all documents.

**Response `200`:**
```json
{
  "processingDocumentIds": [
    "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "9b1e2c3d-4a5f-6789-bcde-0f1a2b3c4d5e"
  ]
}
```

An empty array means no documents are currently being processed for this user:
```json
{ "processingDocumentIds": [] }
```

---

### 4. Fetch Upload History

```
GET /api/v1/documents?page=1&limit=20
Authorization: Bearer <jwt>
x-user-id: <userId>
```

Returns a paginated list of **all** documents uploaded by the authenticated user, ordered by upload time descending. No status filter is applied — records in every status (`PROCESSING`, `COMPLETE`, `PARTIAL_COMPLETE`, `ERROR`) are included.

**Query parameters:**

| Param | Type | Default | Maximum | Description |
|-------|------|---------|---------|-------------|
| `page` | integer | `1` | — | Page number (1-based) |
| `limit` | integer | `20` | `100` | Records per page. If exceeded, silently capped at 100. |

**Response `200`:**
```json
{
  "documents": [
    {
      "documentId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "originalFilename": "architecture-v2.pdf",
      "templateType": "SDA",
      "status": "COMPLETE",
      "createdAt": "2026-04-27T10:00:00Z",
      "completedAt": "2026-04-27T10:01:45Z"
    },
    {
      "documentId": "9b1e2c3d-4a5f-6789-bcde-0f1a2b3c4d5e",
      "originalFilename": "risk-review.docx",
      "templateType": "SDA",
      "status": "PARTIAL_COMPLETE",
      "createdAt": "2026-04-27T11:00:00Z",
      "completedAt": "2026-04-27T11:08:12Z"
    },
    {
      "documentId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "originalFilename": "draft.docx",
      "templateType": "SDA",
      "status": "PROCESSING",
      "createdAt": "2026-04-27T11:30:00Z",
      "completedAt": null
    }
  ],
  "total": 42,
  "page": 1,
  "limit": 20
}
```

`completedAt` is `null` only before the orchestrator begins processing the document (a brief window after upload). It is set for all terminal statuses (`COMPLETE`, `PARTIAL_COMPLETE`, `ERROR`) and is also set once the orchestrator starts, so it may be non-null even for documents still in `PROCESSING`.

---

### 5. Get Document Result

```
GET /api/v1/documents/{documentId}
Authorization: Bearer <jwt>
x-user-id: <userId>
```

Returns the full document record including the AI assessment result. Call this after the document disappears from `processingDocumentIds` (i.e., status has reached a terminal state: `COMPLETE`, `PARTIAL_COMPLETE`, or `ERROR`).

**Path parameter:**

| Param | Type | Description |
|-------|------|-------------|
| `documentId` | UUID string | Document identifier |

**Response `200`:**
```json
{
  "documentId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "originalFilename": "architecture-v2.pdf",
  "templateType": "SDA",
  "status": "COMPLETE",
  "resultMd": "# Assessment Report\n\n## Security\n\n...",
  "errorMessage": null,
  "createdAt": "2026-04-27T10:00:00Z",
  "completedAt": "2026-04-27T10:01:45Z"
}
```

**`resultMd` notes:**
- Value is a **markdown string**. Render with any standard markdown library (e.g. `react-markdown`, `marked`).
- Non-null when `status` is `COMPLETE` or `PARTIAL_COMPLETE` — render the assessment in both cases.
- `null` when `status` is `PROCESSING` or `ERROR`.

**`errorMessage` notes:**
- Non-null when `status` is `ERROR` (unrecoverable failure) or `PARTIAL_COMPLETE` (lists the agent types that did not respond within the timeout).
- `null` when `status` is `PROCESSING` or `COMPLETE`.

**Error responses:**

| Scenario | Code | `detail` value |
|----------|------|----------------|
| Document not found / not owned by user | `404` | `"Document '...' not found."` |

---

### 6. Get Cost Usage (paginated)

```
GET /api/v1/cost-usage?page=1&limit=10
Authorization: Bearer <jwt>
x-user-id: <userId>
```

Returns paginated per-document token consumption and cost for every document the user has uploaded that has at least one cost-usage row recorded. Pagination is applied at the **document** level — each entry in `costUsage[]` is one document, and its `agents[]` array is never split across pages. The `summary` block aggregates totals across the user's entire result set, not just the current page.

**Query parameters:**

| Param | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `page` | int | `1` | `≥ 1` | 1-based page number |
| `limit` | int | `10` | `1`–`100` | Documents per page |

`page=0` or `limit > 100` returns `422 Unprocessable Entity`.

**Response `200`:**
```json
{
  "costUsage": [
    {
      "doc_id": "11111111-1111-1111-1111-111111111111",
      "file_name": "Architecture_Review_Q1_2026.docx",
      "uploadedAt": "2026-05-01T10:30:00Z",
      "agents": [
        { "name": "Security",     "inputTokens": 12500, "outputTokens": 8200 },
        { "name": "Technology",   "inputTokens":  9800, "outputTokens": 6400 },
        { "name": "Architecture", "inputTokens": 11200, "outputTokens": 7800 }
      ],
      "totalCost": 0.4365,
      "currency": "USD"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 10,
    "total": 4,
    "totalPages": 1,
    "hasNext": false,
    "hasPrevious": false,
    "nextPage": null,
    "previousPage": null
  },
  "summary": {
    "totalCost": 1.2345,
    "currency": "USD",
    "totalDocuments": 4,
    "totalInputTokens": 109000,
    "totalOutputTokens": 72500,
    "totalTokens": 181500
  }
}
```

**Field notes:**
- `totalCost` per document is the sum of `unit_cost` across that document's agent rows in the database.
- `currency` is currently fixed to `USD`; promoted to a column when multi-currency is introduced.
- `summary.totalDocuments` counts only documents that have at least one cost-usage row.

---

### 7. Get Cost Usage for a Single Document

```
GET /api/v1/cost-usage/{documentId}
Authorization: Bearer <jwt>
x-user-id: <userId>
```

**Path parameter:**

| Param | Type | Description |
|-------|------|-------------|
| `documentId` | UUID string | Document identifier |

**Response `200`:**
```json
{
  "doc_id": "11111111-1111-1111-1111-111111111111",
  "file_name": "Architecture_Review_Q1_2026.docx",
  "uploadedAt": "2026-05-01T10:30:00Z",
  "agents": [
    { "name": "Security",     "inputTokens": 12500, "outputTokens": 8200 },
    { "name": "Technology",   "inputTokens":  9800, "outputTokens": 6400 },
    { "name": "Architecture", "inputTokens": 11200, "outputTokens": 7800 }
  ],
  "totalCost": 0.4365,
  "currency": "USD"
}
```

**Error responses:**

| Scenario | Code | `detail` value |
|----------|------|----------------|
| Document not found / not owned by user / no cost-usage rows | `404` | `"Document '...' not found."` |

---

### 8. Get Current User

```
GET /api/v1/users/me
Authorization: Bearer <jwt>
x-user-id: <userId>
```

Returns the authenticated user's profile.

**Response `200`:**
```json
{
  "userId": "00000000-0000-0000-0000-000000000001",
  "email": "guest@aia.local",
  "name": "Guest User"
}
```

> **POC:** All requests resolve to the seeded guest user. When EntraID SSO is integrated, this endpoint returns the real user's name and email — no frontend change required.

---

### 9. Get Policy Document Options

```
GET /api/v1/policy-documents/options
Authorization: Bearer <jwt>
x-user-id: <userId>
```

Returns the allowed values for `source` and `category` fields. Call this once on page load to populate dropdowns/filters before rendering the policy-documents list or the edit form.

**Response `200`:**
```json
{
  "sources": ["SharePoint", "Confluence", "GitHub"],
  "categories": ["technical", "security"]
}
```

`categories` is driven by active rows in the `policy_source_categories` reference table — values may grow without a frontend change.

---

### 10. List Policy Documents

```
GET /api/v1/policy-documents?page=1&limit=20
Authorization: Bearer <jwt>
x-user-id: <userId>
```

Returns a paginated list of all policy documents, ordered by `url_id` ascending.

**Query parameters:**

| Param | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `page` | integer | `1` | `≥ 1` | 1-based page number |
| `limit` | integer | `20` | `1`–`200` | Records per page |

**Response `200`:**
```json
{
  "documents": [
    {
      "urlId": 1,
      "filename": "Security Policy v3.docx",
      "category": "security",
      "source": "SharePoint",
      "url": "https://company.sharepoint.com/sites/policies/Security_Policy_v3.docx",
      "isActive": true,
      "updatedAt": "2026-05-01T09:00:00Z"
    }
  ],
  "total": 42,
  "page": 1,
  "limit": 20
}
```

**Field notes:**
- All fields are camelCase in the JSON response.
- `source` is one of `"SharePoint"`, `"Confluence"`, `"GitHub"`.
- `updatedAt` is ISO 8601 UTC or `null` if the row has never been updated.

---

### 11. Get a Policy Document

```
GET /api/v1/policy-documents/{urlId}
Authorization: Bearer <jwt>
x-user-id: <userId>
```

**Path parameter:**

| Param | Type | Description |
|-------|------|-------------|
| `urlId` | integer | `url_id` of the policy document |

**Response `200`:**
```json
{
  "urlId": 1,
  "filename": "Security Policy v3.docx",
  "category": "security",
  "source": "SharePoint",
  "url": "https://company.sharepoint.com/sites/policies/Security_Policy_v3.docx",
  "isActive": true,
  "updatedAt": "2026-05-01T09:00:00Z"
}
```

**Error responses:**

| Scenario | Code | `detail` value |
|----------|------|----------------|
| `urlId` not found | `404` | `"Policy document '1' not found."` |

---

### 12. Update a Policy Document

```
PUT /api/v1/policy-documents/{urlId}
Content-Type: application/json
Authorization: Bearer <jwt>
x-user-id: <userId>
```

Replaces all mutable fields on a policy document. All fields in the request body are required.

**Path parameter:**

| Param | Type | Description |
|-------|------|-------------|
| `urlId` | integer | `url_id` of the policy document |

**Request body:**
```json
{
  "filename": "Security Policy v4.docx",
  "category": "security",
  "source": "SharePoint",
  "url": "https://company.sharepoint.com/sites/policies/Security_Policy_v4.docx",
  "isActive": true
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `filename` | string | 1–500 chars | Display name of the document |
| `category` | string | 1–100 chars; must exist in reference table | Document category |
| `source` | string | `"SharePoint"` \| `"Confluence"` \| `"GitHub"` | Source system |
| `url` | string | 1–4000 chars | Full URL to the document |
| `isActive` | boolean | — | Whether the document is active |

**Response `200`:** Same shape as **Get a Policy Document** (`PolicyDocumentRecord`), reflecting the updated values.

**Error responses:**

| Scenario | Code | `detail` value |
|----------|------|----------------|
| `urlId` not found | `404` | `"Policy document '1' not found."` |
| `category` value not in reference table | `400` | `"Invalid category: 'unknown'. Must be one of: technical, security"` |
| `source` value not in allowed set | `422` | Pydantic validation error |

---

## Endpoint Summary

| Method | Path | Auth | Returns |
|--------|------|------|---------|
| `GET` | `/health` | None | `{ status }` |
| `POST` | `/api/v1/documents/upload` | Required | `{ documentId, status }` — HTTP `202` |
| `GET` | `/api/v1/documents/status` | Required | `{ processingDocumentIds }` |
| `GET` | `/api/v1/documents` | Required | Paginated `HistoryRecord` list |
| `GET` | `/api/v1/documents/{documentId}` | Required | `ResultRecord` |
| `GET` | `/api/v1/cost-usage` | Required | Paginated `CostUsageResponse` |
| `GET` | `/api/v1/cost-usage/{documentId}` | Required | `CostUsageDocument` |
| `GET` | `/api/v1/users/me` | Required | `UserRecord` |
| `GET` | `/api/v1/policy-documents/options` | Required | `PolicyDocumentOptionsResponse` |
| `GET` | `/api/v1/policy-documents` | Required | Paginated `PolicyDocumentListResponse` |
| `GET` | `/api/v1/policy-documents/{urlId}` | Required | `PolicyDocumentRecord` |
| `PUT` | `/api/v1/policy-documents/{urlId}` | Required | `PolicyDocumentRecord` (updated) |

---

## TypeScript Types

Copy these into your frontend project to get full type coverage.

```typescript
export type DocumentStatus = 'PROCESSING' | 'COMPLETE' | 'PARTIAL_COMPLETE' | 'ERROR'

export interface UploadResponse {
  documentId: string
  status: DocumentStatus
}

export interface ProcessingStatusResponse {
  processingDocumentIds: string[]
}

export interface HistoryRecord {
  documentId: string
  originalFilename: string
  templateType: string
  status: DocumentStatus
  createdAt: string       // ISO 8601 UTC, e.g. "2026-04-27T10:00:00Z"
  completedAt: string | null  // null only before orchestrator starts; ISO 8601 for all terminal statuses; do not use as a completion indicator — use `status` instead
}

export interface HistoryResponse {
  documents: HistoryRecord[]
  total: number
  page: number
  limit: number
}

export interface ResultRecord {
  documentId: string
  originalFilename: string
  templateType: string
  status: DocumentStatus
  resultMd: string | null   // non-null for COMPLETE and PARTIAL_COMPLETE; null for PROCESSING and ERROR
  errorMessage: string | null  // non-null for ERROR and PARTIAL_COMPLETE; null for PROCESSING and COMPLETE
  createdAt: string  // ISO 8601 UTC
  completedAt: string | null  // null only before orchestrator starts; ISO 8601 for all terminal statuses; do not use as a completion indicator — use `status` instead
}

export interface UserRecord {
  userId: string
  email: string
  name: string
}

export interface AgentTokenUsage {
  name: string          // e.g. "Security", "Technology", "Architecture", "Governance"
  inputTokens: number
  outputTokens: number
}

export interface CostUsageDocument {
  doc_id: string
  file_name: string
  uploadedAt: string    // ISO 8601 UTC
  agents: AgentTokenUsage[]
  totalCost: number     // SUM(unit_cost) across the doc's agent rows
  currency: string      // currently always "USD"
}

export interface Pagination {
  page: number
  limit: number
  total: number
  totalPages: number
  hasNext: boolean
  hasPrevious: boolean
  nextPage: number | null
  previousPage: number | null
}

export interface CostUsageSummary {
  totalCost: number
  currency: string
  totalDocuments: number
  totalInputTokens: number
  totalOutputTokens: number
  totalTokens: number
}

export interface CostUsageResponse {
  costUsage: CostUsageDocument[]
  pagination: Pagination
  summary: CostUsageSummary   // aggregated across the full result set, not just the current page
}

export interface ApiError {
  detail: string
}

export type PolicyDocumentSource = 'SharePoint' | 'Confluence' | 'GitHub'

export interface PolicyDocumentRecord {
  urlId: number
  filename: string
  category: string
  source: PolicyDocumentSource
  url: string
  isActive: boolean
  updatedAt: string | null   // ISO 8601 UTC, or null if never updated
}

export interface PolicyDocumentListResponse {
  documents: PolicyDocumentRecord[]
  total: number
  page: number
  limit: number
}

export interface PolicyDocumentOptionsResponse {
  sources: PolicyDocumentSource[]
  categories: string[]
}

export interface PolicyDocumentUpdateRequest {
  filename: string        // 1–500 chars
  category: string        // 1–100 chars; must exist in reference table
  source: PolicyDocumentSource
  url: string             // 1–4000 chars
  isActive: boolean
}
```

---

## Integration Cookbook

### Upload → Poll → Display

```typescript
// 1. Upload
const formData = new FormData()
formData.append('file', file)
formData.append('templateType', 'SDA')
formData.append('fileName', file.name)

const uploadRes = await fetch(`${BASE_URL}/api/v1/documents/upload`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${jwt}`, 'x-user-id': userId },
  body: formData,
})
const { documentId } = await uploadRes.json() as UploadResponse

// 2. Poll /documents/status until the uploaded documentId disappears from the list
const POLL_INTERVAL_MS = 30_000
const MAX_WAIT_MS = 600_000

async function waitForCompletion(documentId: string): Promise<void> {
  const start = Date.now()
  while (Date.now() - start < MAX_WAIT_MS) {
    const res = await fetch(`${BASE_URL}/api/v1/documents/status`, {
      headers: { Authorization: `Bearer ${jwt}`, 'x-user-id': userId },
    })
    const { processingDocumentIds } = await res.json() as ProcessingStatusResponse
    if (!processingDocumentIds.includes(documentId)) return   // done (COMPLETE, PARTIAL_COMPLETE, or ERROR)
    await new Promise(r => setTimeout(r, POLL_INTERVAL_MS))
  }
  throw new Error('Assessment timed out — the user can check history later.')
}

await waitForCompletion(documentId)

// 3. Fetch full result (includes resultMd when COMPLETE)
const detailRes = await fetch(`${BASE_URL}/api/v1/documents/${documentId}`, {
  headers: { Authorization: `Bearer ${jwt}`, 'x-user-id': userId },
})
const record = await detailRes.json() as ResultRecord
// record.status is 'COMPLETE', 'PARTIAL_COMPLETE', or 'ERROR'
// record.resultMd is non-null for COMPLETE and PARTIAL_COMPLETE; null for ERROR
// record.errorMessage is non-null for PARTIAL_COMPLETE (lists missing agents) and ERROR
```

### Polling behaviour

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Interval | 30 seconds | Balances responsiveness vs server load |
| Max wait | 10 minutes | Covers worst-case 5-agent processing time |
| Max polls | 20 | `600s ÷ 30s` |

**Flow in words:**
1. Upload → receive `documentId`.
2. Poll `GET /documents/status` every 30 s; check whether your `documentId` is still in `processingDocumentIds`.
3. When it disappears, call `GET /documents/{documentId}` (or `GET /documents` for the full list) to read the final `status`. For `COMPLETE` and `PARTIAL_COMPLETE`, render `resultMd`. For `PARTIAL_COMPLETE` and `ERROR`, also show `errorMessage`.

When 10 minutes elapse and the document is still processing, stop polling and show:

> *"This assessment is taking longer than expected. You can leave this page and check back in your history."*

The backend continues running. The user can re-open history at any time to see the final result.

### Handling errors from the API

```typescript
async function apiFetch(url: string, options: RequestInit) {
  const res = await fetch(url, options)
  if (!res.ok) {
    const err = await res.json() as ApiError
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
  return res.json()
}
```
