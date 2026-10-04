<!--
SPDX-FileCopyrightText: The LineageOS Project
SPDX-FileCopyrightText: 2026 PixelOS
SPDX-License-Identifier: Apache-2.0
-->

# PixelOS Updater

PixelOS Updater downloads, verifies, and installs full OTA packages. The codebase retains its
LineageOS ancestry, but its package identity, product properties, server contract, storage,
branding, and optional PixelOS services are defined for PixelOS.

## PixelOS integration contract

The application is a platform-signed privileged `system_ext` app with package name
`net.pixelos.ota`. Add the `Updater` module to the product packages. Its Soong definition pulls in
the following required modules automatically:

- `default-permissions_net.pixelos.ota`, which grants the runtime notification permission by
  default;
- `init.pixelos-updater.rc`, which creates `/data/system_updates` as `system:cache` with mode
  `0770` and required file-based encryption.

The privileged permissions that require allowlisting are declared in `app/net.pixelos.ota.xml`.
Keep that allowlist installed with the app. Package-install permissions remain in the manifest and
are granted through the platform signature. Downloaded packages live in `/data/system_updates`;
exported packages use the public `PixelOS updates/` directory.

The surrounding PixelOS sepolicy must retain this file-context mapping (currently provided by
`device/custom/sepolicy/private/file_contexts`):

```text
/data/system_updates(/.*)?    u:object_r:ota_package_file:s0
```

It must also retain the `seapp_contexts` rule that assigns platform-signed `net.pixelos.ota` to
the `updater_app` domain. PixelOS inherits that domain's update_engine, OTA-file, custom-property,
and recovery-property rules from `device/lineage/sepolicy/common/private/updater_app.te`. Without
the package-specific domain mapping, the Java permission declarations alone are not sufficient.

The build must provide these properties:

| Property | Meaning | Used for |
| --- | --- | --- |
| `ro.custom.device` | PixelOS device codename | OTA and changelog filenames |
| `ro.custom.version` | Installed PixelOS version | Displayed build version |
| `net.pixelos.version` | Official-devices branch | OTA and changelog URLs |
| `ro.build.date.utc` | Installed build timestamp | Rejecting current and older OTAs |
| `ro.build.ab_update` | A/B capability | Streaming and performance-mode availability |

All three PixelOS-specific properties must be non-empty in production. Missing device or branch
properties make an update check fail closed instead of querying an ambiguous feed.
Downgrades are always rejected. A resource overlay can disallow cross-SDK upgrades.

Product overlays may change the following booleans in `app/src/main/res/values/config.xml`:

| Resource | Default | Effect |
| --- | --- | --- |
| `config_ab_perf_mode` | `true` | Shows and enables update-engine performance mode on A/B devices |
| `config_allowMajorUpgrades` | `true` | Allows installing an OTA with a newer SDK level |
| `config_hideRecoveryUpdate` | `false` | Hides the recovery-update preference when set |

The default automatic check interval is two weeks. On first launch, the app migrates the legacy
SharedPreferences values for performance mode, automatic deletion, periodic checks, and streaming
into DataStore. Room migrations accept both the old PixelOS version-1 table (without `type`) and
the Lineage-derived version-1 table, preserving existing update rows through schema version 4.

## OTA service

For branch `{branch}` and device `{device}`, Updater fetches:

```text
https://raw.githubusercontent.com/raebaexxx/pixelos-ota/{branch}/API/updater/{device}.json
```

`API/updater/{device}.json` uses the strict schema below. The legacy `response` wrapper and its
`id`, `stream_url`, `stream`, and `payload` fields are not accepted. `additional_images` may be
included on an update for website consumers and is ignored by the app.

The response is a non-empty JSON array. Each update has exactly one file:

```json
[
  {
    "datetime": 1781858358,
    "files": [
      {
        "filename": "PixelOS_device-17.0-20260619-0000.zip",
        "os_patch_level": "2026-06-01",
        "os_sdk_level": 36,
        "ota_property_files": "payload_metadata.bin:4662:187245,payload.bin:191907:1926080000,payload_properties.txt:1926271907:156",
        "sha256": "11468fc263696b8bc0afd35861c35d62a562ba29722447a3972c39f0023deb7f",
        "size": 1926282058,
        "url": "https://downloads.example.org/device/PixelOS_device-17.0-20260619-0000.zip"
      }
    ],
    "type": "ci",
    "version": "17.0"
  }
]
```

A release may carry a paired incremental. The full entry then also has an
`incremental` array with exactly one file, in the same shape as `files[0]`:

