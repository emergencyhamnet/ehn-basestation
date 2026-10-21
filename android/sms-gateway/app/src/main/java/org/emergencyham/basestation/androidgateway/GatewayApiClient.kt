package org.emergencyham.basestation.androidgateway

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

data class GatewayApiResult(
    val ok: Boolean,
    val message: String,
)

data class GatewayLinkResult(
    val ok: Boolean,
    val message: String,
    val gatewayId: String?,
    val basestationGatewayId: String?,
    val observedBaseUrl: String?,
)

data class OutboundSmsMessage(
    val messageId: String,
    val toNumber: String,
    val body: String,
)

data class OutboundPollResult(
    val ok: Boolean,
    val message: String,
    val gatewayId: String?,
    val messages: List<OutboundSmsMessage>,
)

class GatewayApiClient(private val context: Context) {
    fun postInboundSms(fromNumber: String, body: String, observedAtUtc: String): GatewayApiResult {
        return postInboundMessage(fromNumber = fromNumber, fromName = null, body = body, observedAtUtc = observedAtUtc, transport = "sms")
    }

    fun postInboundMessage(fromNumber: String?, fromName: String?, body: String, observedAtUtc: String, transport: String): GatewayApiResult {
        val payload = JSONObject()
            .put("from_number", fromNumber)
            .put("from_name", fromName)
            .put("body", body)
            .put("transport", transport)
            .put("observed_at_utc", observedAtUtc)
        val response = requestJson("POST", "/api/sms/android/inbound", payload)
        val warning = response.body.optString("warning")
        val message = when {
            response.ok && response.body.optBoolean("presence_only") -> "Presence recorded"
            response.ok -> "Inbound SMS forwarded"
            warning.isNotBlank() -> warning
            else -> "Inbound post failed with HTTP ${response.statusCode}"
        }
        return GatewayApiResult(response.ok, message)
    }

    fun claimOutbound(): OutboundPollResult {
        val limit = GatewaySettingsStore.getPollLimit(context)
        val response = requestJson("GET", "/api/sms/android/outbound?limit=$limit", null)
        if (!response.ok) {
            return OutboundPollResult(
                ok = false,
                message = response.body.optString("warning").ifBlank { "Outbound poll failed with HTTP ${response.statusCode}" },
                gatewayId = null,
                messages = emptyList(),
            )
        }

        val messages = mutableListOf<OutboundSmsMessage>()
        val rawMessages = response.body.optJSONArray("messages") ?: JSONArray()
        for (index in 0 until rawMessages.length()) {
            val item = rawMessages.optJSONObject(index) ?: continue
            val messageId = item.optString("message_id").trim()
            val toNumber = item.optString("to_number").trim()
            val body = item.optString("body")
            if (messageId.isBlank() || toNumber.isBlank() || body.isBlank()) {
                continue
            }
            messages.add(OutboundSmsMessage(messageId = messageId, toNumber = toNumber, body = body))
        }
        return OutboundPollResult(
            ok = true,
            message = "Fetched ${messages.size} outbound message(s)",
            gatewayId = response.body.optString("gateway_id").ifBlank { null },
            messages = messages,
        )
    }

    fun completeOutbound(messageId: String, ok: Boolean, providerMessageId: String?, detail: String?): GatewayApiResult {
        val payload = JSONObject()
            .put("ok", ok)
            .put("provider_message_id", providerMessageId)
            .put("detail", detail)
        val response = requestJson("POST", "/api/sms/android/outbound/$messageId/complete", payload)
        val warning = response.body.optString("warning")
        val message = when {
            response.ok -> "Completion sent"
            warning.isNotBlank() -> warning
            else -> "Completion failed with HTTP ${response.statusCode}"
        }
        return GatewayApiResult(response.ok, message)
    }

    fun probeLink(): GatewayLinkResult {
        val response = requestJson("GET", "/api/sms/android/link-status", null)
        val warning = response.body.optString("warning")
        val basestation = response.body.optJSONObject("basestation") ?: JSONObject()
        val gatewayId = response.body.optString("gateway_id").ifBlank { null }
        val basestationGatewayId = basestation.optString("gateway_id").ifBlank { null }
        val observedBaseUrl = basestation.optString("observed_base_url").ifBlank { null }
        val message = when {
            response.ok -> {
                val remoteLabel = basestationGatewayId ?: gatewayId ?: "unknown gateway"
                val observedLabel = observedBaseUrl ?: GatewaySettingsStore.getBaseUrl(context)
                "Connected to $remoteLabel at $observedLabel"
            }
            warning.isNotBlank() -> warning
            else -> "Link test failed with HTTP ${response.statusCode}"
        }
        return GatewayLinkResult(
            ok = response.ok,
            message = message,
            gatewayId = gatewayId,
            basestationGatewayId = basestationGatewayId,
            observedBaseUrl = observedBaseUrl,
        )
    }

    fun validateConfiguration(): String? {
        if (GatewaySettingsStore.getBaseUrl(context).isBlank()) {
            return "Basestation URL is required"
        }
        if (GatewaySettingsStore.getAuthToken(context).isBlank()) {
            return "Gateway auth token is required"
        }
        return null
    }

    private fun requestJson(method: String, path: String, payload: JSONObject?): HttpJsonResponse {
        val baseUrl = GatewaySettingsStore.getBaseUrl(context)
        val authToken = GatewaySettingsStore.getAuthToken(context)
        val mode = GatewaySettingsStore.getConnectionMode(context)
        if (baseUrl.isBlank() || authToken.isBlank()) {
            return HttpJsonResponse(false, 0, JSONObject().put("warning", "Missing base URL or auth token"))
        }

        val connection = (URL(baseUrl + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15000
            readTimeout = 15000
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Authorization", "Bearer $authToken")
            if (payload != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
            }
        }

        return try {
            if (payload != null) {
                OutputStreamWriter(connection.outputStream, Charsets.UTF_8).use { writer ->
                    writer.write(payload.toString())
                }
            }

            val statusCode = connection.responseCode
            GatewayLog.recordRequest(method, path, mode, statusCode)
            val stream = if (statusCode in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
            val body = if (text.isBlank()) JSONObject() else runCatching { JSONObject(text) }.getOrElse {
                JSONObject().put("warning", text)
            }
            HttpJsonResponse(statusCode in 200..299 && body.optBoolean("ok", true), statusCode, body)
        } catch (exc: Exception) {
            GatewayLog.recordRequest(method, path, mode, 0)
            HttpJsonResponse(false, 0, JSONObject().put("warning", exc.message ?: exc.javaClass.simpleName))
        } finally {
            connection.disconnect()
        }
    }

    private data class HttpJsonResponse(
        val ok: Boolean,
        val statusCode: Int,
        val body: JSONObject,
    )
}