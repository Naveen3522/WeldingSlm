"""Receiving uploaded Word documents safely: read the multipart form, check each file really is a readable document, and save it.

Nothing is saved that is not a .docx with readable text; a file never replaces an existing one unless asked; a name can never reach outside the folder.
"""
import io
import time
import re
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

MAX_FILE = 25 * 1024 * 1024
MAX_FILES = 10
MIN_TEXT = 80                                            # the ingestion also drops a file with almost no text
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,120}\.docx$")


class UploadError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def parse_multipart(content_type, body):
    """-> [(file name, bytes)] for the file parts of a multipart/form-data body, in order."""
    if not content_type or not content_type.lower().startswith("multipart/form-data") or "boundary=" not in content_type:
        raise UploadError("Send the files as a form upload (multipart/form-data).")
    message = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    if not message.is_multipart():
        raise UploadError("That was not a form upload.")
    out = []
    for part in message.iter_parts():
        name = part.get_filename()
        if name is not None:
            out.append((name, part.get_payload(decode=True) or b""))
    return out


def clean_name(name):
    """The file name with any folder part removed; the reason it is unusable as a plain sentence, or the name."""
    base = re.split(r"[\\/]", name or "")[-1].strip()
    if not base:
        raise UploadError("The file has no name.")
    if base.startswith("~$"):
        raise UploadError("That is a temporary Word file.")
    if not base.lower().endswith(".docx"):
        raise UploadError("Only Word documents (.docx) can be added.")
    if ".." in base or not SAFE_NAME.match(base):
        raise UploadError("The file name may use letters, digits, spaces and . _ ( ) - only (and must end in .docx).")
    return base


def check_document(data):
    """Raises UploadError unless the bytes are a Word document whose text can be read. -> the number of characters of text."""
    if not data:
        raise UploadError("The file is empty.")
    if len(data) > MAX_FILE:
        raise UploadError(f"The file is larger than {MAX_FILE // (1024 * 1024)} MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "word/document.xml" not in z.namelist():
                raise UploadError("This is not a Word document (no document body inside).")
            if sum(i.file_size for i in z.infolist()) > 40 * len(data) + 5_000_000:
                raise UploadError("The file expands to an unreasonable size.")
    except zipfile.BadZipFile:
        raise UploadError("This is not a valid .docx file.")
    import docx
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception:
        raise UploadError("The document could not be opened.")
    text = "\n".join(p.text for p in document.paragraphs) + "\n".join(c.text for t in document.tables for row in t.rows for c in row.cells)
    if len(text.strip()) < MIN_TEXT:
        raise UploadError(f"The document has almost no text ({len(text.strip())} characters), so there is nothing to search. Scanned pages need text recognition first.")
    return len(text)


def save_all(folder, files, replace=False):
    """-> {"saved": [{name, size, replaced}], "rejected": [{name, reason}]}. Each file is judged on its own; one bad file does not stop the others."""
    folder = Path(folder)
    if len(files) > MAX_FILES:
        raise UploadError(f"At most {MAX_FILES} files at a time.")
    out = {"saved": [], "rejected": []}
    for raw_name, data in files:
        try:
            name = clean_name(raw_name)
            check_document(data)
            target = folder / name
            existed = target.exists()
            if existed and not replace:
                raise UploadError("A document with this name already exists. Rename the file, or choose to replace it.")
            if folder.resolve() not in target.resolve().parents:
                raise UploadError("The file name is not allowed.")
            folder.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(".docx.part")
            temp.write_bytes(data)
            temp.replace(target)                         # the old file is never left half-written
            out["saved"].append({"name": name, "size": len(data), "replaced": existed})
        except UploadError as e:
            out["rejected"].append({"name": raw_name, "reason": e.message})
    return out


def delete_all(folder, names, trash):
    """-> {"deleted": [names], "missing": [names]}. A deleted document is moved into the `trash` folder (stamped with the time), not erased, so a mistake can be undone by hand.
    The trash must be outside the documents folder: the indexer reads every subfolder of it."""
    folder, trash = Path(folder), Path(trash)
    out = {"deleted": [], "missing": []}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for raw in names:
        try:
            name = clean_name(raw)
        except UploadError:
            out["missing"].append(str(raw))
            continue
        source = folder / name
        if not source.is_file() or folder.resolve() not in source.resolve().parents:
            out["missing"].append(name)
            continue
        trash.mkdir(parents=True, exist_ok=True)
        source.replace(trash / f"{stamp}-{name}")
        out["deleted"].append(name)
    return out
