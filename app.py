import os
import time
import tempfile
import logging
import threading
import fitz
from pathlib import Path
from logging.handlers import RotatingFileHandler

import streamlit as st

import config
import utils
import converter

config.LOG_DIR.mkdir(exist_ok=True)

log_path = config.LOG_DIR / config.LOG_FILE
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

file_handler = RotatingFileHandler(
    log_path,
    maxBytes=config.LOG_MAX_BYTES,
    backupCount=config.LOG_BACKUP_COUNT,
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")
)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")
)

root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cached conversion  (global across sessions — same file+options = instant hit)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _cached_convert(
    file_bytes: bytes,
    output_format: str,
    pw: str | None,
    page_range: str,
    ocr_enabled: bool,
) -> dict:
    """
    Pure conversion function — no UI side effects.
    @st.cache_data stores the result globally keyed on (file_bytes, output_format,
    pw, page_range, ocr_enabled).  Cache hits return instantly without re-reading
    the PDF, even across page refreshes or new sessions on the same server.
    """
    cleanup = []
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        cleanup.append(tmp_path)

        if pw and utils.is_password_protected(tmp_path):
            doc = fitz.open(tmp_path)
            if doc.authenticate(pw) == 0:
                doc.close()
                raise ValueError("Incorrect password.")
            doc.close()

        total_pages = utils.get_page_count(tmp_path, pw)
        selected_pages = utils.parse_page_range(page_range, total_pages)
        conv_path = tmp_path

        if len(selected_pages) < total_pages:
            src = fitz.open(tmp_path)
            if pw and src.needs_pass:
                src.authenticate(pw)
            sub = fitz.open()
            groups = []
            gs = ge = selected_pages[0]
            for p in selected_pages[1:]:
                if p == ge + 1:
                    ge = p
                else:
                    groups.append((gs, ge))
                    gs = ge = p
            groups.append((gs, ge))
            for s, e in groups:
                sub.insert_pdf(src, from_page=s, to_page=e)
            subset_path = tmp_path + "_subset.pdf"
            sub.save(subset_path)
            sub.close()
            src.close()
            conv_path = subset_path
            cleanup.append(subset_path)
            logger.info("Page range subset: %d pages", len(selected_pages))

        _, empty_pages = utils.has_text_content(conv_path, pw)

        t0 = time.time()
        if output_format == ".md":
            result = converter.convert_pdf_to_markdown(conv_path, pw, ocr=ocr_enabled)
        else:
            result = converter.convert_pdf_to_text(conv_path, pw, ocr=ocr_enabled)

        if not result or not result.strip():
            raise ValueError("No content could be extracted from this PDF.")

        elapsed = time.time() - t0
        ts = f"{elapsed:.1f}s" if elapsed < 3600 else f"{elapsed/60:.1f}m"
        logger.info("Cached convert done: format=%s len=%d time=%s", output_format, len(result), ts)

        return {
            "result": result,
            "format": output_format,
            "char_count": len(result),
            "word_count": len(result.split()),
            "line_count": result.count("\n") + 1,
            "time": ts,
            "empty_pages": empty_pages,
        }
    finally:
        for p in cleanup:
            try:
                os.unlink(p)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Background conversion thread
# ---------------------------------------------------------------------------

def _run_conversion_thread(
    shared: dict,
    files_data: list[dict],
    output_format: str,
    pw: str | None,
    page_range: str,
    ocr_enabled: bool,
) -> None:
    for i, fd in enumerate(files_data):
        if shared["cancel"]:
            shared["cancelled"] = True
            break

        shared["current_file"] = fd["name"]
        shared["file_index"] = i

        try:
            data = _cached_convert(fd["bytes"], output_format, pw, page_range, ocr_enabled)
            shared["results"][fd["hash"]] = {**data, "file_name": fd["name"]}
            shared["empty_pages"][fd["name"]] = data["empty_pages"]
        except ValueError as e:
            shared["errors"][fd["name"]] = str(e)
            logger.error("ValueError for %s: %s", fd["name"], e)
        except Exception as e:
            shared["errors"][fd["name"]] = f"Conversion failed: {e}"
            logger.exception("Thread conversion error: %s", fd["name"])

    shared["done"] = True


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="txtmd-converter",
    page_icon="📄",
    layout="centered",
)

st.title("txtmd-converter")
st.caption("Convert PDF to Markdown or Plain Text")

uploaded_files = st.file_uploader(
    "Upload PDF files",
    type=["pdf"],
    accept_multiple_files=True,
    help=f"Maximum file size: {config.MAX_FILE_SIZE_MB} MB each",
)

output_format = st.selectbox(
    "Output format",
    options=list(config.OUTPUT_FORMATS.keys()),
    format_func=lambda x: config.OUTPUT_FORMATS[x],
    index=0,
)

with st.expander("Options"):
    password = st.text_input(
        "Password",
        type="password",
        help="Unlocks a password-protected PDF. The same password will also protect the downloaded output file.",
    )
    page_range = st.text_input(
        "Page range (e.g., 1-5,7,10-12 — leave blank for all pages)",
        value="",
    )
    enable_ocr = st.checkbox(
        "Use OCR for scanned pages",
        value=False,
        help="Runs Tesseract OCR on pages with no extractable text (scanned/image pages). Slower but recovers text from image-only pages.",
    )

if not uploaded_files:
    st.stop()

file_count = len(uploaded_files)
st.caption(f"{file_count} file(s) selected")

for f in uploaded_files:
    valid, err = utils.validate_file(f)
    if not valid:
        st.error(f"**{f.name}**: {err}")
        st.stop()

