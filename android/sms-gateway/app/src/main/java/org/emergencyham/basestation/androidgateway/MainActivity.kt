package org.emergencyham.basestation.androidgateway

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.widget.ArrayAdapter
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton

class MainActivity : AppCompatActivity() {
    private lateinit var statusText: TextView
    private val refreshHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private var lastVersion = -1

    private val refresher = object : Runnable {
        override fun run() {
            refreshLog()
            refreshHandler.postDelayed(this, 1500L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        setSupportActionBar(findViewById<MaterialToolbar>(R.id.toolbar))

        GatewayLog.attach(this)
        statusText = findViewById(R.id.statusText)
        requestRuntimePermissions()
        GatewayLog.append("App opened")
        showCurrentConfigSummary()

        findViewById<MaterialButton>(R.id.startGatewayButton).setOnClickListener {
            BasestationGatewayService.start(this)
            GatewayLog.append("Gateway service start requested")
        }

        findViewById<MaterialButton>(R.id.stopGatewayButton).setOnClickListener {
            BasestationGatewayService.stop(this)
            GatewayLog.append("Gateway service stop requested")
        }

        findViewById<MaterialButton>(R.id.pollNowButton).setOnClickListener {
            BasestationGatewayService.requestPollNow(this)
            GatewayLog.append("Manual outbound poll requested")
        }

        findViewById<MaterialButton>(R.id.testLinkButton).setOnClickListener {
            GatewayLog.append("Testing basestation link")
            Thread {
                val result = GatewayApiClient(this).probeLink()
                GatewayLog.append(result.message)
            }.start()
        }

        findViewById<MaterialButton>(R.id.settingsButton).setOnClickListener {
            showSettingsDialog()
        }
    }

    override fun onResume() {
        super.onResume()
        refreshHandler.post(refresher)
    }

    override fun onPause() {
        refreshHandler.removeCallbacks(refresher)
        super.onPause()
    }

    private fun requestRuntimePermissions() {
        val needed = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.RECEIVE_SMS)
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.SEND_SMS)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (needed.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, needed.toTypedArray(), 1001)
        }
    }

    private fun showCurrentConfigSummary() {
        val baseUrl = GatewaySettingsStore.getBaseUrl(this)
        val tokenState = if (GatewaySettingsStore.getAuthToken(this).isBlank()) "missing" else "set"
        val localSmsNumber = GatewaySettingsStore.getLocalSmsNumber(this).ifBlank { "not set" }
        val receiveSmsPermission = if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECEIVE_SMS) == PackageManager.PERMISSION_GRANTED) "granted" else "missing"
        val sendSmsPermission = if (ContextCompat.checkSelfPermission(this, Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED) "granted" else "missing"
        GatewayLog.append("Base URL: $baseUrl")
        GatewayLog.append("Connection: ${GatewaySettingsStore.getConnectionMode(this)}")
        GatewayLog.append("Gateway token: $tokenState")
        GatewayLog.append("Local SMS line: $localSmsNumber")
        GatewayLog.append("RECEIVE_SMS permission: $receiveSmsPermission")
        GatewayLog.append("SEND_SMS permission: $sendSmsPermission")
        GatewayLog.append("Notification access: ${if (isNotificationAccessEnabled()) "enabled" else "missing"}")
    }

    private fun isNotificationAccessEnabled(): Boolean {
        val enabledListeners = Settings.Secure.getString(contentResolver, "enabled_notification_listeners").orEmpty()
        return enabledListeners.contains(packageName, ignoreCase = true)
    }

    private fun refreshLog() {
        val (version, lines) = GatewayLog.snapshot()
        if (version == lastVersion) {
            return
        }
        lastVersion = version
        statusText.text = lines.joinToString(separator = "\n")
    }

    private fun showSettingsDialog() {
        val density = resources.displayMetrics.density
        fun Int.dp() = (this * density).toInt()

        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24.dp(), 16.dp(), 24.dp(), 8.dp())
        }

        val connectionModeInput = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, listOf("Network (Wi-Fi / LAN)", "USB development (ADB)"))
            setSelection(GatewaySettingsStore.getConnectionMode(this@MainActivity).ordinal)
        }
        container.addView(connectionModeInput)

        val baseUrlInput = EditText(this).apply {
            hint = "Network basestation URL"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setText(GatewaySettingsStore.getNetworkBaseUrl(this@MainActivity))
        }
        container.addView(baseUrlInput)

        val usbPortInput = EditText(this).apply {
            hint = "USB basestation port"
            inputType = InputType.TYPE_CLASS_NUMBER
            setText(GatewaySettingsStore.getUsbPort(this@MainActivity).toString())
        }
        container.addView(usbPortInput)

        val tokenInput = EditText(this).apply {
            hint = "Gateway auth token"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setText(GatewaySettingsStore.getAuthToken(this@MainActivity))
        }
        container.addView(tokenInput)

        val pollIntervalInput = EditText(this).apply {
            hint = "Poll interval seconds"
            inputType = InputType.TYPE_CLASS_NUMBER
            setText(GatewaySettingsStore.getPollIntervalSeconds(this@MainActivity).toString())
        }
        container.addView(pollIntervalInput)

        val pollLimitInput = EditText(this).apply {
            hint = "Poll limit"
            inputType = InputType.TYPE_CLASS_NUMBER
            setText(GatewaySettingsStore.getPollLimit(this@MainActivity).toString())
        }
        container.addView(pollLimitInput)

        val localSmsNumberInput = EditText(this).apply {
            hint = "Local SMS number"
            inputType = InputType.TYPE_CLASS_PHONE
            setText(GatewaySettingsStore.getLocalSmsNumber(this@MainActivity))
        }
        container.addView(localSmsNumberInput)

        AlertDialog.Builder(this)
            .setTitle(getString(R.string.settings_title))
            .setView(ScrollView(this).apply { addView(container) })
            .setPositiveButton("Save") { _, _ ->
                GatewaySettingsStore.setBaseUrl(this, baseUrlInput.text?.toString().orEmpty())
                GatewaySettingsStore.setConnectionMode(this, GatewayConnectionMode.entries[connectionModeInput.selectedItemPosition])
                GatewaySettingsStore.setUsbPort(this, usbPortInput.text?.toString()?.toIntOrNull() ?: 18098)
                GatewaySettingsStore.setAuthToken(this, tokenInput.text?.toString().orEmpty())
                GatewaySettingsStore.setPollIntervalSeconds(this, pollIntervalInput.text?.toString()?.toIntOrNull() ?: 15)
                GatewaySettingsStore.setPollLimit(this, pollLimitInput.text?.toString()?.toIntOrNull() ?: 10)
                GatewaySettingsStore.setLocalSmsNumber(this, localSmsNumberInput.text?.toString().orEmpty())
                GatewayLog.append("Settings saved")
                showCurrentConfigSummary()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
}