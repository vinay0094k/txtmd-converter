# txtmd-converter

Convert PDF files to **Markdown (.md)** or **Plain Text (.txt)** via a web UI.

Built with [Streamlit](https://streamlit.io), [PyMuPDF](https://pypi.org/project/PyMuPDF/), [markitdown](https://github.com/microsoft/markitdown), [pyzipper](https://pypi.org/project/pyzipper/), and [Tesseract OCR](https://github.com/tesseract-ocr/tesseract).

---

## Features

- **Two output formats** — select `.md` or `.txt` from a dropdown.
- **Multi-tier conversion pipeline** — automatically falls through:
  1. `markitdown` (best-effort structural Markdown)
  2. PyMuPDF block extraction with smart heading detection
  3. Raw text extraction (guaranteed output)
- **Batch upload** — upload and convert multiple PDFs at once.
- **Page range selection** — convert only specific pages (e.g., `1-5,7,10-12`). End values beyond the last page are clamped automatically. Invalid ranges show a clear error.
- **Page range confirmation** — an info banner shows exactly which pages were selected before conversion begins.
- **OCR for scanned pages** — enable "Use OCR for scanned pages" in Options to run Tesseract OCR on image-only pages. Only pages with no extractable text are OCR'd; pages with a text layer are not reprocessed. When OCR is on, Tier 1 (MarkItDown) is skipped to prevent scanned pages being silently dropped.
- **Smart heading detection** — detects numbered sections, ALL CAPS titles, underlined headings, colon-ending labels, and font-size-based hierarchy.
- **Password support (input + output)**:
  - Unlocks a password-protected input PDF.
  - Wraps the downloaded output in an **AES-256 encrypted ZIP** using the same password — the file prompts for the password when extracted.
- **File validation** — checks MIME type, extension, and size limit (50 MB).
- **Non-blocking conversion with cancel** — conversion runs in a background thread; the Convert button shows live status ("Converting 2/3 — report.pdf") and a Cancel button appears beside it to stop mid-batch.
- **Global result cache (`@st.cache_data`)** — results are cached globally across sessions keyed on file content + all options. Converting the same file twice (or after a page refresh) returns instantly without re-reading the PDF.
- **Preview toggle** — click "Show Preview" to view rendered output; not auto-displayed.
- **Download** — browser download button (plain `.md`/`.txt` without password; `.zip` with password).
- **Stats** — character count, word count, line count, and elapsed time shown after conversion.
- **File logging** — rotating log file at `logs/txtmd-converter.log` (5 MB, 3 backups).

---

## Limitations

| Limitation | Explanation |
|---|---|
| **OCR accuracy** | OCR quality depends on scan resolution and image clarity. Low-quality scans may produce garbled output. |
| **OCR speed** | OCR at 300 DPI is significantly slower than text extraction. Expect several seconds per scanned page. |
| **Table fidelity** | Tables are extracted but complex layouts (merged cells, nested tables) may lose structure. |
| **Max file size** | Hard-coded at 50 MB. Can be increased via `config.py`. |
| **Single-page images** | Image extraction from PDF is not included. |
| **Output encryption format** | Password-protected output is a ZIP file, not a PDF. Markdown and plain text have no native encryption format. |

---

## Quick Start

### Prerequisites

OCR requires Tesseract to be installed on the system (the Python packages alone are not enough):

```bash
# Ubuntu / Debian
sudo apt install tesseract-ocr

# macOS
brew install tesseract

# Windows
# Download installer from https://github.com/tesseract-ocr/tessdoc
```

Tesseract is only needed if you intend to use the OCR feature. The rest of the app works without it.

### Installation

```bash
git clone <repo-url>
cd txtmd-converter
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

### Run

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`.

---

## File Structure

```
txtmd-converter/
├── app.py                  # Streamlit UI
├── converter.py            # Conversion pipeline (3 tiers) + heading heuristics
├── utils.py                # File validation, hashing, page range parsing, ZIP encryption
├── config.py               # Constants
├── requirements.txt        # Python dependencies
├── .gitignore              # Excludes .venv/, __pycache__/, temp/, logs/
├── .streamlit/
│   └── config.toml         # Streamlit server config (max upload 50 MB)
└── logs/                   # Auto-created rotating log directory
```

---

## Conversion Pipeline

```
Upload PDF(s)
  │
  ├─ Valid? ── No ──→ Error message
  │
  ├─ Password required? ── Yes ──→ Authenticate → Fail? → Error
  │
  ├─ Page range specified? ── Yes ──→ Clamp to valid range → Create subset PDF
  │                                   Invalid (no pages match)? → Error
  │
  └─ Valid ✓
       │
       ├─ OCR enabled? ── No ──→ Tier 1: markitdown ── Has markdown syntax? ──→ Return
       │                                └─ No/Fail ──→ Fall through
       │
       ├─ Tier 2: PyMuPDF blocks + heading heuristics
       │      Per page: has text? → block extraction
       │                no text + OCR on? → Tesseract OCR
       │      └─ Fail ──→ Fall through
       │
       └─ Tier 3: PyMuPDF raw text
              Per page: has text? → direct extraction
                        no text + OCR on? → Tesseract OCR

Output:
  No password → .md or .txt download
  Password    → AES-256 encrypted .zip containing the .md or .txt file
```

### Heading detection priority (Tier 2)

| Rule | Example | Result |
|---|---|---|
| Underlined + ≥15px | Underlined Title | `##` |
| Underlined + ≥11px | Underlined Sub | `###` |
| Numbered section | `1.1 Introduction` | `#` |
| ALL CAPS (≥60%) | `CHAPTER OVERVIEW` | `##` or `###` |
| Colon-ending | `Settings:` | `###` or `**` |
| Font-size ≥18px | Large text | `#` |
| Font-size ≥15px | Medium text | `##` |
| Font-size ≥13px | Sub-heading | `###` |

---

## Configuration

All constants in `config.py`:

| Key | Default | Description |
|---|---|---|
| `MAX_FILE_SIZE_MB` | 50 | Max upload size per file |
| `SUPPORTED_FORMATS` | `[".pdf"]` | Allowed extensions |
| `OUTPUT_FORMATS` | `{".md", ".txt"}` | Dropdown options |
| `DEFAULT_FORMAT` | `".md"` | Pre-selected format |
| `LOG_DIR` | `./logs` | Log file directory |
| `LOG_FILE` | `txtmd-converter.log` | Log filename |
| `LOG_MAX_BYTES` | 5 MB | Rotate size |
| `LOG_BACKUP_COUNT` | 3 | Rotated files to keep |

---

## Page Range Syntax

Leave blank to convert all pages. Use comma-separated values and hyphenated ranges (1-based page numbers):

| Input | Pages converted |
|---|---|
| (blank) | All pages |
| `1-5` | Pages 1 through 5 |
| `1,3,5` | Pages 1, 3, and 5 |
| `1-5,7,10-12` | Pages 1–5, 7, and 10–12 |
| `50-200` on a 99-page PDF | Pages 50–99 (end clamped to last page) |

If the range produces no valid pages (e.g., `200-300` on a 100-page PDF), conversion stops with an error message.

---

## Password-Protected Output

When a password is provided in the **Password** field:

1. The input PDF is unlocked using that password (if it is encrypted).
2. After conversion, the download is an **AES-256 encrypted ZIP** file (e.g., `document.md.zip`) containing the `.md` or `.txt` output.
3. Extracting the ZIP with any standard tool (7-Zip, WinRAR, macOS, Windows) will prompt for the password.

Without a password, the output is downloaded as a plain `.md` or `.txt` file.

---

## OCR for Scanned Pages

Enable **"Use OCR for scanned pages"** in the Options expander before clicking Convert.

**How it works:**

- Before conversion, the app scans each page for a text layer.
- Pages with text are converted normally (fast path).
- Pages with no text are rendered at 300 DPI and passed to Tesseract OCR.
- A blue info banner lists which pages will be OCR'd.

**When OCR is off** and scanned pages are detected, a warning lists the affected page numbers and suggests enabling OCR.

**Performance:** OCR is noticeably slower than text extraction — roughly 1–5 seconds per scanned page depending on page complexity and hardware. Only enable it when the PDF contains scanned content.

**Language:** Defaults to English (`eng`). Tesseract supports many languages; to change the default, edit `_OCR_DPI` and the `language` parameter in `converter.py → _ocr_page()`.

**Tier 1 skip:** When OCR is enabled, the MarkItDown tier is skipped entirely. MarkItDown uses `pdfminer` internally which has no OCR capability — it would silently produce empty output for scanned pages. Tiers 2 and 3 handle OCR per-page, so non-scanned pages still get full heading detection.

---

## Deployment

### Streamlit Community Cloud

1. Push to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io).
3. Deploy from repo — entry point `app.py`.

### Docker (self-hosted)

```bash
docker build -t txtmd-converter .
docker run -p 8501:8501 txtmd-converter
```

(Requires `Dockerfile` — not included.)

---

## License

MIT