```json
"incremental": [
  {
    "filename": "incremental_device_20260619_0000_to_20260703_0000.zip",
    "os_patch_level": "2026-07-01",
    "os_sdk_level": 36,
    "sha256": "6d2f6b0e8a2f1d0f6b3b2f9e8d7c6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f",
    "size": 214748364,
    "url": "https://downloads.example.org/device/incremental_device_20260619_0000_to_20260703_0000.zip"
  }
]
```

The app attempts the delta first on A/B devices and automatically falls back
to the paired full entry when verification or installation fails;
update_engine itself rejects wrong-source deltas.

The runtime contract is:

- `datetime`: positive UNIX build timestamp; it must be newer than `ro.build.date.utc`.
- `files`: exactly one artifact. Do not publish mirrors or package variants as extra elements.
- `filename`: non-blank display and download filename.
- `os_patch_level`: non-blank Android security patch level from OTA metadata.
- `os_sdk_level`: positive Android SDK level. Values below the installed SDK are rejected.
- `ota_property_files`: optional exact `ota-property-files` value from
  `META-INF/com/android/metadata`. When present, every range must be numeric, positive, unique, and
  contained in the advertised package size. The three payload entries shown above are mandatory so
  streaming has all offsets it needs.
- `sha256`: lowercase, 64-character SHA-256 digest of the complete OTA artifact. It is also the
  update's stable database identity.
- `size`: positive artifact size in bytes.
- `url`: absolute HTTPS artifact URL.
- `type`: retained as `ci` for feed compatibility; it is not used to filter updates.
- `version`: non-blank PixelOS release version.
- `additional_images`: optional website metadata ignored by the updater app.

The response body is capped at 1 MiB, redirects are disabled, and a non-successful HTTP response,
invalid JSON, or any invalid entry rejects the whole response. Unknown JSON fields are ignored by
the app for forward compatibility; the publication validator below rejects them to catch mistakes,
except for `additional_images`.
When a valid feed no longer contains an online update, the stale online row and its temporary file
are removed while locally imported packages are preserved.

### Generate and validate OTA JSON

`tools/pixelos_feed.py` uses only the Python standard library. It reads authoritative values from
the OTA ZIP and computes size and SHA-256 itself:

```sh
tools/pixelos_feed.py generate-ota PixelOS_device-17.0-build.zip \
  --url https://downloads.example.org/device/PixelOS_device-17.0-build.zip \
  --version 17.0 \
  --output device.json

tools/pixelos_feed.py validate-ota device.json \
  --artifact PixelOS_device-17.0-build.zip
```

Run the validator in official-devices CI before publishing each JSON file. Artifact comparison
checks filename, timestamp, patch level, SDK level, `ota_property_files`, size, and SHA-256.

## Device changelog

The main screen loads Markdown from:

```text
https://raw.githubusercontent.com/raebaexxx/pixelos-ota/{branch}/API/updater/changelogs/{device}.md
```

The app shows explicit loading, empty, failure, and loaded states. Responses must be successful,
must not redirect, and are capped at 256 KiB. A changelog is cached in memory by branch and device;
the menu action opens the same current-device document in a browser.

## Local developer package import

`push-update.sh` registers a local full OTA on a rooted development device:

```sh
./push-update.sh PixelOS_device-17.0-build.zip [UNVERIFIED] [SERIAL]
```

The script accepts only `PixelOS_DEVICE-VERSION-*.zip`, verifies the connected
`ro.custom.device`, validates OTA metadata through `tools/pixelos_feed.py`, computes SHA-256, checks
for Room schema version 4 and duplicate IDs, fills the current database columns, and writes the
package to `/data/system_updates`. Launch Updater once first so Room creates or migrates the
database. This script is for local engineering devices; it is not a feed publication mechanism.

## Verification

Run the host-side metadata tests without an Android build:

```sh
python3 -m unittest discover -s tools/tests -v
sh -n push-update.sh
```

## Building with Android Studio

Updater uses system APIs and privileged permissions, so the public Android SDK alone is not
enough. For IDE work:

1. Place this directory in the Android source tree.
2. Generate `keystore.properties` from `keystore.properties.sample` if a local Gradle signing key
   is required.
3. Produce the platform intermediates used by `pull-system-libs.sh` in the surrounding Android
   tree.
4. Run `./pull-system-libs.sh [path/to/out]` to populate `system_libs/` with `framework.jar`,
   `SettingsLib.jar`, and `SpaLib.jar`.

The authoritative product build remains the Soong `Updater` module in `app/Android.bp`; it must be
platform-signed and installed as a privileged `system_ext` app.
