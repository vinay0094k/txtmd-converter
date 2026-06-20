import logging
import re
import fitz

logger = logging.getLogger(__name__)

_OCR_DPI = 300


def convert_pdf_to_markdown(
    file_path: str,
    password: str = None,
    progress_cb: callable = None,
    pages: list[int] = None,
    ocr: bool = False,
) -> str:
    # Skip Tier 1 when OCR is requested — MarkItDown has no OCR capability
    # and would silently drop scanned pages.
    if not ocr:
        result = _tier1_markitdown(file_path)
        if result:
            if progress_cb:
                progress_cb(1, 1)
            return result

    result = _tier2_blocks(file_path, password, progress_cb, pages, ocr)
    if result:
        return result

    if progress_cb:
        progress_cb(1, 1)
    return _tier3_raw_text(file_path, password, pages=pages, ocr=ocr)


def convert_pdf_to_text(
    file_path: str,
    password: str = None,
    progress_cb: callable = None,
    pages: list[int] = None,
    ocr: bool = False,
) -> str:
    return _tier3_raw_text(file_path, password, progress_cb, pages, ocr)


def _tier1_markitdown(file_path: str) -> str | None:
    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(file_path)
        if result and result.text_content and len(result.text_content.strip()) > 20:
            text = result.text_content
            if _has_markdown_syntax(text):
                logger.info("Tier 1 (markitdown) succeeded")
                return text
            logger.info("Tier 1 output lacks markdown formatting, falling through")
    except Exception as e:
        logger.warning(f"Tier 1 (markitdown) failed: {e}")
    return None


def _has_markdown_syntax(text: str) -> bool:
    patterns = [
        r"^#{1,6}\s",
        r"\*\*.*?\*\*",
        r"\*.*?\*",
        r"^[-*+]\s",
        r"^\d+\.\s",
        r"^>\s",
        r"```",
        r"\|.*\|.*\|",
        r"!\[.*\]\(.*\)",
        r"\[.*\]\(.*\)",
    ]
    return any(re.search(p, text, re.MULTILINE) for p in patterns)


def _ocr_page(page: fitz.Page, page_num: int) -> str:
    try:
        tp = page.get_textpage_ocr(dpi=_OCR_DPI, full=True, language="eng")
        text = page.get_text("text", textpage=tp).strip()
        if text:
            logger.info("OCR succeeded for page %d", page_num + 1)
        return text
    except Exception as e:
        logger.warning("OCR failed for page %d: %s", page_num + 1, e)
        return ""


def _tier2_blocks(
    file_path: str,
    password: str = None,
    progress_cb: callable = None,
    pages: list[int] = None,
    ocr: bool = False,
) -> str | None:
    try:
        doc = fitz.open(file_path)
        if password and doc.needs_pass:
            if doc.authenticate(password) == 0:
                doc.close()
                raise ValueError("Incorrect password")
        elif doc.needs_pass:
            doc.close()
            raise ValueError("Password required")

        if pages is None:
            pages = list(range(len(doc)))
        total = len(pages)

        pages_md = []
        for idx, page_num in enumerate(pages):
            page = doc[page_num]

            # If the page has no text and OCR is enabled, use OCR output directly.
            raw_check = page.get_text("text").strip()
            if not raw_check and ocr:
                ocr_text = _ocr_page(page, page_num)
                if ocr_text:
                    pages_md.append(ocr_text)
                if progress_cb:
                    progress_cb(idx + 1, total)
                continue

            blocks = page.get_text("dict", sort=True)["blocks"]

            page_elements = []
            for block in blocks:
                if block["type"] != 0:
                    continue

                max_font_size = 0
                block_lines = []
                has_underline = False
                for line in block["lines"]:
                    line_text = ""
                    for span in line["spans"]:
                        max_font_size = max(max_font_size, span["size"])
                        line_text += span["text"]
                        if span["flags"] & 8 or span["flags"] & 4:
                            has_underline = True
                    block_lines.append(line_text)

                block_text = " ".join(block_lines).strip()
                if not block_text:
                    continue

                prefix = _detect_heading(block_text, max_font_size, has_underline)
                if prefix:
                    page_elements.append(f"{prefix} {block_text}")
                else:
                    page_elements.append(block_text)

            pages_md.append("\n\n".join(page_elements))

            if progress_cb:
                progress_cb(idx + 1, total)

        doc.close()

        result = "\n\n---\n\n".join(pages_md).strip()
        if len(result) > 20:
            logger.info("Tier 2 (PyMuPDF blocks) succeeded")
            return result
    except ValueError:
        raise
    except Exception as e:
        logger.warning(f"Tier 2 (PyMuPDF blocks) failed: {e}")

    return None


def _detect_heading(text: str, max_font_size: float, has_underline: bool) -> str | None:
    stripped = text.strip()

    # Underlined text → treat as heading (common in docs)
    if has_underline and max_font_size >= 11:
        if max_font_size >= 15:
            return "##"
        return "###"

    # Numbered heading: "1. Introduction", "1.1 Subsection", "1.1.1 Detail"
    if re.match(r"^\d+(\.\d+)*\s+", stripped):
        if max_font_size >= 13:
            return "#"
        return "##"

    # ALL CAPS heading: at least 2 words, most chars are uppercase
    words = [w.strip("().:;!?-") for w in stripped.split() if w.strip("().:;!?-")]
    alpha_words = [w for w in words if len(w) > 1 and w.isalpha()]
    if len(alpha_words) >= 2:
        upper_count = sum(1 for w in alpha_words if w.isupper())
        if upper_count / len(alpha_words) > 0.6:
            if max_font_size >= 13:
                return "##"
            return "###"

    # Line ending with colon (label-style heading)
    if stripped.endswith(":") and len(stripped) > 5 and max_font_size >= 11:
        if max_font_size >= 14:
            return "###"
        return "**"

    # Font-size based (classic detection)
    if max_font_size >= 18:
        return "#"
    if max_font_size >= 15:
        return "##"
    if max_font_size >= 13:
        return "###"

    return None


def _tier3_raw_text(
    file_path: str,
    password: str = None,
    progress_cb: callable = None,
    pages: list[int] = None,
    ocr: bool = False,
) -> str:
    logger.info("Using Tier 3 (raw text)")
    doc = fitz.open(file_path)
    if password and doc.needs_pass:
        if doc.authenticate(password) == 0:
            doc.close()
            raise ValueError("Incorrect password")
    elif doc.needs_pass:
        doc.close()
        raise ValueError("Password required")

    if pages is None:
        pages = list(range(len(doc)))
    total = len(pages)

    text_parts = []
    for idx, page_num in enumerate(pages):
        page = doc[page_num]
        text = page.get_text("text").strip()
        if not text and ocr:
            text = _ocr_page(page, page_num)
        if text:
            text_parts.append(text)
        if progress_cb:
            progress_cb(idx + 1, total)

    doc.close()
    return "\n\n".join(text_parts)
