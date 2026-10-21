package org.emergencyham.basestation.androidgateway

import android.content.Context
import android.util.Log
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object GatewayLog {
    private val lines = ArrayDeque<String>()
    private var version = 0
    private const val maxLines = 200
    private const val prefsName = "gateway_log"
    private const val keyLines = "lines"
    private var loaded = false
    private var attachedContext: Context? = null
    const val remoteTag = "EHNRelayLink"

    fun requestDiagnostic(method: String, path: String, mode: GatewayConnectionMode, statusCode: Int): String {
        val route = when {
            path.substringBefore('?') == "/api/sms/android/inbound" -> "inbound"
            path.substringBefore('?') == "/api/sms/android/outbound" -> "outbound"
            path.substringBefore('?') == "/api/sms/android/link-status" -> "link_status"
            path.startsWith("/api/sms/android/outbound/") && path.endsWith("/complete") -> "completion"
            else -> "unknown"
        }
        val safeMethod = method.takeIf { it in setOf("GET", "POST") } ?: "unknown"
        return "http method=$safeMethod route=$route mode=${mode.name} status=${statusCode.coerceIn(0, 599)}"
    }

    fun recordRequest(method: String, path: String, mode: GatewayConnectionMode, statusCode: Int) {
        Log.i(remoteTag, requestDiagnostic(method, path, mode, statusCode))
    }

    fun deliveryDiagnostic(messageId: String, phase: String): String {
        val safeId = messageId.takeIf { it.matches(Regex("msg-[0-9a-f]{12}")) } ?: "redacted"
        val safePhase = phase.takeIf { it in setOf("claimed", "submitted", "completion_reported", "failed", "permission_denied") } ?: "unknown"
        return "sms message_id=$safeId phase=$safePhase"
    }

    fun recordDelivery(messageId: String, phase: String) {
        Log.i(remoteTag, deliveryDiagnostic(messageId, phase))
    }

    @Synchronized
    fun attach(context: Context) {
        attachedContext = context.applicationContext
        if (loaded) {
            return
        }
        val prefs = attachedContext!!.getSharedPreferences(prefsName, Context.MODE_PRIVATE)
        val persisted = prefs.getString(keyLines, "").orEmpty()
        if (persisted.isNotBlank()) {
            for (line in persisted.split('\n')) {
                if (line.isNotBlank()) {
                    lines.add(line)
                }
            }
        }
        loaded = true
    }

    @Synchronized
    fun append(message: String) {
        val stamp = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
        lines.addFirst("[$stamp] $message")
        while (lines.size > maxLines) {
            lines.removeLast()
        }
        version += 1
        attachedContext?.let { persist(it) }
    }

    @Synchronized
    fun append(context: Context, message: String) {
        attach(context)
        append(message)
    }

    @Synchronized
    fun snapshot(): Pair<Int, List<String>> = version to lines.toList()

    @Synchronized
    private fun persist(context: Context) {
        val prefs = context.applicationContext.getSharedPreferences(prefsName, Context.MODE_PRIVATE)
        prefs.edit().putString(keyLines, lines.joinToString("\n")).apply()
    }
}