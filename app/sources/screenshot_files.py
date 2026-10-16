"""Validate and store screenshot uploads (multipart mode of POST /verification-jobs)."""

import hashlib
import io
import json
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from fastapi.exceptions import RequestValidationError
from PIL import Image, UnidentifiedImageError
from starlette.datastructures import FormData, UploadFile

from ..api.errors import ApiError

REQUIRED_FIELDS = ("product", "store")
OPTIONAL_FIELDS = ("description", "low_reviews", "newest_reviews")
SLOT_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS
EXTRA_FIELD = "extra_screenshots"
EXTRA_NAMES_FIELD = "extra_names"
TEXT_FIELDS = ("locale", EXTRA_NAMES_FIELD)

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_IMAGES = 10
MAX_EXTRAS = 5
MAX_NAME_LENGTH = 100
MAX_PIXELS = 40_000_000
LOCALES = ("en-US", "id-ID")

_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


@dataclass
class ImageUpload:
    field: str
    data: bytes
    ext: str
    name: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class ScreenshotUpload:
    locale: str
    slots: dict[str, ImageUpload] = field(default_factory=dict)
    extras: list[ImageUpload] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.slots) + len(self.extras)

    def request_hash(self) -> str:
        fingerprint = {
            "mode": "screenshots",
            "locale": self.locale,
            "slots": {k: v.sha256 for k, v in sorted(self.slots.items())},
            "extras": [[x.name, x.sha256] for x in self.extras],
        }
        return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()


def _issue(loc: str, msg: str, type_: str) -> dict:
    return {"loc": ("body", loc), "msg": msg, "type": type_}


def sniff_format(data: bytes) -> str | None:
    """Image type from the file signature, ignoring filename and client MIME type."""
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def check_image(data: bytes, loc: str) -> str:
    """Return the file extension, or raise 413/415."""
    if len(data) > MAX_FILE_BYTES:
        raise ApiError(413, "file_too_large", f"{loc} is larger than 10 MiB.")
    sniffed = sniff_format(data)
    if sniffed is None:
        raise ApiError(415, "unsupported_image_type", f"{loc} must be a JPEG, PNG or WebP image.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format != sniffed:
                raise ApiError(415, "unsupported_image_type", f"{loc} must be a JPEG, PNG or WebP image.")
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise ApiError(413, "image_too_large", f"{loc} has too many pixels.")
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise ApiError(415, "unsupported_image_type", f"{loc} is not a readable JPEG, PNG or WebP image.")
    return _EXT[sniffed]


async def read_screenshot_form(form: FormData) -> ScreenshotUpload:
    """Validate a screenshot form. Raises 422 (fields), 413 (size) or 415 (type)."""
    issues: list[dict] = []
    keys = list(form.keys())

    for key in dict.fromkeys(keys):
        if key not in SLOT_FIELDS and key not in (EXTRA_FIELD, *TEXT_FIELDS):
            issues.append(_issue(key, "Extra inputs are not permitted", "extra_forbidden"))
        elif key not in (EXTRA_FIELD, EXTRA_NAMES_FIELD) and len(form.getlist(key)) > 1:
            issues.append(_issue(key, "Field must be sent once", "value_error"))

    for key in REQUIRED_FIELDS:
        if key not in form:
            issues.append(_issue(key, "Field required", "missing"))

    files: dict[str, UploadFile] = {}
    for key in SLOT_FIELDS:
        value = form.get(key)
        if value is None:
            continue
        if not isinstance(value, UploadFile):
            issues.append(_issue(key, "Expected an image file", "value_error"))
        else:
            files[key] = value

    extras = form.getlist(EXTRA_FIELD)
    if any(not isinstance(x, UploadFile) for x in extras):
        issues.append(_issue(EXTRA_FIELD, "Expected image files", "value_error"))
    if len(extras) > MAX_EXTRAS:
        issues.append(_issue(EXTRA_FIELD, f"At most {MAX_EXTRAS} extra screenshots are allowed", "too_long"))

    names = form.getlist(EXTRA_NAMES_FIELD)
    if any(not isinstance(n, str) for n in names):
        issues.append(_issue(EXTRA_NAMES_FIELD, "Names must be text", "value_error"))
    elif names and len(names) != len(extras):
        issues.append(_issue(EXTRA_NAMES_FIELD, "Send one name per extra screenshot, or none", "value_error"))
    elif any(len(n) > MAX_NAME_LENGTH for n in names):
        issues.append(_issue(EXTRA_NAMES_FIELD, f"Names can be at most {MAX_NAME_LENGTH} characters", "string_too_long"))

    locale = form.get("locale", "en-US")
    if locale not in LOCALES:
        issues.append(
            {"loc": ("body", "locale"), "msg": "Input should be 'en-US' or 'id-ID'", "type": "literal_error"}
        )

    if issues:
        raise RequestValidationError(issues)

    upload = ScreenshotUpload(locale=str(locale))
    total = 0
    for key, file in files.items():
        data = await file.read()
        if not data:
            raise RequestValidationError([_issue(key, "File is empty", "value_error")])
        total += len(data)
        upload.slots[key] = ImageUpload(field=key, data=data, ext=check_image(data, key))
    for i, file in enumerate(extras):
        data = await file.read()
        if not data:
            raise RequestValidationError([_issue(EXTRA_FIELD, "File is empty", "value_error")])
        total += len(data)
        name = names[i].strip() if names and names[i].strip() else f"Screenshot {i + 1}"
        upload.extras.append(ImageUpload(field=EXTRA_FIELD, data=data, ext=check_image(data, EXTRA_FIELD), name=name))

    if upload.count > MAX_IMAGES:
        raise ApiError(413, "too_many_images", f"At most {MAX_IMAGES} images are allowed.")
    if total > MAX_TOTAL_BYTES:
        raise ApiError(413, "payload_too_large", "Screenshots are larger than 50 MiB in total.")
    return upload


def save_upload(upload: ScreenshotUpload, directory: Path) -> dict:
    """Write files with random names; return the manifest stored on the job."""
    directory.mkdir(parents=True, exist_ok=True)

    def write(image: ImageUpload) -> str:
        filename = f"{secrets.token_hex(8)}.{image.ext}"
        (directory / filename).write_bytes(image.data)
        return filename

    return {
        "dir": str(directory),
        "slots": {key: write(image) for key, image in upload.slots.items()},
        "extras": [{"name": x.name, "file": write(x)} for x in upload.extras],
    }


def remove_upload(directory: str | Path | None) -> None:
    if directory:
        shutil.rmtree(directory, ignore_errors=True)
