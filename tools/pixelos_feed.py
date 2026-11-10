#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 PixelOS
# SPDX-License-Identifier: Apache-2.0

"""Generate and validate PixelOS Updater metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


OTA_METADATA_PATH = "META-INF/com/android/metadata"
REQUIRED_AB_FILES = {
    "payload_metadata.bin",
    "payload.bin",
    "payload_properties.txt",
}
OTA_KEYS = {"datetime", "files", "type", "version"}
OTA_OPTIONAL_KEYS = {"additional_images", "incremental"}
OTA_FILE_REQUIRED_KEYS = {
    "filename",
    "os_patch_level",
    "os_sdk_level",
    "sha256",
    "size",
    "url",
}
OTA_FILE_OPTIONAL_KEYS = {"ota_property_files"}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024


class ValidationError(ValueError):
    """Raised when generated or supplied metadata is unsafe or malformed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_exact_keys(
    value: dict[str, Any],
    required: set[str],
    optional: set[str] | None = None,
    *,
    label: str,
) -> None:
    optional = optional or set()
    actual = set(value)
    missing = required - actual
    unknown = actual - required - optional
    _require(not missing, f"{label} is missing keys: {', '.join(sorted(missing))}")
    _require(not unknown, f"{label} has unknown keys: {', '.join(sorted(unknown))}")


def _require_https(url: Any, label: str) -> str:
    _require(isinstance(url, str) and bool(url), f"{label} must be a non-empty string")
    parsed = urlparse(url)
    _require(
        parsed.scheme.lower() == "https" and bool(parsed.netloc),
        f"{label} must be an absolute HTTPS URL",
    )
    _require(parsed.username is None and parsed.password is None, f"{label} must not contain credentials")
    return url


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_ota_metadata(artifact: Path) -> dict[str, str]:
    _require(artifact.is_file(), f"OTA artifact does not exist: {artifact}")
    try:
        with zipfile.ZipFile(artifact) as archive:
            info = archive.getinfo(OTA_METADATA_PATH)
            _require(info.file_size <= MAX_METADATA_BYTES, "OTA metadata exceeds the size limit")
            raw = archive.read(info)
    except (KeyError, zipfile.BadZipFile) as error:
        raise ValidationError(f"Unable to read {OTA_METADATA_PATH}: {error}") from error

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValidationError("OTA metadata is not valid UTF-8") from error

    metadata: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        _require("=" in raw_line, f"Malformed OTA metadata line {line_number}")
        key, value = raw_line.split("=", 1)
        key = key.strip()
        _require(bool(key), f"Empty OTA metadata key on line {line_number}")
        _require(key not in metadata, f"Duplicate OTA metadata key: {key}")
        metadata[key] = value
    return metadata


def _metadata_value(metadata: dict[str, str], key: str) -> str:
    _require(key in metadata, f"OTA metadata is missing {key}")
    value = metadata[key].strip()
    _require(bool(value), f"OTA metadata value is blank: {key}")
    return value


def parse_ota_property_files(value: Any, package_size: int) -> dict[str, tuple[int, int]]:
    _require(isinstance(value, str) and bool(value.strip()), "ota_property_files must be non-empty")
    ranges: dict[str, tuple[int, int]] = {}
    for token in value.split(","):
        parts = [part.strip() for part in token.split(":", 2)]
        _require(len(parts) == 3, f"Malformed ota_property_files entry: {token}")
        name, offset_text, size_text = parts
        _require(bool(name), "ota_property_files contains an empty name")
        _require(name not in ranges, f"ota_property_files contains duplicate name: {name}")
        try:
            offset = int(offset_text)
            size = int(size_text)
        except ValueError as error:
            raise ValidationError(f"Invalid range for {name}") from error
        _require(offset >= 0 and size > 0, f"Invalid range for {name}")
        _require(offset + size <= package_size, f"Range for {name} exceeds package size")
        ranges[name] = (offset, size)

    # Non-A/B packages only list their metadata.
    if "payload.bin" in ranges:
        missing = REQUIRED_AB_FILES - set(ranges)
        _require(
            not missing,
            "ota_property_files is missing required entries: " + ", ".join(sorted(missing)),
        )
    return ranges


