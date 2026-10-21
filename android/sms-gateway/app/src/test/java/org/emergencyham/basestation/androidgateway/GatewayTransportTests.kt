package org.emergencyham.basestation.androidgateway

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class GatewayTransportTests {
    @Test
    fun networkModePreservesConfiguredHost() {
        assertEquals("http://192.0.2.1:18098", GatewaySettingsStore.resolveBaseUrl(GatewayConnectionMode.NETWORK, " http://192.0.2.1:18098/ ", 18098))
    }

    @Test
    fun usbModeUsesPhoneLoopback() {
        assertEquals("http://127.0.0.1:18098", GatewaySettingsStore.resolveBaseUrl(GatewayConnectionMode.USB_ADB, "http://192.0.2.1", 18098))
        assertEquals("http://127.0.0.1:8099", GatewaySettingsStore.resolveBaseUrl(GatewayConnectionMode.USB_ADB, "", 8099))
    }

    @Test
    fun usbPortIsBounded() {
        assertEquals("http://127.0.0.1:1", GatewaySettingsStore.resolveBaseUrl(GatewayConnectionMode.USB_ADB, "", 0))
        assertEquals("http://127.0.0.1:65535", GatewaySettingsStore.resolveBaseUrl(GatewayConnectionMode.USB_ADB, "", 99999))
    }

    @Test
    fun requestLogsDoNotEchoUrlsOrQueries() {
        val log = GatewayLog.requestDiagnostic("GET", "/api/sms/android/outbound?token=private-value", GatewayConnectionMode.USB_ADB, 200)
        assertEquals("http method=GET route=outbound mode=USB_ADB status=200", log)
        assertFalse(log.contains("private-value"))
        assertEquals("http method=unknown route=unknown mode=NETWORK status=0", GatewayLog.requestDiagnostic("private-value", "http://private-value", GatewayConnectionMode.NETWORK, -1))
    }

    @Test
    fun deliveryLogsContainOnlyKnownMessageIdsAndPhases() {
        assertEquals("sms message_id=msg-b0ea366a5cd4 phase=submitted", GatewayLog.deliveryDiagnostic("msg-b0ea366a5cd4", "submitted"))
        assertEquals("sms message_id=redacted phase=unknown", GatewayLog.deliveryDiagnostic("+15551234567", "private message body"))
    }
}