/*
 * SPDX-FileCopyrightText: The LineageOS Project
 * SPDX-License-Identifier: Apache-2.0
 */

package net.pixelos.ota

import android.app.Application
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.LiveData
import androidx.lifecycle.asLiveData
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChangedBy
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import net.pixelos.ota.data.ChangelogState
import net.pixelos.ota.data.Update
import net.pixelos.ota.updatescheck.UpdatesCheckModel
import net.pixelos.ota.updatescheck.UpdatesCheckState

private const val TAG = "UpdatesViewModel"

class UpdatesViewModel(
    application: Application,
) : AndroidViewModel(application) {
    data class UiState(
        val updates: List<Update> = emptyList(),
        val isCheckingForUpdates: Boolean = false,
        val isOnline: Boolean = true,
        val lastCheckedTimestamp: Long = 0L,
        val hasUpdateCheckFailed: Boolean = false,
        val changelogState: ChangelogState = ChangelogState.Idle,
    ) {
        val updatesCheckModel = UpdatesCheckModel(
            state = when {
                isCheckingForUpdates -> UpdatesCheckState.Checking
                !isOnline -> UpdatesCheckState.NoInternet
                hasUpdateCheckFailed -> UpdatesCheckState.Error
                else -> UpdatesCheckState.Idle
            },
            lastCheckedTimestamp = lastCheckedTimestamp,
            canCheckForUpdates = isOnline && !isCheckingForUpdates,
        )
    }

    private val _uiState = MutableStateFlow(UiState())
    val uiState: StateFlow<UiState> = _uiState.asStateFlow()
    val uiStateLive: LiveData<UiState> = _uiState.asLiveData()

    private val updaterApplication = getApplication<UpdaterApplication>()
    private val repository = updaterApplication.updatesRepository
    private val appStateRepository = updaterApplication.appStateRepository
    private val changelogRepository = updaterApplication.changelogRepository
    private val networkMonitor = updaterApplication.networkMonitor
    private var changelogJob: Job? = null

    init {
        viewModelScope.launch {
            appStateRepository.lastCheckedTimestampFlow.collect { ts ->
                _uiState.update { it.copy(lastCheckedTimestamp = ts) }
            }
        }

        viewModelScope.launch {
            repository.observeLocalUpdates().collect { updates ->
                _uiState.update { it.copy(updates = updates) }
                // Load the changelog as soon as any update is known, not only
                // when one is flagged available online. isAvailableOnline lives
                // in memory only and is set by UpdaterController after a
                // successful feed fetch, so a build that already has its entry
                // in the database would never show the changelog.
                if (updates.isNotEmpty()) {
                    loadChangelog()
                }
            }
        }

        viewModelScope.launch {
            networkMonitor.networkState
                .distinctUntilChangedBy { it.isOnline }
                .collect { networkState ->
                    _uiState.update { it.copy(isOnline = networkState.isOnline) }
                }
        }
    }

    fun fetchUpdates() {
        if (_uiState.value.isCheckingForUpdates) return

        viewModelScope.launch {
            _uiState.update {
                it.copy(
                    isCheckingForUpdates = true,
                    hasUpdateCheckFailed = false,
                )
            }
            try {
                val fetchedAt = repository.fetchUpdates()
                fetchedAt?.let { fetchedAt ->
                    appStateRepository.setLastCheckedTimestamp(fetchedAt)
                }
                _uiState.update { it.copy(isCheckingForUpdates = false) }
                // Same reasoning as in the observeLocalUpdates() collector:
                // do not gate the changelog on the in-memory online flag.
                loadChangelog()
            } catch (e: Exception) {
                Log.e(TAG, "Failed to fetch updates", e)
                _uiState.update {
                    it.copy(
                        isCheckingForUpdates = false,
                        hasUpdateCheckFailed = true,
                    )
                }
            }
        }
    }

    private fun loadChangelog() {
        if (changelogJob?.isActive == true ||
            _uiState.value.changelogState is ChangelogState.Loaded
        ) {
            return
        }
        changelogJob = viewModelScope.launch {
            _uiState.update { it.copy(changelogState = ChangelogState.Loading) }
            val state = try {
                ChangelogState.Loaded(changelogRepository.fetchChangelog())
            } catch (e: Exception) {
                Log.e(TAG, "Failed to fetch changelog", e)
                ChangelogState.Error
            }
            _uiState.update { it.copy(changelogState = state) }
        }
    }
}