def _metadata_integer(metadata: dict[str, str], key: str) -> int:
    value = _metadata_value(metadata, key)
    try:
        result = int(value)
    except ValueError as error:
        raise ValidationError(f"OTA metadata value is not an integer: {key}") from error
    _require(result > 0, f"OTA metadata value must be positive: {key}")
    return result


def build_ota_file(artifact: Path, url: str) -> dict[str, Any]:
    _require_https(url, "OTA URL")
    metadata = read_ota_metadata(artifact)
    package_size = artifact.stat().st_size
    _require(package_size > 0, "OTA artifact is empty")

    file_metadata: dict[str, Any] = {
        "filename": artifact.name,
        "os_patch_level": _metadata_value(metadata, "post-security-patch-level"),
        "os_sdk_level": _metadata_integer(metadata, "post-sdk-level"),
        "sha256": sha256_file(artifact),
        "size": package_size,
        "url": url,
    }
    if "ota-property-files" in metadata:
        ota_property_files = metadata["ota-property-files"]
        parse_ota_property_files(ota_property_files, package_size)
        file_metadata["ota_property_files"] = ota_property_files
    return file_metadata


def build_ota_feed(artifact: Path, url: str, version: str) -> list[dict[str, Any]]:
    _require(bool(version.strip()), "version must not be blank")
    metadata = read_ota_metadata(artifact)

    feed = [
        {
            "datetime": _metadata_integer(metadata, "post-timestamp"),
            "files": [build_ota_file(artifact, url)],
            "type": "ci",
            "version": version.strip(),
        }
    ]
    validate_ota_feed(feed, artifact=artifact)
    return feed


def add_incremental_to_feed(
    feed: Any,
    incremental_artifact: Path,
    full_artifact: Path,
    url: str,
) -> list[dict[str, Any]]:
    validate_ota_feed(feed)
    _require(len(feed) == 1, "Incremental publishing requires a single current release")
    update = feed[0]
    full_metadata = read_ota_metadata(full_artifact)
    incremental_metadata = read_ota_metadata(incremental_artifact)
    full_file = update["files"][0]

    _require(full_file["filename"] == full_artifact.name, "Full artifact does not match feed filename")
    _require(full_file["size"] == full_artifact.stat().st_size, "Full artifact does not match feed size")
    _require(full_file["sha256"] == sha256_file(full_artifact), "Full artifact does not match feed SHA-256")
    _require(
        update["datetime"] == _metadata_integer(full_metadata, "post-timestamp"),
        "Full artifact does not match feed timestamp",
    )
    _require(
        _metadata_value(incremental_metadata, "ota-type") == "AB",
        "Incremental OTA must be an A/B package",
    )
    _require(
        _metadata_value(incremental_metadata, "post-build")
        == _metadata_value(full_metadata, "post-build"),
        "Incremental target build does not match full OTA",
    )
    _require(
        _metadata_integer(incremental_metadata, "post-timestamp") == update["datetime"],
        "Incremental timestamp does not match full OTA",
    )
    _require(
        _metadata_integer(incremental_metadata, "post-build-incremental")
        == _metadata_integer(full_metadata, "post-build-incremental"),
        "Incremental target version does not match full OTA",
    )
    device = _metadata_value(full_metadata, "pre-device")
    _require(device, "Full OTA is missing pre-device metadata")
    _require(
        _metadata_value(incremental_metadata, "pre-device") == device,
        f"Incremental OTA is not built for {device}",
    )
    _require("pre-build" in incremental_metadata, "Incremental OTA is missing pre-build metadata")
    _require(
        _metadata_integer(incremental_metadata, "pre-build-incremental")
        != _metadata_integer(incremental_metadata, "post-build-incremental"),
        "Incremental source and target builds are identical",
    )

    incremental_file = build_ota_file(incremental_artifact, url)
    ranges = incremental_file.get("ota_property_files")
    _require(ranges is not None, "Incremental OTA is missing ota-property-files metadata")
    range_names = parse_ota_property_files(ranges, incremental_file["size"])
    missing = REQUIRED_AB_FILES - set(range_names)
    _require(not missing, "Incremental OTA is missing A/B payload ranges: " + ", ".join(sorted(missing)))
    _require(
        incremental_file["size"] < full_file["size"],
        "Incremental OTA is not smaller than the full OTA",
    )
    update["incremental"] = [incremental_file]
    validate_ota_feed(feed, incremental_artifact=incremental_artifact)
    return feed


