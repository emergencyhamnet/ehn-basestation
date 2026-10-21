package org.emergencyham.basestation.androidgateway

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

class SmsInboundReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action.orEmpty()
        if (action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION && action != Telephony.Sms.Intents.SMS_DELIVER_ACTION) {
            return
        }

        GatewayLog.attach(context)
        GatewayLog.append(context, "Inbound SMS broadcast received: $action")

        val messages = Telephony.Sms.Intents.getMessagesFromIntent(intent)
        val senderRaw = messages.firstOrNull()?.originatingAddress.orEmpty()
        val sender = GatewaySettingsStore.normalizePhoneIdentity(senderRaw)
        val body = messages.joinToString(separator = "") { it.messageBody.orEmpty() }.trim()

        if (sender.isBlank() || body.isBlank()) {
            GatewayLog.append(context, "Dropped inbound SMS with missing sender or body")
            return
        }

        GatewayLog.append(context, "Inbound SMS captured from $sender")
        val pendingResult = goAsync()
        Thread {
            try {
                val apiClient = GatewayApiClient(context)
                val configurationWarning = apiClient.validateConfiguration()
                if (configurationWarning != null) {
                    GatewayLog.append(context, configurationWarning)
                    return@Thread
                }

                val observedAtUtc = DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(OffsetDateTime.now(ZoneOffset.UTC))
                val result = apiClient.postInboundSms(
                    fromNumber = sender,
                    body = body,
                    observedAtUtc = observedAtUtc,
                )
                GatewayLog.append(context, "Inbound SMS forwarded from $sender: ${result.message}")
            } catch (exc: Exception) {
                GatewayLog.append(context, "Inbound SMS forwarding failed for $sender: ${exc.message ?: exc.javaClass.simpleName}")
            } finally {
                pendingResult.finish()
            }
        }.start()
    }
}