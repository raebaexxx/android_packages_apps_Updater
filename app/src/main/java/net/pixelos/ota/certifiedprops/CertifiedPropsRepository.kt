/*
 * SPDX-FileCopyrightText: 2026 PixelOS
 * SPDX-License-Identifier: Apache-2.0
 */

package net.pixelos.ota.certifiedprops

import android.content.Context
import android.provider.Settings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import net.pixelos.ota.R
import net.pixelos.ota.deviceinfo.DeviceInfoUtils
import net.pixelos.ota.download.APKDownloader
import org.json.JSONException
import org.json.JSONObject
import java.io.File

enum class CertifiedPropsError {
    DOWNLOAD_FAILED,
    INSTALL_FAILED,
    INVALID_DATA,
}

sealed interface CertifiedPropsState {
    val installedVersion: String

    data class Idle(override val installedVersion: String) : CertifiedPropsState
    data class Checking(override val installedVersion: String) : CertifiedPropsState
    data class UpToDate(
        override val installedVersion: String,
        val remoteVersion: String,
    ) : CertifiedPropsState
    data class UpdateAvailable(
        override val installedVersion: String,
        val remoteVersion: String,
    ) : CertifiedPropsState
    data class Installing(
        override val installedVersion: String,
        val remoteVersion: String,
    ) : CertifiedPropsState
    data class Installed(override val installedVersion: String) : CertifiedPropsState
    data class Error(
        override val installedVersion: String,
        val error: CertifiedPropsError,
    ) : CertifiedPropsState
}

class CertifiedPropsRepository(private val context: Context) {
    private val propsPath = File(context.getExternalFilesDir(null), PROPS_FILE_NAME).absolutePath

    private val _state = MutableStateFlow<CertifiedPropsState>(
        CertifiedPropsState.Idle(getInstalledProps()?.version.orEmpty())
    )
    val state = _state.asStateFlow()

    suspend fun checkForUpdate() {
        if (_state.value is CertifiedPropsState.Checking ||
            _state.value is CertifiedPropsState.Installing
        ) {
            return
        }

        val installed = getInstalledProps()
        val installedVersion = installed?.version.orEmpty()
        _state.value = CertifiedPropsState.Checking(installedVersion)

        val file = File(propsPath)
        if (file.exists()) file.delete()

        val url = context.getString(R.string.certified_prop_url)
            .replace("{branch}", DeviceInfoUtils.otaBranch)
        if (!APKDownloader.downloadApk(propsPath, url)) {
            _state.value = CertifiedPropsState.Error(
                installedVersion,
                CertifiedPropsError.DOWNLOAD_FAILED,
            )
            return
        }

        val remote = withContext(Dispatchers.IO) {
            try {
                parseProps(file.readText())
            } catch (_: Exception) {
                null
            }
        }
        if (remote == null) {
            file.delete()
            _state.value = CertifiedPropsState.Error(
                installedVersion,
                CertifiedPropsError.INVALID_DATA,
            )
            return
        }

        _state.value = if (installed != null && installed.isSameAs(remote)) {
            file.delete()
            CertifiedPropsState.UpToDate(installedVersion, remote.version)
        } else {
            CertifiedPropsState.UpdateAvailable(installedVersion, remote.version)
        }
    }

    suspend fun installUpdate() {
        val update = _state.value as? CertifiedPropsState.UpdateAvailable ?: return
        _state.value = CertifiedPropsState.Installing(
            update.installedVersion,
            update.remoteVersion,
        )

        val applied = withContext(Dispatchers.IO) {
            try {
                val file = File(propsPath)
                val props = parseProps(file.readText()) ?: return@withContext false
                val ok = Settings.Secure.putString(
                    context.contentResolver,
                    Settings.Secure.PIF_DATA,
                    props.json,
                )
                if (ok) file.delete()
                ok
            } catch (_: Exception) {
                false
            }
        }

        _state.value = if (applied) {
            CertifiedPropsState.Installed(update.remoteVersion)
        } else {
            CertifiedPropsState.Error(
                update.installedVersion,
                CertifiedPropsError.INSTALL_FAILED,
            )
        }
    }

    private fun getInstalledProps(): Props? {
        val json = try {
            Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.PIF_DATA,
            ) ?: Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.FETCHED_PIF,
            )
        } catch (_: Exception) {
            null
        } ?: return null

        return parseProps(json)
    }

    private fun parseProps(json: String): Props? {
        return try {
            val obj = JSONObject(json)
            val fingerprint = obj.optString("FINGERPRINT")
            if (fingerprint.isEmpty()) {
                null
            } else {
                val release = obj.optString("VERSION.RELEASE")
                val incremental = obj.optString("VERSION.INCREMENTAL")
                val version = when {
                    release.isNotEmpty() && incremental.isNotEmpty() -> "$release ($incremental)"
                    incremental.isNotEmpty() -> incremental
                    else -> fingerprint
                }
                Props(json, fingerprint, incremental, version)
            }
        } catch (_: JSONException) {
            null
        }
    }

    private data class Props(
        val json: String,
        val fingerprint: String,
        val incremental: String,
        val version: String,
    ) {
        fun isSameAs(other: Props): Boolean =
            fingerprint == other.fingerprint && incremental == other.incremental
    }

    private companion object {
        const val PROPS_FILE_NAME = "props.json"
    }
}