def validate_ota_feed(
    data: Any,
    artifact: Path | None = None,
    incremental_artifact: Path | None = None,
) -> None:
    _require(isinstance(data, list) and bool(data), "OTA feed must be a non-empty JSON array")
    for update_index, update in enumerate(data):
        label = f"update[{update_index}]"
        _require(isinstance(update, dict), f"{label} must be an object")
        _require_exact_keys(update, OTA_KEYS, OTA_OPTIONAL_KEYS, label=label)
        _require(_is_integer(update["datetime"]) and update["datetime"] > 0, f"{label}.datetime must be positive")
        _require(update["type"] == "ci", f"{label}.type must be ci")
        _require(isinstance(update["version"], str) and bool(update["version"].strip()), f"{label}.version must not be blank")
        files = update["files"]
        _require(isinstance(files, list) and len(files) == 1, f"{label}.files must contain exactly one file")
        _validate_ota_file(files[0], f"{label}.files[0]")
        if "incremental" in update:
            incrementals = update["incremental"]
            _require(
                isinstance(incrementals, list) and len(incrementals) == 1,
                f"{label}.incremental must contain exactly one file",
            )
            incremental_file = incrementals[0]
            _validate_ota_file(incremental_file, f"{label}.incremental[0]")
            _require(
                "ota_property_files" in incremental_file,
                f"{label}.incremental[0] is missing ota_property_files",
            )
            _require(
                incremental_file["size"] < files[0]["size"],
                f"{label}.incremental[0] is not smaller than the full OTA",
            )
            ranges = parse_ota_property_files(
                incremental_file["ota_property_files"], incremental_file["size"]
            )
            missing = REQUIRED_AB_FILES - set(ranges)
            _require(
                not missing,
                f"{label}.incremental[0] is missing A/B payload ranges: " + ", ".join(sorted(missing)),
            )

    if artifact is not None:
        _require(len(data) == 1, "Artifact comparison requires exactly one update")
        _require(artifact.is_file(), f"OTA artifact does not exist: {artifact}")
        update = data[0]
        file_value = update["files"][0]
        metadata = read_ota_metadata(artifact)
        _require(file_value["filename"] == artifact.name, "OTA filename does not match artifact")
        _require(file_value["size"] == artifact.stat().st_size, "OTA size does not match artifact")
        _require(file_value["sha256"] == sha256_file(artifact), "OTA SHA-256 does not match artifact")
        _require(update["datetime"] == _metadata_integer(metadata, "post-timestamp"), "OTA datetime does not match metadata")
        _require(file_value["os_patch_level"] == _metadata_value(metadata, "post-security-patch-level"), "OTA patch level does not match metadata")
        _require(file_value["os_sdk_level"] == _metadata_integer(metadata, "post-sdk-level"), "OTA SDK level does not match metadata")
        metadata_ranges = metadata.get("ota-property-files")
        _require(
            file_value.get("ota_property_files") == metadata_ranges,
            "ota_property_files does not match OTA metadata",
        )

    if incremental_artifact is not None:
        _require(len(data) == 1, "Incremental artifact comparison requires exactly one update")
        _require(incremental_artifact.is_file(), f"Incremental artifact does not exist: {incremental_artifact}")
        update = data[0]
        _require("incremental" in update, "Feed is missing an incremental entry")
        file_value = update["incremental"][0]
        metadata = read_ota_metadata(incremental_artifact)
        _require(file_value["filename"] == incremental_artifact.name, "Incremental filename does not match artifact")
        _require(file_value["size"] == incremental_artifact.stat().st_size, "Incremental size does not match artifact")
        _require(file_value["sha256"] == sha256_file(incremental_artifact), "Incremental SHA-256 does not match artifact")
        _require(file_value.get("ota_property_files") == metadata.get("ota-property-files"),
                 "Incremental ota_property_files does not match artifact metadata")
        _require(update["datetime"] == _metadata_integer(metadata, "post-timestamp"),
                 "Incremental timestamp does not match feed")


