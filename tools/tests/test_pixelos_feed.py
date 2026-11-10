# SPDX-FileCopyrightText: 2026 PixelOS
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "pixelos_feed.py"
SPEC = importlib.util.spec_from_file_location("pixelos_feed", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
pixelos_feed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pixelos_feed)


class PixelOsFeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.directory = Path(self.temp_directory.name)

    def create_ota(
        self,
        metadata_overrides: dict[str, str] | None = None,
        padding: int = 0,
        filename: str = "PixelOS_device-17.0-20260619-0000.zip",
    ) -> Path:
        metadata = {
            "ota-type": "AB",
            "pre-device": "nuwa",
            "post-timestamp": "1781858358",
            "post-build": "Xiaomi/nuwa/nuwa:17/target",
            "post-build-incremental": "1781858358",
            "post-security-patch-level": "2026-06-01",
            "post-sdk-level": "36",
            "ota-property-files": (
                "payload_metadata.bin:0:1,payload.bin:1:2,"
                "payload_properties.txt:3:1"
            ),
        }
        metadata.update(metadata_overrides or {})
        artifact = self.directory / filename
        rendered = "\n".join(f"{key}={value}" for key, value in metadata.items()) + "\n"
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr(pixelos_feed.OTA_METADATA_PATH, rendered)
            archive.writestr("payload.bin", b"payload")
            if padding:
                archive.writestr("padding", b"x" * padding)
        return artifact

    def test_generate_and_validate_ota_feed(self) -> None:
        artifact = self.create_ota()
        feed = pixelos_feed.build_ota_feed(
            artifact,
            "https://downloads.pixelos.net/device/update.zip",
            "17.0",
        )

        pixelos_feed.validate_ota_feed(feed, artifact=artifact)
        update = feed[0]
        self.assertEqual(1781858358, update["datetime"])
        self.assertEqual(pixelos_feed.sha256_file(artifact), update["files"][0]["sha256"])
        self.assertEqual(artifact.stat().st_size, update["files"][0]["size"])

    def test_ota_feed_rejects_more_than_one_file(self) -> None:
        feed = pixelos_feed.build_ota_feed(
            self.create_ota(),
            "https://downloads.pixelos.net/device/update.zip",
            "17.0",
        )
        feed[0]["files"].append(dict(feed[0]["files"][0]))

        with self.assertRaisesRegex(pixelos_feed.ValidationError, "exactly one"):
            pixelos_feed.validate_ota_feed(feed)

    def test_ota_feed_rejects_uppercase_hash(self) -> None:
        feed = pixelos_feed.build_ota_feed(
            self.create_ota(),
            "https://downloads.pixelos.net/device/update.zip",
            "17.0",
        )
        feed[0]["files"][0]["sha256"] = "A" * 64

        with self.assertRaisesRegex(pixelos_feed.ValidationError, "lowercase"):
            pixelos_feed.validate_ota_feed(feed)

    def test_ota_feed_rejects_unknown_keys(self) -> None:
        feed = pixelos_feed.build_ota_feed(
            self.create_ota(),
            "https://downloads.pixelos.net/device/update.zip",
            "17.0",
        )
        feed[0]["unsupported"] = True

        with self.assertRaisesRegex(pixelos_feed.ValidationError, "unknown keys"):
            pixelos_feed.validate_ota_feed(feed)

    def test_ota_ranges_must_fit_artifact(self) -> None:
        artifact = self.create_ota(
            {"ota-property-files": "payload_metadata.bin:0:1,payload.bin:1:999999,payload_properties.txt:3:1"}
        )

        with self.assertRaisesRegex(pixelos_feed.ValidationError, "exceeds package size"):
            pixelos_feed.build_ota_feed(
                artifact,
                "https://downloads.pixelos.net/device/update.zip",
                "17.0",
            )

    def test_artifact_comparison_detects_hash_mismatch(self) -> None:
        artifact = self.create_ota()
        feed = pixelos_feed.build_ota_feed(
            artifact,
            "https://downloads.pixelos.net/device/update.zip",
            "17.0",
        )
        feed[0]["files"][0]["sha256"] = "0" * 64

        with self.assertRaisesRegex(pixelos_feed.ValidationError, "does not match artifact"):
            pixelos_feed.validate_ota_feed(feed, artifact=artifact)

    def test_add_incremental_to_feed(self) -> None:
        full = self.create_ota(padding=2048)
        incremental = self.create_ota(
            {
                "pre-build": "Xiaomi/nuwa/nuwa:17/source",
                "pre-build-incremental": "1781850000",
                "ota-property-files": (
                    "payload_metadata.bin:0:1,payload.bin:1:2,"
                    "payload_properties.txt:3:1"
                ),
            },
            filename="PixelOS_nuwa-incremental-20260619.zip",
        )
        feed = pixelos_feed.build_ota_feed(
            full, "https://downloads.pixelos.net/nuwa/full.zip", "17.0"
        )
        feed[0]["files"][0].pop("ota_property_files")

        result = pixelos_feed.add_incremental_to_feed(
            feed,
            incremental,
            full,
            "https://downloads.pixelos.net/nuwa/incremental.zip",
        )

        pixelos_feed.validate_ota_feed(result, incremental_artifact=incremental)
        entry = result[0]["incremental"][0]
        self.assertEqual(incremental.name, entry["filename"])
        self.assertIn("payload_metadata.bin", entry["ota_property_files"])

    def test_add_incremental_rejects_wrong_target_build(self) -> None:
        full = self.create_ota(padding=2048)
        incremental = self.create_ota(
            {
                "pre-build": "Xiaomi/nuwa/nuwa:17/source",
                "pre-build-incremental": "1781850000",
                "post-build": "Xiaomi/nuwa/nuwa:17/other-target",
            },
            filename="PixelOS_nuwa-incremental-20260619.zip",
        )
        feed = pixelos_feed.build_ota_feed(
            full, "https://downloads.pixelos.net/nuwa/full.zip", "17.0"
        )

        with self.assertRaisesRegex(pixelos_feed.ValidationError, "target build"):
            pixelos_feed.add_incremental_to_feed(
                feed,
                incremental,
                full,
                "https://downloads.pixelos.net/nuwa/incremental.zip",
            )

    def test_incremental_may_upgrade_security_patch_level(self) -> None:
        full = self.create_ota(padding=2048)
        incremental = self.create_ota(
            {
                "pre-build": "Xiaomi/nuwa/nuwa:17/source",
                "pre-build-incremental": "1781850000",
                "post-security-patch-level": "2026-07-01",
            },
            filename="PixelOS_nuwa-incremental-20260619.zip",
        )
        feed = pixelos_feed.build_ota_feed(
            full, "https://downloads.pixelos.net/nuwa/full.zip", "17.0"
        )

        result = pixelos_feed.add_incremental_to_feed(
            feed,
            incremental,
            full,
            "https://downloads.pixelos.net/nuwa/incremental.zip",
        )

        self.assertEqual("2026-07-01", result[0]["incremental"][0]["os_patch_level"])

    def test_incremental_allows_fixed_fingerprint_with_distinct_versions(self) -> None:
        full = self.create_ota(padding=2048)
        incremental = self.create_ota(
            {
                "pre-build": "Xiaomi/nuwa/nuwa:17/target",
                "pre-build-incremental": "1781850000",
            },
            filename="PixelOS_nuwa-incremental-20260619.zip",
        )
        feed = pixelos_feed.build_ota_feed(
            full, "https://downloads.pixelos.net/nuwa/full.zip", "17.0"
        )

        result = pixelos_feed.add_incremental_to_feed(
            feed, incremental, full, "https://downloads.pixelos.net/nuwa/incremental.zip"
        )

        self.assertEqual(incremental.name, result[0]["incremental"][0]["filename"])

    def test_incremental_rejects_identical_versions(self) -> None:
        full = self.create_ota(padding=2048)
        incremental = self.create_ota(
            {
                "pre-build": "Xiaomi/nuwa/nuwa:17/target",
                "pre-build-incremental": "1781858358",
            },
            filename="PixelOS_nuwa-incremental-20260619.zip",
        )
        feed = pixelos_feed.build_ota_feed(
            full, "https://downloads.pixelos.net/nuwa/full.zip", "17.0"
        )

        with self.assertRaisesRegex(pixelos_feed.ValidationError, "source and target builds are identical"):
            pixelos_feed.add_incremental_to_feed(
                feed, incremental, full, "https://downloads.pixelos.net/nuwa/incremental.zip"
            )

    def test_incremental_rejects_wrong_target_version(self) -> None:
        full = self.create_ota(padding=2048)
        incremental = self.create_ota(
            {
                "pre-build": "Xiaomi/nuwa/nuwa:17/target",
                "pre-build-incremental": "1781850000",
                "post-build-incremental": "1781858359",
            },
            filename="PixelOS_nuwa-incremental-20260619.zip",
        )
        feed = pixelos_feed.build_ota_feed(
            full, "https://downloads.pixelos.net/nuwa/full.zip", "17.0"
        )

        with self.assertRaisesRegex(pixelos_feed.ValidationError, "target version does not match"):
            pixelos_feed.add_incremental_to_feed(
                feed, incremental, full, "https://downloads.pixelos.net/nuwa/incremental.zip"
            )

    def test_incremental_feed_requires_payload_ranges(self) -> None:
        feed = pixelos_feed.build_ota_feed(
            self.create_ota(), "https://downloads.pixelos.net/nuwa/full.zip", "17.0"
        )
        delta = dict(feed[0]["files"][0])
        delta.pop("ota_property_files")
        feed[0]["incremental"] = [delta]
        with self.assertRaisesRegex(pixelos_feed.ValidationError, "missing ota_property_files"):
            pixelos_feed.validate_ota_feed(feed)

    def test_incremental_must_be_smaller_than_full(self) -> None:
        feed = pixelos_feed.build_ota_feed(
            self.create_ota(), "https://downloads.pixelos.net/nuwa/full.zip", "17.0"
        )
        delta = dict(feed[0]["files"][0])
        delta["ota_property_files"] = (
            "payload_metadata.bin:0:1,payload.bin:1:2,"
            "payload_properties.txt:3:1"
        )
        feed[0]["incremental"] = [delta]

        with self.assertRaisesRegex(pixelos_feed.ValidationError, "not smaller"):
            pixelos_feed.validate_ota_feed(feed)

if __name__ == "__main__":
    unittest.main()
