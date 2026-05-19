# SharePoint URL Patterns — Extraction Approach

This document captures the design and approach for handling the different SharePoint URL patterns present in `policy_sources.json`.

---

## URL Pattern Taxonomy

| Pattern | Example | Handler |
|---------|---------|---------|
| SitePage | `.../SitePages/foo.aspx` | `read_page_content()` — existing Graph `sitepages` API |
| Shared PDF | `/:b:/r/...pdf` | `read_pdf_from_share()` — implemented |
| Document Library | `.../Forms/AllItems.aspx` | **Approach defined below** |
| Shared Folder | `/:f:/r/...` | **Approach defined below** |

---

## Pattern 1: SitePage (`.../SitePages/*.aspx`)

**Already implemented.** Uses:
```
GET /sites/{hostname}:{sitePath}:/pages/{pageName}?$expand=canvasLayout
```
Returns rich-text page content directly.

---

## Pattern 2: Shared PDF Link (`/:b:/r/`)

**Implemented** in `app/datapipeline/src/sharepoint.py` — `read_pdf_from_share()`.

**Approach:**
1. Detect `/:b:/r/` in URL path.
2. Base64url-encode the full URL to form a share token: `u!` + base64url(url)
3. Call `GET /shares/{shareToken}/driveItem` → get `id` and `@microsoft.graph.downloadUrl`
4. Download content via `/shares/{shareToken}/driveItem/content`
5. Extract text using `pypdf.PdfReader`

---

## Pattern 3: Document Library (`/Forms/AllItems.aspx`)

**URL example:**
```
https://defra.sharepoint.com/teams/Team3221/Published%20Architecture%20Documentation/Forms/AllItems.aspx
```

### Detection
Match URLs containing `/Forms/AllItems.aspx`. This is distinct from SitePages (no `Forms/`) and shared links (no `/:b:/r/` or `/:f:/r/`).

### Step 1 — Parse URL → Site + Drive

From the URL decompose:
- **hostname**: `defra.sharepoint.com`
- **site path**: `/teams/Team3221` (everything before the library segment)
- **library name**: `Published Architecture Documentation` (segment immediately before `/Forms/`)

Graph API resolution:
```
GET /sites/defra.sharepoint.com:/teams/Team3221          → siteId
GET /sites/{siteId}/drives                                → list all drives, match by name
```

### Step 2 — Recursive Folder Traversal

Start from drive root, recurse depth-first:
```
GET /drives/{driveId}/root/children
```

For each item returned:
- **folder** → `GET /drives/{driveId}/items/{itemId}/children` (recurse)
- **file** → add to extraction queue

**Pagination**: Each `/children` response returns ≤ 200 items. Follow `@odata.nextLink` until exhausted.

**Depth limit**: Enforce max depth (e.g. 5 levels) to avoid runaway traversal.

### Step 3 — File Filtering

Only process known extractable extensions: `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.txt`.
Skip binaries, images, zip archives.

### Step 4 — Content Extraction per File Type

Download each file:
```
GET /drives/{driveId}/items/{itemId}/content
```

| Extension | Library |
|-----------|---------|
| `.pdf` | `pypdf` (already in requirements.txt) |
| `.docx` | `python-docx` |
| `.xlsx` | `openpyxl` |
| `.pptx` | `python-pptx` |

### Step 5 — Content Aggregation

**Recommended: per-file** (Option B over flat concatenation).

Treat each file as an independent document. This preserves:
- `lastModifiedDateTime` per file for incremental re-processing
- File-level attribution in extracted questions
- Cleaner chunking for the LLM pipeline

### Step 6 — Change Detection

Graph API provides `lastModifiedDateTime` and `eTag` per item. On re-runs:
- Store `{itemId: lastModifiedDateTime}` after first crawl
- Only re-extract files that have changed or are new

For bulk change tracking, use **Graph Delta API**:
```
GET /drives/{driveId}/root/delta    → full snapshot + deltaLink token
GET /drives/{driveId}/root/delta?token={deltaLink}  → only changes since last run
```
This is very efficient for large libraries with infrequent changes.

### Practical Constraints

| Concern | Mitigation |
|---------|------------|
| Rate limiting (12,000 req/10 min per app) | Exponential backoff on HTTP 429 |
| Large files | Skip files > 50 MB or extract only first N pages |
| Large libraries (100s of files) | Process in batches; async/parallel downloads |
| Permissions | Existing service principal credentials are sufficient if it has read access to the library — no extra consent needed |

---

## Pattern 4: Shared Folder Link (`/:f:/r/`)

**URL example:**
```
https://defra.sharepoint.com/:f:/r/teams/Team3221/Soln%20and%20App%20Architecture/Solution%20Design%20Authority
```

### Approach
Reuses the same base64url share-token encoding already implemented for PDF links (`/:b:/r/`), but resolves to a folder instead of a file.

1. Detect `/:f:/r/` in URL path.
2. Encode URL as share token: `u!` + base64url(url)
3. Call `GET /shares/{shareToken}/driveItem` → confirms it is a folder (`folder` facet present)
4. Call `GET /shares/{shareToken}/driveItem/children` → list immediate children
5. Recurse into sub-folders; extract files using the same per-type extractors as Pattern 3.

The detection and recursion logic is identical to the AllItems.aspx pattern from Step 2 onward — only the entry point differs (share token vs site+drive resolution).

---

## Implementation Order (Recommended)

1. **`/:b:/r/` shared PDF** — Done ✅
2. **`/Forms/AllItems.aspx` document library** — Next
3. **`/:f:/r/` shared folder** — After, as it reuses the recursion logic from step 2
4. **`.docx` / `.xlsx` extraction** — Alongside steps 2–3 as new file types appear
