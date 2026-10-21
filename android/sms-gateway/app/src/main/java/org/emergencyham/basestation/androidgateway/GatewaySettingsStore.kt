package org.emergencyham.basestation.androidgateway

import android.content.Context

enum class GatewayConnectionMode {
    NETWORK,
    USB_ADB,
}

object GatewaySettingsStore {
    private const val prefsName = "basestation_android_gateway"
    private const val keyBaseUrl = "base_url"
    private const val keyAuthToken = "auth_token"
    private const val keyPollIntervalSeconds = "poll_interval_seconds"
    private const val keyPollLimit = "poll_limit"
    private const val keyLocalSmsNumber = "local_sms_number"
    private const val keyConnectionMode = "connection_mode"
    private const val keyUsbPort = "usb_port"

    fun getBaseUrl(context: Context): String {
        return resolveBaseUrl(getConnectionMode(context), getNetworkBaseUrl(context), getUsbPort(context))
    }

    fun resolveBaseUrl(mode: GatewayConnectionMode, networkBaseUrl: String, usbPort: Int): String {
        return when (mode) {
            GatewayConnectionMode.NETWORK -> networkBaseUrl.trim().trimEnd('/')
            GatewayConnectionMode.USB_ADB -> "http://127.0.0.1:${usbPort.coerceIn(1, 65535)}"
        }
    }

    fun getNetworkBaseUrl(context: Context): String {
        return prefs(context).getString(keyBaseUrl, "")
            .orEmpty()
            .trim()
            .trimEnd('/')
    }

    fun setBaseUrl(context: Context, value: String) {
        prefs(context).edit().putString(keyBaseUrl, value.trim().trimEnd('/')).apply()
    }

    fun getConnectionMode(context: Context): GatewayConnectionMode {
        return runCatching {
            GatewayConnectionMode.valueOf(prefs(context).getString(keyConnectionMode, GatewayConnectionMode.NETWORK.name).orEmpty())
        }.getOrDefault(GatewayConnectionMode.NETWORK)
    }

    fun setConnectionMode(context: Context, mode: GatewayConnectionMode) {
        prefs(context).edit().putString(keyConnectionMode, mode.name).apply()
    }

    fun getUsbPort(context: Context): Int = prefs(context).getInt(keyUsbPort, 18098).coerceIn(1, 65535)

    fun setUsbPort(context: Context, port: Int) {
        prefs(context).edit().putInt(keyUsbPort, port.coerceIn(1, 65535)).apply()
    }

    fun getAuthToken(context: Context): String {
        return prefs(context).getString(keyAuthToken, "").orEmpty().trim()
    }

    fun setAuthToken(context: Context, value: String) {
        prefs(context).edit().putString(keyAuthToken, value.trim()).apply()
    }

    fun getPollIntervalSeconds(context: Context): Int {
        return prefs(context).getInt(keyPollIntervalSeconds, 15).coerceIn(5, 300)
    }

    fun setPollIntervalSeconds(context: Context, value: Int) {
        prefs(context).edit().putInt(keyPollIntervalSeconds, value.coerceIn(5, 300)).apply()
    }

    fun getPollLimit(context: Context): Int {
        return prefs(context).getInt(keyPollLimit, 10).coerceIn(1, 50)
    }

    fun setPollLimit(context: Context, value: Int) {
        prefs(context).edit().putInt(keyPollLimit, value.coerceIn(1, 50)).apply()
    }

    fun getLocalSmsNumber(context: Context): String {
        return prefs(context).getString(keyLocalSmsNumber, "")
            .orEmpty()
            .trim()
    }

    fun setLocalSmsNumber(context: Context, value: String) {
        prefs(context).edit().putString(keyLocalSmsNumber, normalizePhoneIdentity(value)).apply()
    }

    fun normalizePhoneIdentity(value: String): String {
        val trimmed = value.trim()
        if (trimmed.isEmpty()) {
            return ""
        }

        val builder = StringBuilder()
        trimmed.forEachIndexed { index, char ->
            when {
                char.isDigit() -> builder.append(char)
                char == '+' && index == 0 -> builder.append(char)
            }
        }
        return builder.toString()
    }

    private fun prefs(context: Context) = context.getSharedPreferences(prefsName, Context.MODE_PRIVATE)
}