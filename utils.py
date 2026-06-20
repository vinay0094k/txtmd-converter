import hashlib
import io
import logging
import fitz
import config

logger = logging.getLogger(__name__)


def validate_file(uploaded_file) -> tuple[bool, str]:
    if uploaded_file is None:
        return False, "No file uploaded."

    if uploaded_file.size > config.MAX_FILE_SIZE_BYTES:
        return False, f"File exceeds {config.MAX_FILE_SIZE_MB} MB limit."

    if not uploaded_file.name.lower().endswith(".pdf"):
        return False, "Only PDF files are supported."

    return True, ""


def get_file_hash(file_bytes: bytes) -> str:
    return hashlib.md5(file_bytes).hexdigest()


def is_password_protected(file_path: str) -> bool:
    try:
        doc = fitz.open(file_path)
        needs_pass = doc.needs_pass
        doc.close()
        return needs_pass
    except Exception:
        return False


def has_text_content(file_path: str, password: str = None) -> tuple[bool, list[int]]:
    empty_pages = []
    try:
        doc = fitz.open(file_path)
        if password and doc.needs_pass:
            doc.authenticate(password)
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            if not text:
                empty_pages.append(i + 1)
        doc.close()
    except Exception as e:
        logger.warning(f"Could not check text content: {e}")
        return True, []
    return len(empty_pages) == 0, empty_pages


def get_page_count(file_path: str, password: str = None) -> int:
    try:
        doc = fitz.open(file_path)
        if password and doc.needs_pass:
            doc.authenticate(password)
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0


def parse_page_range(range_str: str, total_pages: int) -> list[int]:
    if not range_str or not range_str.strip():
        return list(range(total_pages))
    pages = set()
    invalid_parts = []
    for part in range_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            parts = part.split("-", 1)
            try:
                start = int(parts[0].strip())
                end = int(parts[1].strip())
                if start < 1 or start > total_pages:
                    invalid_parts.append(part)
                    continue
                end = min(end, total_pages)
                if start > end:
                    invalid_parts.append(part)
                    continue
                pages.update(range(start - 1, end))
            except ValueError:
                invalid_parts.append(part)
                continue
        else:
            try:
                p = int(part)
                if 1 <= p <= total_pages:
                    pages.add(p - 1)
                else:
                    invalid_parts.append(part)
            except ValueError:
                invalid_parts.append(part)
                continue
    result = sorted(pages)
    if not result:
        raise ValueError(
            f"Page range '{range_str}' matched no pages in this {total_pages}-page PDF. "
            f"Use 1-based page numbers (e.g., 1-{total_pages})."
        )
    return result


def make_protected_zip(content: str, inner_filename: str, password: str) -> bytes:
    """Return bytes of an AES-256 encrypted ZIP containing `inner_filename`."""
    import pyzipper

    buf = io.BytesIO()
    with pyzipper.AESZipFile(
        buf,
        mode="w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.writestr(inner_filename, content.encode("utf-8"))
    return buf.getvalue()
