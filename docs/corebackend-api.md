# AIA CoreBackend — API Reference

**Version:** 1.0 (POC)  
**Last Updated:** 2026-04-27  
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
| `500` | Unexpected server error |

---

## Document Status Values

| Value | Terminal | What to show |
|-------|----------|--------------|
| `PROCESSING` | No | "Processing..." spinner |
| `COMPLETE` | **Yes** | "View Result" button — assessment is ready |
| `ERROR` | **Yes** | Error message from `errorMessage` field |

**Rule:** Keep showing "Processing..." for any non-terminal status. Only update the UI when `COMPLETE` or `ERROR` arrives.

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
| `file` | File (binary) | Yes | PDF or DOCX file |
| `templateType` | string | Yes | Assessment template identifier — e.g. `SDA` |
| `fileName` | string | Yes | Original filename including extension — e.g. `architecture-v2.pdf` |

**Response `202`:**
```json
{
  "documentId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "PROCESSING"
}
```

Capture `documentId` — it is the key used by all subsequent calls.

**Error responses:**

| Scenario | Code | `detail` value |
|----------|------|----------------|
| Same `fileName` already uploaded by this user | `400` | `"A file named 'x.pdf' has already been uploaded by user '...'"` |
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

Returns a paginated list of all documents uploaded by the authenticated user, ordered by upload time descending.

**Query parameters:**

| Param | Type | Default | Maximum | Description |
|-------|------|---------|---------|-------------|
| `page` | integer | `1` | — | Page number (1-based) |
| `limit` | integer | `20` | `100` | Records per page |

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
    }
  ],
  "total": 42,
  "page": 1,
  "limit": 20
}
```

`completedAt` is `null` for documents still in `PROCESSING`.

---

### 5. Get Document Result

```
GET /api/v1/documents/{documentId}
Authorization: Bearer <jwt>
x-user-id: <userId>
```

Returns the full document record including the AI assessment result. Call this after the status endpoint returns `COMPLETE`.

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
- `null` when `status` is `PROCESSING` or `ERROR`.

**Error responses:**

| Scenario | Code | `detail` value |
|----------|------|----------------|
| Document not found / not owned by user | `404` | `"Document '...' not found."` |

---

### 6. Get Current User

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

## Endpoint Summary

| Method | Path | Auth | Returns |
|--------|------|------|---------|
| `GET` | `/health` | None | `{ status }` |
| `POST` | `/api/v1/documents/upload` | Required | `{ documentId, status }` — HTTP `202` |
| `GET` | `/api/v1/documents/status` | Required | `{ processingDocumentIds }` |
| `GET` | `/api/v1/documents` | Required | Paginated `HistoryRecord` list |
| `GET` | `/api/v1/documents/{documentId}` | Required | `ResultRecord` |
| `GET` | `/api/v1/users/me` | Required | `UserRecord` |

---

## TypeScript Types

Copy these into your frontend project to get full type coverage.

```typescript
export type DocumentStatus = 'PROCESSING' | 'COMPLETE' | 'ERROR'

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
  createdAt: string       // ISO 8601 UTC
  completedAt: string | null
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
  resultMd: string | null   // markdown string — render with react-markdown or similar
  errorMessage: string | null
  createdAt: string
  completedAt: string | null
}

export interface UserRecord {
  userId: string
  email: string
  name: string
}

export interface ApiError {
  detail: string
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
    if (!processingDocumentIds.includes(documentId)) return   // done (COMPLETE or ERROR)
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
// record.status is 'COMPLETE' or 'ERROR'
// record.resultMd is the markdown assessment string (null if ERROR)
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
3. When it disappears, call `GET /documents/{documentId}` (or `GET /documents` for the full list) to read the final `status` and `errorMessage`.

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
