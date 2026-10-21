package org.emergencyham.basestation.androidgateway

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

class MessagingNotificationListenerService : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName != GOOGLE_MESSAGES_PACKAGE) {
            return
        }
        if ((sbn.notification.flags and Notification.FLAG_GROUP_SUMMARY) != 0) {
            return
        }

        val senderName = extractSenderName(sbn.notification)
        val body = extractMessageBody(sbn.notification)
        if (senderName.isBlank() || body.isBlank()) {
            return
        }

        val signature = "${sbn.packageName}|$senderName|$body|${sbn.postTime}"
        if (signature == lastSignature) {
            return
        }
        lastSignature = signature

        GatewayLog.attach(this)
        GatewayLog.append(this, "Inbound notification captured from $senderName")
        BasestationGatewayService.enqueueInboundMessage(
            context = this,
            sender = null,
            senderName = senderName,
            body = body,
            transport = "rcs",
        )
    }

    private fun extractSenderName(notification: Notification): String {
        val extras = notification.extras
        val conversationTitle = extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString()?.trim().orEmpty()
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim().orEmpty()
        return conversationTitle.ifBlank { title }
    }

    private fun extractMessageBody(notification: Notification): String {
        val extras = notification.extras
        return extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()?.trim().orEmpty()
    }

    companion object {
        private const val GOOGLE_MESSAGES_PACKAGE = "com.google.android.apps.messaging"
        private var lastSignature: String? = null
    }
}