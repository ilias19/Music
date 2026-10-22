package com.ilias.practiceroom

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.util.UUID
import kotlin.concurrent.thread

class MainActivity : ComponentActivity() {
    private lateinit var web: WebView
    private lateinit var status: TextView
    private var origin = ""

    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var cameraUri: Uri? = null
    private var pendingMic: PermissionRequest? = null

    private val pickFiles = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        fileCallback?.onReceiveValue(uris.toTypedArray())
        fileCallback = null
    }
    private val pickFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        fileCallback?.onReceiveValue(uri?.let { arrayOf(it) })
        fileCallback = null
    }
    private val takePhoto = registerForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val uri = cameraUri
        fileCallback?.onReceiveValue(if (ok && uri != null) arrayOf(uri) else null)
        fileCallback = null
    }
    private val askMic = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val req = pendingMic ?: return@registerForActivityResult
        pendingMic = null
        if (granted) req.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE)) else req.deny()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val rootView = FrameLayout(this).apply { setBackgroundColor(getColor(R.color.paper)) }
        web = WebView(this).apply { visibility = View.INVISIBLE }
        status = TextView(this).apply {
            text = "Starting Practice Room…"
            textSize = 16f
            setTextColor(getColor(R.color.ink))
            gravity = android.view.Gravity.CENTER
        }
        rootView.addView(web, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        rootView.addView(status, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        setContentView(rootView)
        // Android 15 draws apps edge to edge: keep the page clear of the status/navigation bars and keyboard.
        ViewCompat.setOnApplyWindowInsetsListener(rootView) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }

        setUpWebView()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })

        val selfTest = intent.getBooleanExtra("selftest", false)
        thread(name = "startup") { startBackend(selfTest) }
    }

    private fun startBackend(selfTest: Boolean) {
        try {
            val files = AppFiles(this)
            val token = UUID.randomUUID().toString()
            // One backend per process, even if the activity is created twice while starting.
            val port: Int
            val server: com.chaquo.python.PyObject
            synchronized(startLock) {
                files.install { msg -> runOnUiThread { status.text = msg } }
                if (!Python.isStarted()) Python.start(AndroidPlatform(applicationContext))
                server = Python.getInstance().getModule("server")
                port = server.callAttr(
                    "start", files.data.path, files.www.path, files.gallery.path, files.models.path,
                    bridge, PlatformBridge(), token,
                ).toInt()
            }
            origin = "http://127.0.0.1:$port"
            Log.i(TAG, "Backend listening on $origin")
            runOnUiThread {
                web.loadUrl("$origin/__start?k=$token")
            }
            thread(name = "warmup") { runCatching { server.callAttr("warm_up") } }
            if (selfTest) runSelfTest(server, token)
        } catch (t: Throwable) {
            Log.e(TAG, "Startup failed", t)
            runOnUiThread { status.text = "Practice Room couldn't start:\n${t.message}" }
            if (selfTest) Log.e(SELFTEST, "FAIL startup: $t")
        }
    }

    /** CI runs the app with --ez selftest true and reads the result from logcat. */
    private fun runSelfTest(server: com.chaquo.python.PyObject, token: String) {
        thread(name = "selftest") {
            try {
                val image = File(cacheDir, "selftest.png")
                assets.open("selftest/ode.png").use { i -> image.outputStream().use { i.copyTo(it) } }
                val report = server.callAttr("selftest", image.path, token).toString()
                Log.i(SELFTEST, "PASS $report")
            } catch (t: Throwable) {
                Log.e(SELFTEST, "FAIL $t", t)
            }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setUpWebView() {
        // The emulator build lets CI drive the page through Chrome DevTools (tests/e2e). Phones: off.
        if (BuildConfig.FLAVOR == "emu") WebView.setWebContentsDebuggingEnabled(true)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = false
            allowContentAccess = true
            builtInZoomControls = false
            textZoom = 100
        }
        web.addJavascriptInterface(NativeApi(), "AndroidApp")

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url.toString()
                if (origin.isNotEmpty() && url.startsWith(origin)) return false
                // Anything outside the app opens in the browser.
                try { startActivity(Intent(Intent.ACTION_VIEW, request.url)) } catch (_: ActivityNotFoundException) {}
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                if (url.startsWith(origin) && !url.contains("__start")) {
                    status.visibility = View.GONE
                    web.visibility = View.VISIBLE
                }
            }
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                runOnUiThread {
                    if (!request.resources.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)) {
                        request.deny(); return@runOnUiThread
                    }
                    if (ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.RECORD_AUDIO)
                        == PackageManager.PERMISSION_GRANTED
                    ) {
                        request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
                    } else {
                        pendingMic = request
                        askMic.launch(Manifest.permission.RECORD_AUDIO)
                    }
                }
            }

            override fun onShowFileChooser(
                view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams,
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return try {
                    if (params.isCaptureEnabled) {
                        val dir = File(cacheDir, "camera").apply { mkdirs() }
                        val photo = File(dir, "photo-${System.currentTimeMillis()}.jpg")
                        cameraUri = FileProvider.getUriForFile(this@MainActivity, "$packageName.files", photo)
                        takePhoto.launch(cameraUri!!)
                    } else {
                        // MusicXML has no reliable MIME type on Android: allow everything, the app validates.
                        val types = arrayOf("*/*")
                        if (params.mode == FileChooserParams.MODE_OPEN_MULTIPLE) pickFiles.launch(types)
                        else pickFile.launch(types)
                    }
                    true
                } catch (e: Exception) {
                    Log.e(TAG, "File chooser failed", e)
                    fileCallback = null
                    false
                }
            }
        }

        web.setDownloadListener { url, _, contentDisposition, mimeType, _ ->
            val name = URLUtil.guessFileName(url, contentDisposition, mimeType)
            thread(name = "download") {
                val msg = try {
                    Downloads.save(this, url, name, mimeType ?: "application/octet-stream")
                    "Saved to Downloads/Practice Room: $name"
                } catch (e: Exception) {
                    Log.e(TAG, "Download failed", e)
                    "Couldn't save $name: ${e.message}"
                }
                runOnUiThread { Toast.makeText(this, msg, Toast.LENGTH_LONG).show() }
            }
        }
    }

    /** Called from the page's JavaScript as window.AndroidApp. */
    inner class NativeApi {
        @JavascriptInterface
        fun keepScreenOn(on: Boolean) {
            runOnUiThread {
                if (on) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
        }
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }

    companion object {
        private val startLock = Any()
        private val bridge by lazy { OrtBridge() }
        private const val TAG = "PracticeRoom"
        private const val SELFTEST = "PR_SELFTEST"
    }
}