pw = password if password else None
ocr_enabled = enable_ocr


def _make_cache_key(fh: str) -> str:
    return f"{fh}_{output_format}_{bool(pw)}_pages_{page_range}_ocr_{ocr_enabled}"


# ---------------------------------------------------------------------------
# Display helper
# ---------------------------------------------------------------------------

def _show_result(result, fmt, fname, ck, cc, wc, lc, ts, pw=None):
    ext = fmt
    base_name = Path(fname).stem
    inner_filename = f"{base_name}{ext}"

    if pw:
        download_data = utils.make_protected_zip(result, inner_filename, pw)
        download_filename = f"{base_name}{ext}.zip"
        mime = "application/zip"
        label = f"Download {ext}.zip (password protected)"
    else:
        download_data = result
        download_filename = inner_filename
        mime = "text/markdown" if ext == ".md" else "text/plain"
        label = f"Download {ext}"

    st.download_button(
        label=label,
        data=download_data,
        file_name=download_filename,
        mime=mime,
        use_container_width=True,
    )

    preview_flag = f"show_preview_{ck}"
    show = st.session_state.get(preview_flag, False)
    if st.button("Hide Preview" if show else "Show Preview", key=f"toggle_{ck}"):
        st.session_state[preview_flag] = not show
        st.rerun()

    if show:
        st.subheader("Preview")
        if fmt == ".md":
            st.markdown(result)
        else:
            st.text_area("Converted text", result, height=400, key=f"area_{ck}")

    st.caption(f"{cc} chars | {wc} words | {lc} lines | took {ts}")


# ---------------------------------------------------------------------------
# Button area — state-aware
# ---------------------------------------------------------------------------

_is_conv = st.session_state.get("_conv_running", False)
_shared = st.session_state.get("_conv_shared")

col_main, col_cancel = st.columns([4, 1])

if _is_conv and _shared is not None:
    fidx = _shared.get("file_index", 0)
    n_files = len(st.session_state.get("_conv_files_data", []))
    cur_file = _shared.get("current_file", "")

    btn_lbl = f"Converting {fidx + 1}/{n_files} — {cur_file}" if cur_file else "Starting..."
    col_main.button(btn_lbl, disabled=True, type="primary", use_container_width=True)

    if col_cancel.button("Cancel", use_container_width=True):
        _shared["cancel"] = True

    files_done = len(_shared.get("results", {})) + len(_shared.get("errors", {}))
    file_pct = files_done / n_files if n_files else 0
    st.progress(
        min(file_pct, 0.99),
        text=f"File {fidx + 1}/{n_files}: {cur_file}" if cur_file else "Starting...",
    )

    if _shared.get("done"):
        for fh, rd in _shared.get("results", {}).items():
            st.session_state[_make_cache_key(fh)] = rd
        st.session_state["_last_errors"] = _shared.get("errors", {})
        st.session_state["_last_empty_pages"] = _shared.get("empty_pages", {})
        st.session_state["_was_cancelled"] = _shared.get("cancelled", False)
        st.session_state["_conv_running"] = False
        st.rerun()
    else:
        time.sleep(0.3)
        st.rerun()

else:
    convert_clicked = col_main.button(
        "Convert All", type="primary", use_container_width=True
    )

    if convert_clicked:
        files_data = [
            {
                "bytes": uf.getvalue(),
                "name": uf.name,
                "hash": utils.get_file_hash(uf.getvalue()),
            }
            for uf in uploaded_files
        ]
        shared = {
            "cancel": False,
            "cancelled": False,
            "done": False,
            "current_file": None,
            "file_index": 0,
            "progress": {},
            "results": {},
            "errors": {},
            "empty_pages": {},
        }
        st.session_state["_conv_running"] = True
        st.session_state["_conv_shared"] = shared
        st.session_state["_conv_files_data"] = files_data
        st.session_state["_last_errors"] = {}
        st.session_state["_last_empty_pages"] = {}
        st.session_state["_was_cancelled"] = False
        threading.Thread(
            target=_run_conversion_thread,
            args=(shared, files_data, output_format, pw, page_range, ocr_enabled),
            daemon=True,
        ).start()
        st.rerun()
    else:
        any_cached = any(
            st.session_state.get(_make_cache_key(utils.get_file_hash(uf.getvalue())))
            for uf in uploaded_files
        )
        if not any_cached:
            st.stop()


# ---------------------------------------------------------------------------
# Post-conversion notifications
# ---------------------------------------------------------------------------

if st.session_state.get("_was_cancelled"):
    st.warning("Conversion cancelled.")

for fname, err in st.session_state.get("_last_errors", {}).items():
    st.error(f"**{fname}**: {err}")

for fname, empty in st.session_state.get("_last_empty_pages", {}).items():
    if empty:
        if ocr_enabled:
            st.info(
                f"**{fname}**: {len(empty)} scanned page(s) detected "
                f"(pages {empty}) — OCR was applied."
            )
        else:
            st.warning(
                f"**{fname}**: Pages {empty} contain no extractable text. "
                "Enable OCR in Options to recover text from these pages."
            )

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

for uploaded_file in uploaded_files:
    file_hash = utils.get_file_hash(uploaded_file.getvalue())
    cache_key = _make_cache_key(file_hash)
    cached = st.session_state.get(cache_key)
    if not cached:
        continue

    with st.container():
        st.markdown("---")
        st.subheader(f"📄 {uploaded_file.name}")
        _show_result(
            cached["result"],
            cached["format"],
            uploaded_file.name,
            cache_key,
            cached["char_count"],
            cached["word_count"],
            cached["line_count"],
            cached["time"],
            pw,
        )
