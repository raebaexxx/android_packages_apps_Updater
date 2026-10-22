/*
 * SPDX-FileCopyrightText: The LineageOS Project
 * SPDX-License-Identifier: Apache-2.0
 */

package net.pixelos.ota.preferences

import androidx.compose.runtime.Composable
import androidx.compose.runtime.IntState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.android.settingslib.spa.widget.preference.ListPreference
import com.android.settingslib.spa.widget.preference.ListPreferenceModel
import com.android.settingslib.spa.widget.preference.ListPreferenceOption
import com.android.settingslib.spa.widget.preference.Preference
import com.android.settingslib.spa.widget.preference.PreferenceModel
import com.android.settingslib.spa.widget.preference.SwitchPreference
import com.android.settingslib.spa.widget.preference.SwitchPreferenceModel
import com.android.settingslib.spa.widget.scaffold.RegularScaffold
import com.android.settingslib.spa.widget.ui.Category
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import net.pixelos.ota.R
import net.pixelos.ota.UpdaterApplication
import net.pixelos.ota.certifiedprops.CertifiedPropsError
import net.pixelos.ota.certifiedprops.CertifiedPropsRepository
import net.pixelos.ota.certifiedprops.CertifiedPropsState
import net.pixelos.ota.data.CheckInterval
import net.pixelos.ota.data.UserPreferencesRepository
import net.pixelos.ota.deviceinfo.DeviceInfoUtils
import net.pixelos.ota.util.BatteryMonitor
import java.io.File

@Composable
fun PreferencesScreen() {
    val context = LocalContext.current
    val application = remember(context) { context.applicationContext as UpdaterApplication }
    val repository = application.userPreferencesRepository
    val batteryMonitor = application.batteryMonitor
    val isABDevice = remember { DeviceInfoUtils.isABDevice }
    val supportsAbPerformanceMode = remember {
        isABDevice && context.resources.getBoolean(R.bool.config_ab_perf_mode)
    }
    val showRecoveryUpdate = remember {
        !context.resources.getBoolean(R.bool.config_hideRecoveryUpdate) &&
                installRecoveryScriptExists()
    }
    RegularScaffold(title = stringResource(R.string.display_name)) {
        PreferencesContent(
            repository,
            batteryMonitor,
            application.certifiedPropsRepository,
            isABDevice,
            supportsAbPerformanceMode,
            showRecoveryUpdate,
        )
    }
}

@Composable
private fun PreferencesContent(
    repository: UserPreferencesRepository,
    batteryMonitor: BatteryMonitor,
    certifiedPropsRepository: CertifiedPropsRepository,
    isABDevice: Boolean,
    supportsAbPerformanceMode: Boolean,
    showRecoveryUpdate: Boolean,
) {
    val coroutineScope = rememberCoroutineScope()
    val abPerfMode by repository.abPerfModeFlow.collectAsStateWithLifecycle(false)
    val batteryState by batteryMonitor.batteryState.collectAsStateWithLifecycle(
        batteryMonitor.currentBatteryState
    )
    val autoDelete by repository.autoDeleteFlow.collectAsStateWithLifecycle(true)
    val streamUpdates by repository.streamUpdatesFlow.collectAsStateWithLifecycle(true)
    val checkInterval by repository.checkIntervalFlow.collectAsStateWithLifecycle(CheckInterval.default)
    val meteredNetworkWarning by repository.meteredNetworkWarningFlow.collectAsStateWithLifecycle(
        true
    )
    val periodicCheckEnabled by repository.periodicCheckEnabledFlow.collectAsStateWithLifecycle(true)
    var recoveryUpdateEnabled by remember { mutableStateOf(repository.getRecoveryUpdateEnabled()) }

    val autoUpdatesCheckSummary = stringResource(R.string.menu_auto_updates_check_summary)
    val autoDeleteUpdatesSummary = stringResource(R.string.menu_auto_delete_updates_summary)
    val streamUpdatesSummary = stringResource(R.string.menu_stream_updates_summary)
    val meteredNetworkWarningSummary = stringResource(R.string.menu_metered_network_warning_summary)
    val abPerfModeSummary = stringResource(R.string.menu_ab_perf_mode_summary)
    val abPerfModeChargingSummary = stringResource(R.string.menu_ab_perf_mode_summary_charging)
    val updateRecoverySummary = stringResource(R.string.menu_update_recovery_summary)
    val selectedCheckInterval = remember(checkInterval) {
        object : IntState {
            override val intValue = checkInterval.ordinal
        }
    }

    Category(title = stringResource(R.string.pref_category_background_sync)) {
        SwitchPreference(object : SwitchPreferenceModel {
            override val title = stringResource(R.string.menu_auto_updates_check)
            override val summary = { autoUpdatesCheckSummary }
            override val checked = { periodicCheckEnabled }
            override val onCheckedChange: (Boolean) -> Unit = { value ->
                coroutineScope.launch { repository.setPeriodicCheckEnabled(value) }
            }
        })

        ListPreference(object : ListPreferenceModel {
            override val title = stringResource(R.string.menu_auto_updates_check_interval)
            override val enabled = { periodicCheckEnabled }
            override val options = listOf(
                ListPreferenceOption(
                    id = CheckInterval.DAILY.ordinal,
                    text = stringResource(R.string.time_unit_day),
                ),
                ListPreferenceOption(
                    id = CheckInterval.WEEKLY.ordinal,
                    text = stringResource(R.string.time_unit_week),
                ),
                ListPreferenceOption(
                    id = CheckInterval.BIWEEKLY.ordinal,
                    text = stringResource(R.string.time_unit_two_weeks),
                ),
                ListPreferenceOption(
                    id = CheckInterval.MONTHLY.ordinal,
                    text = stringResource(R.string.time_unit_month),
                ),
            )
            override val selectedId = selectedCheckInterval
            override val onIdSelected: (Int) -> Unit = { id ->
                val interval = CheckInterval.entries.getOrElse(id) { CheckInterval.default }
                coroutineScope.launch { repository.setCheckInterval(interval) }
            }
        })
    }

    Category(title = stringResource(R.string.pref_category_download_install)) {
        if (isABDevice) {
            SwitchPreference(object : SwitchPreferenceModel {
                override val title = stringResource(R.string.menu_stream_updates)
                override val summary = { streamUpdatesSummary }
                override val checked = { streamUpdates }
                override val onCheckedChange: (Boolean) -> Unit = { value ->
                    coroutineScope.launch { repository.setStreamUpdates(value) }
                }
            })
        } else {
            SwitchPreference(object : SwitchPreferenceModel {
                override val title = stringResource(R.string.menu_auto_delete_updates)
                override val summary = { autoDeleteUpdatesSummary }
                override val checked = { autoDelete }
                override val onCheckedChange: (Boolean) -> Unit = { value ->
                    coroutineScope.launch { repository.setAutoDelete(value) }
                }
            })
        }

        SwitchPreference(object : SwitchPreferenceModel {
            override val title = stringResource(R.string.menu_metered_network_warning)
            override val summary = { meteredNetworkWarningSummary }
            override val checked = { meteredNetworkWarning }
            override val onCheckedChange: (Boolean) -> Unit = { value ->
                coroutineScope.launch { repository.setMeteredNetworkWarning(value) }
            }
        })

        if (supportsAbPerformanceMode) {
            SwitchPreference(object : SwitchPreferenceModel {
                override val title = stringResource(R.string.menu_ab_perf_mode)
                override val summary = {
                    if (batteryState.isAcCharging) {
                        abPerfModeChargingSummary
                    } else {
                        abPerfModeSummary
                    }
                }
                override val changeable = { !batteryState.isAcCharging }
                override val checked = { batteryState.isAcCharging || abPerfMode }
                override val onCheckedChange: (Boolean) -> Unit = { value ->
                    coroutineScope.launch { repository.setAbPerfMode(value) }
                }
            })
        }

        if (showRecoveryUpdate) {
            SwitchPreference(object : SwitchPreferenceModel {
                override val title = stringResource(R.string.menu_update_recovery)
                override val summary = { updateRecoverySummary }
                override val checked = { recoveryUpdateEnabled }
                override val onCheckedChange: (Boolean) -> Unit = { value ->
                    recoveryUpdateEnabled = value
                    repository.setRecoveryUpdateEnabled(value)
                }
            })
        }
    }

    CertifiedPropsPreferences(certifiedPropsRepository, coroutineScope)
}

