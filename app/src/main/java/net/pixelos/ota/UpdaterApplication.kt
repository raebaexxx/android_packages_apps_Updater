/*
 * SPDX-FileCopyrightText: The LineageOS Project
 * SPDX-License-Identifier: Apache-2.0
 */

package net.pixelos.ota

import android.app.Application
import com.android.settingslib.spa.framework.common.SettingsPageProviderRepository
import com.android.settingslib.spa.framework.common.SpaEnvironment
import com.android.settingslib.spa.framework.common.SpaEnvironmentFactory
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.launch
import net.pixelos.ota.data.AppStateRepository
import net.pixelos.ota.data.ChangelogRepository
import net.pixelos.ota.data.UpdatesRepository
import net.pixelos.ota.data.UserPreferencesRepository
import net.pixelos.ota.data.source.local.UpdatesDatabase
import net.pixelos.ota.data.source.local.UpdatesLocalDataSource
import net.pixelos.ota.data.source.network.UpdatesNetworkDataSource
import net.pixelos.ota.deviceinfo.DeviceInfoUtils
import net.pixelos.ota.notifications.NotificationHelper
import net.pixelos.ota.util.BatteryMonitor
import net.pixelos.ota.util.NetworkMonitor

class UpdaterApplication : Application() {
    private val coroutineScope = MainScope()
    private val database by lazy { UpdatesDatabase.getInstance(applicationContext) }
    private val networkDataSource by lazy { UpdatesNetworkDataSource(applicationContext) }
    private val localDataSource by lazy { UpdatesLocalDataSource(database.updateDao()) }


    val batteryMonitor by lazy {
        BatteryMonitor(applicationContext, coroutineScope, userPreferencesRepository)
    }
    val networkMonitor by lazy { NetworkMonitor(applicationContext, coroutineScope) }
    val notificationHelper by lazy { NotificationHelper(applicationContext) }
    val appStateRepository by lazy { AppStateRepository(applicationContext) }
    val changelogRepository by lazy { ChangelogRepository(applicationContext) }
    val userPreferencesRepository by lazy { UserPreferencesRepository(applicationContext) }
    val updatesRepository by lazy {
        UpdatesRepository(
            context = applicationContext,
            networkMonitor = networkMonitor,
            notificationHelper = notificationHelper,
            networkDataSource = networkDataSource,
            localDataSource = localDataSource,
            userPreferencesRepository = userPreferencesRepository,
        )
    }

    override fun onCreate() {
        super.onCreate()
        DeviceInfoUtils.initialize(applicationContext)
        notificationHelper.setUpNotificationChannels()
        coroutineScope.launch {
            userPreferencesRepository.migrateLegacyPreferences()
        }
        coroutineScope.launch {
            updatesRepository.pruneInstalledUpdates()
        }
        SpaEnvironmentFactory.reset(object : SpaEnvironment(applicationContext) {
            override val pageProviderRepository = lazy {
                SettingsPageProviderRepository(emptyList())
            }

            override val isSpaExpressiveEnabled = true
        })
    }
}
