/*
 * SPDX-FileCopyrightText: 2026 PixelOS
 * SPDX-License-Identifier: Apache-2.0
 */

package net.pixelos.ota.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import net.pixelos.ota.R
import net.pixelos.ota.deviceinfo.DeviceInfoUtils
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.util.concurrent.TimeUnit

sealed interface ChangelogState {
    data object Idle : ChangelogState
    data object Loading : ChangelogState
    data class Loaded(val markdown: String) : ChangelogState
    data object Error : ChangelogState
}

class ChangelogRepository(private val context: Context) {
    private val client = OkHttpClient.Builder()
        .callTimeout(10, TimeUnit.SECONDS)
        .followRedirects(false)
        .build()

    @Volatile
    private var cachedKey: String? = null

    @Volatile
    private var cachedMarkdown: String? = null

    suspend fun fetchChangelog(): String = withContext(Dispatchers.IO) {
        val device = DeviceInfoUtils.device
        val branch = DeviceInfoUtils.otaBranch
        require(device.isNotBlank()) { "Missing ro.custom.device" }
        require(branch.isNotBlank()) { "Missing net.pixelos.version" }

        val key = "$branch/$device"
        if (cachedKey == key) {
            cachedMarkdown?.let { return@withContext it }
        }

        val url = context.getString(R.string.changelog_url)
            .replace("{branch}", branch)
            .replace("{device}", device)
        require(url.startsWith("https://")) { "Changelog URL must use HTTPS" }

        val markdown = client.newCall(Request.Builder().url(url).build()).execute().use { response ->
            if (!response.isSuccessful) {
                throw IOException("Unexpected changelog HTTP status: ${response.code}")
            }
            val body = response.body ?: throw IOException("Empty changelog response")
            val contentLength = body.contentLength()
            if (contentLength > MAX_RESPONSE_BYTES) {
                throw IOException("Changelog is too large: $contentLength bytes")
            }
            // readByteArray(count) requires exactly count bytes and raises
            // EOFException on a shorter body, which is every changelog below
            // the 256 KiB limit. Probe with request() and then read what is
            // actually there, the same way UpdatesNetworkDataSource does.
            val source = body.source()
            val hasMore = source.request(MAX_RESPONSE_BYTES + 1)
            if (hasMore) {
                throw IOException("Changelog exceeds $MAX_RESPONSE_BYTES bytes")
            }
            source.buffer.readByteArray().decodeToString().trim()
        }

        cachedKey = key
        cachedMarkdown = markdown
        markdown
    }

    private companion object {
        const val MAX_RESPONSE_BYTES = 256L * 1024L
    }
}