@Composable
private fun CertifiedPropsPreferences(
    repository: CertifiedPropsRepository,
    coroutineScope: CoroutineScope,
) {
    val state by repository.state.collectAsStateWithLifecycle()
    val unknown = stringResource(R.string.text_download_size_unknown)
    val installedVersion = state.installedVersion.ifEmpty { unknown }
    val remoteVersion = when (val currentState = state) {
        is CertifiedPropsState.UpToDate -> currentState.remoteVersion
        is CertifiedPropsState.UpdateAvailable -> currentState.remoteVersion
        is CertifiedPropsState.Installing -> currentState.remoteVersion
        else -> ""
    }.ifEmpty { unknown }
    val versionSummary = stringResource(
        R.string.certified_prop_info,
        installedVersion,
    ) + stringResource(
        R.string.certified_prop_remote,
        remoteVersion,
    )
    val summary = when (val currentState = state) {
        is CertifiedPropsState.Checking -> stringResource(R.string.certified_prop_checking)
        is CertifiedPropsState.Installing -> stringResource(R.string.certified_prop_downloading)
        is CertifiedPropsState.Installed -> stringResource(R.string.certified_prop_install_success)
        is CertifiedPropsState.Error -> when (currentState.error) {
            CertifiedPropsError.DOWNLOAD_FAILED ->
                stringResource(R.string.certified_prop_download_failed)
            CertifiedPropsError.INSTALL_FAILED ->
                stringResource(R.string.certified_prop_install_failed)
            CertifiedPropsError.INVALID_DATA ->
                stringResource(R.string.certified_prop_invalid_data)
        }
        else -> versionSummary
    }
    val actionTitle = if (state is CertifiedPropsState.UpdateAvailable) {
        stringResource(R.string.certified_prop_available)
    } else {
        stringResource(R.string.certified_prop_check)
    }
    val busy = state is CertifiedPropsState.Checking || state is CertifiedPropsState.Installing

    Category(title = stringResource(R.string.play_integrity_header)) {
        Preference(object : PreferenceModel {
            override val title = stringResource(R.string.certified_prop_status)
            override val summary = { summary }
        })
        Preference(object : PreferenceModel {
            override val title = actionTitle
            override val enabled = { !busy }
            override val onClick: () -> Unit = {
                coroutineScope.launch {
                    if (state is CertifiedPropsState.UpdateAvailable) {
                        repository.installUpdate()
                    } else {
                        repository.checkForUpdate()
                    }
                }
            }
        })
    }
}

private fun installRecoveryScriptExists() = File("/vendor/bin/install-recovery.sh").exists()