def _validate_ota_file(file_value: Any, label: str) -> None:
    _require(isinstance(file_value, dict), f"{label} must be an object")
    _require_exact_keys(file_value, OTA_FILE_REQUIRED_KEYS, OTA_FILE_OPTIONAL_KEYS, label=label)
    for key in ("filename", "os_patch_level"):
        _require(
            isinstance(file_value[key], str) and bool(file_value[key].strip()),
            f"{label}.{key} must not be blank",
        )
    _require(
        _is_integer(file_value["os_sdk_level"]) and file_value["os_sdk_level"] > 0,
        f"{label}.os_sdk_level must be positive",
    )
    _require(
        isinstance(file_value["sha256"], str) and bool(SHA256_RE.fullmatch(file_value["sha256"])),
        f"{label}.sha256 must be lowercase hexadecimal",
    )
    _require(
        _is_integer(file_value["size"]) and file_value["size"] > 0,
        f"{label}.size must be positive",
    )
    _require_https(file_value["url"], f"{label}.url")
    if "ota_property_files" in file_value:
        parse_ota_property_files(file_value["ota_property_files"], file_value["size"])


def load_json(path: Path, max_bytes: int = MAX_JSON_BYTES) -> Any:
    _require(path.is_file(), f"JSON file does not exist: {path}")
    _require(path.stat().st_size <= max_bytes, "JSON file exceeds the size limit")
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValidationError(f"Invalid JSON: {error}") from error


def write_json(data: Any, output: str) -> None:
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if output == "-":
        sys.stdout.write(rendered)
    else:
        Path(output).write_text(rendered, encoding="utf-8")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_ota = subparsers.add_parser("generate-ota", help="generate one OTA feed entry")
    generate_ota.add_argument("artifact", type=Path)
    generate_ota.add_argument("--url", required=True)
    generate_ota.add_argument("--version", required=True)
    generate_ota.add_argument("--output", default="-")

    validate_ota = subparsers.add_parser("validate-ota", help="validate an OTA feed")
    validate_ota.add_argument("feed", type=Path)
    validate_ota.add_argument("--artifact", type=Path)
    validate_ota.add_argument("--incremental-artifact", type=Path)

    add_incremental = subparsers.add_parser(
        "add-incremental", help="add one source-specific incremental OTA to a feed"
    )
    add_incremental.add_argument("feed", type=Path)
    add_incremental.add_argument("artifact", type=Path)
    add_incremental.add_argument("--full-artifact", type=Path, required=True)
    add_incremental.add_argument("--url", required=True)
    add_incremental.add_argument("--output", default="-")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate-ota":
            write_json(
                build_ota_feed(args.artifact, args.url, args.version),
                args.output,
            )
        elif args.command == "validate-ota":
            validate_ota_feed(
                load_json(args.feed),
                artifact=args.artifact,
                incremental_artifact=args.incremental_artifact,
            )
        elif args.command == "add-incremental":
            write_json(
                add_incremental_to_feed(
                    load_json(args.feed), args.artifact, args.full_artifact, args.url
                ),
                args.output,
            )
        else:  # pragma: no cover - argparse enforces the command choices.
            parser.error(f"Unknown command: {args.command}")
    except (OSError, ValidationError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
