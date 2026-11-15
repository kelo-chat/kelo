package chat.kelo.app.agent

import chat.kelo.app.core.KeloApi

// From android/kelo .../agent/LiveClient.kt (that file also holds the Android WebSocket client). Keep in step with it.

/** The tab this device's chat replies work in (its device id from GET /api/devices), or "owner". */
suspend fun ownTabId(api: KeloApi): String = api.get<chat.kelo.app.core.DeviceList>("/api/devices").current?.toString() ?: "owner"
