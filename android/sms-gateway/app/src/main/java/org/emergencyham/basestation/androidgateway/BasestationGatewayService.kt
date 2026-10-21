package org.emergencyham.basestation.androidgateway

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.telephony.SmsManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

class BasestationGatewayService : Service() {
    private lateinit var workerThread: HandlerThread
    private lateinit var workerHandler: Handler
    private lateinit var apiClient: GatewayApiClient

    private val pollRunnable = object : Runnable {
        override fun run() {
            pollOutboundQueue()
            scheduleNextPoll()
        }
    }

    override fun onCreate() {
        super.onCreate()
        GatewayLog.attach(this)
        workerThread = HandlerThread("BasestationGatewayWorker").apply { start() }
        workerHandler = Handler(workerThread.looper)
        apiClient = GatewayApiClient(applicationContext)
        createNotificationChannel()
        GatewayLog.append(this, "Gateway service created")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(notificationId, buildNotification("Basestation SMS gateway active"))

        when (intent?.action) {
            actionStop -> {
                stopSelf()
                return START_NOT_STICKY
            }

            actionInboundSms -> {
                val sender = intent.getStringExtra(extraSender).orEmpty()
                val senderName = intent.getStringExtra(extraSenderName).orEmpty()
                val body = intent.getStringExtra(extraBody).orEmpty()
                val transport = intent.getStringExtra(extraTransport).orEmpty().ifBlank { "sms" }
                workerHandler.post {
                    handleInboundMessage(sender, senderName, body, transport)
                }
            }

            actionPollNow -> {
                workerHandler.post {
                    pollOutboundQueue()
                    scheduleNextPoll()
                }
            }
        }

        workerHandler.post {
            scheduleNextPoll()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        workerHandler.removeCallbacksAndMessages(null)
        workerThread.quitSafely()
        GatewayLog.append(this, "Gateway service stopped")
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun handleInboundMessage(sender: String, senderName: String, body: String, transport: String) {
        val configurationWarning = apiClient.validateConfiguration()
        if (configurationWarning != null) {
            GatewayLog.append(this, configurationWarning)
            return
        }

        val observedAtUtc = DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(OffsetDateTime.now(ZoneOffset.UTC))
        val result = apiClient.postInboundMessage(
            fromNumber = sender.ifBlank { null },
            fromName = senderName.ifBlank { null },
            body = body,
            observedAtUtc = observedAtUtc,
            transport = transport,
        )
        val senderLabel = sender.ifBlank { senderName }.ifBlank { "unknown" }
        GatewayLog.append(this, "Inbound ${transport.lowercase()} $senderLabel: ${result.message}")
    }

    private fun pollOutboundQueue() {
        val configurationWarning = apiClient.validateConfiguration()
        if (configurationWarning != null) {
            GatewayLog.append(configurationWarning)
            return
        }

        val pollResult = apiClient.claimOutbound()
        if (!pollResult.ok) {
            GatewayLog.append("Outbound poll failed: ${pollResult.message}")
            return
        }

        if (pollResult.messages.isEmpty()) {
            GatewayLog.append("No outbound SMS queued")
            return
        }

        for (message in pollResult.messages) {
            GatewayLog.recordDelivery(message.messageId, "claimed")
            sendOutboundSms(message)
        }
    }

    private fun sendOutboundSms(message: OutboundSmsMessage) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
            GatewayLog.recordDelivery(message.messageId, "permission_denied")
            GatewayLog.append("SEND_SMS permission is not granted")
            apiClient.completeOutbound(message.messageId, ok = false, providerMessageId = null, detail = "SEND_SMS permission is not granted")
            return
        }

        val normalizedTarget = GatewaySettingsStore.normalizePhoneIdentity(message.toNumber)
        if (normalizedTarget.isBlank()) {
            GatewayLog.append("Invalid outbound target for ${message.messageId}")
            apiClient.completeOutbound(message.messageId, ok = false, providerMessageId = null, detail = "Invalid destination number")
            return
        }

        try {
            val smsManager = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                getSystemService(SmsManager::class.java)
            } else {
                @Suppress("DEPRECATION")
                SmsManager.getDefault()
            }
            smsManager.sendTextMessage(normalizedTarget, null, message.body, null, null)
            GatewayLog.recordDelivery(message.messageId, "submitted")
            val providerMessageId = "android-${System.currentTimeMillis()}"
            val completion = apiClient.completeOutbound(
                messageId = message.messageId,
                ok = true,
                providerMessageId = providerMessageId,
                detail = "Sent through Android SmsManager",
            )
            GatewayLog.append("Outbound ${message.messageId} -> $normalizedTarget: ${completion.message}")
            if (completion.ok) GatewayLog.recordDelivery(message.messageId, "completion_reported")
        } catch (exc: Exception) {
            GatewayLog.recordDelivery(message.messageId, "failed")
            val completion = apiClient.completeOutbound(
                messageId = message.messageId,
                ok = false,
                providerMessageId = null,
                detail = exc.message ?: exc.javaClass.simpleName,
            )
            GatewayLog.append("Outbound ${message.messageId} failed: ${completion.message}")
        }
    }

    private fun scheduleNextPoll() {
        workerHandler.removeCallbacks(pollRunnable)
        val delayMs = GatewaySettingsStore.getPollIntervalSeconds(this).toLong() * 1000L
        workerHandler.postDelayed(pollRunnable, delayMs)
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            val channel = NotificationChannel(channelId, "Basestation Android Gateway", NotificationManager.IMPORTANCE_LOW)
            manager.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(contentText: String): Notification {
        return NotificationCompat.Builder(this, channelId)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(contentText)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val channelId = "basestation_android_gateway"
        private const val notificationId = 4101
        private const val actionInboundSms = "org.emergencyham.basestation.androidgateway.action.INBOUND_SMS"
        private const val actionPollNow = "org.emergencyham.basestation.androidgateway.action.POLL_NOW"
        private const val actionStop = "org.emergencyham.basestation.androidgateway.action.STOP"
        private const val extraSender = "sender"
        private const val extraSenderName = "sender_name"
        private const val extraBody = "body"
        private const val extraTransport = "transport"

        fun start(context: Context) {
            val intent = Intent(context, BasestationGatewayService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, BasestationGatewayService::class.java))
        }

        fun requestPollNow(context: Context) {
            val intent = Intent(context, BasestationGatewayService::class.java).apply {
                action = actionPollNow
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun enqueueInboundSms(context: Context, sender: String, body: String) {
            enqueueInboundMessage(context, sender = sender, senderName = null, body = body, transport = "sms")
        }

        fun enqueueInboundMessage(context: Context, sender: String?, senderName: String?, body: String, transport: String) {
            val intent = Intent(context, BasestationGatewayService::class.java).apply {
                action = actionInboundSms
                putExtra(extraSender, sender.orEmpty())
                putExtra(extraSenderName, senderName.orEmpty())
                putExtra(extraBody, body)
                putExtra(extraTransport, transport)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}