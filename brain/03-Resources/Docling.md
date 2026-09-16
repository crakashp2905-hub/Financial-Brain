---
type: resource
verdict: INTEGRATE
category: documents
license: "MIT"
license-risk: green
url: https://github.com/docling-project/docling
role: "Filing, PDF, table, XBRL and OCR parsing"
tags:
  - resource
  - verdict/integrate
  - category/documents
  - licence/green
---

# Docling

**Filing, PDF, table, XBRL and OCR parsing - the missing Phase-1/2 resource**

The original register named [[Browser Use]] for filings. That was wrong: Browser Use is an
*acquisition* tool, not a parser. Annual reports and results PDFs are core inputs and need
a serious document pipeline.

Division of labour:
- [[Browser Use]] - find and fetch the document (last-mile, sandboxed)
- **Docling** - parse it into structure
- [[C00 Raw data lake]] - keep the original bytes either way

Feeds [[C11 Document intelligence]] and the Structure Recognition / Numerical Reasoning
tasks from the [[FinLLMs survey]].
