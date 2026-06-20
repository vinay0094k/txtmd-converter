from pathlib import Path

MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

SUPPORTED_FORMATS = [".pdf"]
OUTPUT_FORMATS = {
    ".md": "Markdown (.md)",
    ".txt": "Plain Text (.txt)",
}
DEFAULT_FORMAT = ".md"

TEMP_DIR = Path("./temp")

LOG_DIR = Path("./logs")
LOG_FILE = "txtmd-converter.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
