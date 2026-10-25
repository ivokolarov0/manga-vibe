package io.github.ivokolarov0.mangavibe

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.KeyEvent
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.webkit.RenderProcessGoneDetail
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread
import kotlin.math.max

/**
 * The whole app: MangaVibe's web app in a WebView that fills the screen between the phone's status
 * and navigation bars. The reader logic stays in the web app; this adds what a browser can't do over
 * plain HTTP: no browser bars, the screen kept on while reading, and page turns with the volume keys.
 */
class MainActivity : Activity() {

    private lateinit var root: View
    private lateinit var web: WebView
    private lateinit var setup: View
    private lateinit var address: EditText
    private lateinit var setupError: TextView
    private lateinit var connect: Button
    private lateinit var setupCancel: Button
    private lateinit var error: View

    /** the server's origin, e.g. "http://192.168.1.20:8090"; null until set up */
    @Volatile
    var server: String? = null
        private set
    lateinit var appVersion: String
        private set

    private var readerOpen = false
    private var volumeKeys = false
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        root = findViewById(R.id.root)
        web = findViewById(R.id.web)
        setup = findViewById(R.id.setup)
        address = findViewById(R.id.address)
        setupError = findViewById(R.id.setup_error)
        connect = findViewById(R.id.connect)
        setupCancel = findViewById(R.id.setup_cancel)
        error = findViewById(R.id.error)
        appVersion = packageManager.getPackageInfo(packageName, 0).versionName ?: "dev"

        padForSystemBars()

        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) {
            WebView.setWebContentsDebuggingEnabled(true)  // chrome://inspect
        }
        web.setBackgroundColor(getColor(R.color.bg))
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            // the web app looks for this to know it runs in the app
            userAgentString = "$userAgentString MangaVibeAndroid/$appVersion"
        }
        web.addJavascriptInterface(Bridge(this), "MangaVibeAndroid")
        web.webViewClient = Client()
        web.webChromeClient = Chrome()

        connect.setOnClickListener { tryConnect() }
        address.setOnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_GO) tryConnect()
            action == EditorInfo.IME_ACTION_GO
        }
        setupCancel.setOnClickListener { setup.visibility = View.GONE }
        findViewById<Button>(R.id.retry).setOnClickListener { retry() }
        findViewById<Button>(R.id.change).setOnClickListener { showSetup(true) }

        server = Server.saved(this)
        if (!openLink(intent)) {
            val s = server
            if (s == null) showSetup(false) else load(s)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        openLink(intent)
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
    }

    override fun onPause() {
        web.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        speech?.shutdown()
        web.destroy()
        super.onDestroy()
    }

    /* ---------------- server ---------------- */

    private fun load(url: String) {
        setup.visibility = View.GONE
        error.visibility = View.GONE
        web.loadUrl(url)
    }

    /** whether the page in the WebView is the server's (the bridge only listens to that page) */
    fun showsServer(): Boolean = server != null && Server.origin(Uri.parse(web.url ?: "")) == server

    private fun useServer(origin: String) {
        server = origin
        Server.save(this, origin)
        web.clearHistory()
    }

    /**
     * "Open in app" from the browser: mangavibe://open with the page in the "url" extra (Chrome passes
     * S.url=... that way) or query parameter. Sets the server up on first use. Returns false if the
     * intent isn't such a link.
     */
    private fun openLink(intent: Intent?): Boolean {
        val data = intent?.data ?: return false
        if (intent.action != Intent.ACTION_VIEW || data.scheme != "mangavibe") return false
        val url = intent.getStringExtra("url") ?: data.getQueryParameter("url") ?: return false
        val origin = Server.origin(Uri.parse(url)) ?: return false
        val current = server
        if (current == null || current == origin) {
            useServer(origin)
            load(url)
        } else {
            AlertDialog.Builder(this)
                .setTitle(R.string.switch_title)
                .setMessage(getString(R.string.switch_text, origin, current))
                .setPositiveButton(R.string.switch_yes) { _, _ -> useServer(origin); load(url) }
                .setNegativeButton(R.string.switch_no) { _, _ -> if (web.url == null) load(current) }
                .setCancelable(false)
                .show()
        }
        return true
    }

    fun showSetup(cancelable: Boolean) {
        setup.visibility = View.VISIBLE
        error.visibility = View.GONE
        setupCancel.visibility = if (cancelable && server != null) View.VISIBLE else View.GONE
        setupError.visibility = View.GONE
        address.setText(server?.removePrefix("http://") ?: "")
        address.setSelection(address.text.length)
        connect.isEnabled = true
        connect.setText(R.string.setup_connect)
    }

    private fun tryConnect() {
        val origin = Server.normalize(address.text.toString())
        if (origin == null) {
            showSetupError(getString(R.string.setup_bad_address))
            return
        }
        connect.isEnabled = false
        connect.setText(R.string.setup_checking)
        setupError.visibility = View.GONE
        thread {
            val ok = Server.answers(origin)
            runOnUiThread {
                connect.isEnabled = true
                connect.setText(R.string.setup_connect)
                if (!ok) {
                    showSetupError(getString(R.string.setup_no_answer, origin))
                    return@runOnUiThread
                }
                getSystemService(InputMethodManager::class.java)?.hideSoftInputFromWindow(address.windowToken, 0)
                useServer(origin)
                load(origin)
            }
        }
    }

    private fun showSetupError(text: String) {
        setupError.text = text
        setupError.visibility = View.VISIBLE
    }

    private fun showError() {
        if (setup.visibility == View.VISIBLE) return
        findViewById<TextView>(R.id.error_text).text = getString(R.string.error_text, server ?: "")
        error.visibility = View.VISIBLE
        setReaderState(false, false)
    }

    private fun retry() {
        error.visibility = View.GONE
        val url = web.url
        if (url != null && Server.origin(Uri.parse(url)) == server) web.reload() else server?.let { load(it) }
    }

    /* ---------------- reader: screen on, volume keys ---------------- */

    fun setReaderState(open: Boolean, volumeKeys: Boolean) {
        readerOpen = open
        this.volumeKeys = volumeKeys
        if (open) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    /* ---------------- reader: read aloud (the WebView has no speech synthesis) ---------------- */

    private var speech: Speech? = null

    fun speak(id: Int, text: String, rate: Float) {
        val s = speech ?: Speech(
            this,
            onStart = { run, part ->
                web.evaluateJavascript("window.MangaVibeApp && MangaVibeApp.speaking && MangaVibeApp.speaking($run, $part)", null)
            },
            onDone = { run ->
                web.evaluateJavascript("window.MangaVibeApp && MangaVibeApp.spoke && MangaVibeApp.spoke($run)", null)
            },
        ).also { speech = it }
        s.speak(id, text, rate)
    }

    fun stopSpeaking() {
        speech?.stop()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val code = event.keyCode
        val volume = code == KeyEvent.KEYCODE_VOLUME_DOWN || code == KeyEvent.KEYCODE_VOLUME_UP
        if (volume && readerOpen && volumeKeys && setup.visibility != View.VISIBLE && error.visibility != View.VISIBLE) {
            // one page per press; the key-up is swallowed too, so the volume doesn't change
            if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) {
                val dir = if (code == KeyEvent.KEYCODE_VOLUME_DOWN) "next" else "prev"
                web.evaluateJavascript("window.MangaVibeApp && MangaVibeApp.volumeKey('$dir')", null)
            }
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    /* ---------------- back button ---------------- */

    @Deprecated("Deprecated in Java")
    @Suppress("DEPRECATION")
    override fun onBackPressed() {
        when {
            setup.visibility == View.VISIBLE && server != null && web.url != null -> setup.visibility = View.GONE
            setup.visibility == View.VISIBLE || error.visibility == View.VISIBLE -> super.onBackPressed()
            // the page closes its own panels first (reader options, sheets, dialogs)
            else -> web.evaluateJavascript("!!(window.MangaVibeApp && MangaVibeApp.back())") { handled ->
                if (handled == "true") return@evaluateJavascript
                if (web.canGoBack()) web.goBack() else super.onBackPressed()
            }
        }
    }

    /* ---------------- system bars ---------------- */

    /**
     * The status and navigation bars stay visible. The window draws behind them (always so from
     * Android 15 on, so the same on every version), and the root is padded to keep the page between
     * the bars, out of the camera cutout and above the keyboard.
     */
    @Suppress("DEPRECATION")  // setDecorFitsSystemWindows is the default from Android 15 on; still needed on 11-14
    private fun padForSystemBars() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(false)
        } else {
            window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION)
        }
        root.setOnApplyWindowInsetsListener { v, insets ->
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val i = insets.getInsets(WindowInsets.Type.systemBars()
                    or WindowInsets.Type.displayCutout() or WindowInsets.Type.ime())
                v.setPadding(i.left, i.top, i.right, i.bottom)
            } else {
                // systemWindowInsets: the bars and, with adjustResize, the keyboard
                val cut = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) insets.displayCutout else null
                v.setPadding(max(insets.systemWindowInsetLeft, cut?.safeInsetLeft ?: 0),
                    max(insets.systemWindowInsetTop, cut?.safeInsetTop ?: 0),
                    max(insets.systemWindowInsetRight, cut?.safeInsetRight ?: 0),
                    max(insets.systemWindowInsetBottom, cut?.safeInsetBottom ?: 0))
            }
            // the padding already keeps the page clear: the WebView mustn't see the bars too, or the
            // page adds its own env(safe-area-inset-*) space on top (an empty row above the nav bar)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) WindowInsets.CONSUMED
            else insets.consumeSystemWindowInsets().let {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) it.consumeDisplayCutout() else it
            }
        }
    }

    /* ---------------- WebView ---------------- */

    private inner class Client : WebViewClient() {
        /** the server's pages stay in the app; everything else (AniList, GitHub, ...) opens in the browser */
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val uri = request.url
            if (server != null && Server.origin(uri) == server) return false
            try {
                startActivity(Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE))
            } catch (e: ActivityNotFoundException) {
                Toast.makeText(this@MainActivity, R.string.no_app_for_link, Toast.LENGTH_SHORT).show()
            }
            return true
        }

        override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
            setReaderState(false, false)  // a fresh page: the reader tells us again if it opens
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, err: WebResourceError) {
            if (request.isForMainFrame) showError()
        }

        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
            // e.g. a reverse proxy's 502 while the server restarts after an update
            if (request.isForMainFrame && response.statusCode >= 500) showError()
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            recreate()  // the page's process crashed or was killed for memory: start over
            return true
        }
    }

    private inner class Chrome : WebChromeClient() {
        /** <input type="file"> (e.g. uploading a cover) */
        override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>,
                                       params: FileChooserParams): Boolean {
            fileCallback?.onReceiveValue(null)
            fileCallback = callback
            try {
                @Suppress("DEPRECATION")
                startActivityForResult(params.createIntent(), PICK_FILE)
            } catch (e: ActivityNotFoundException) {
                fileCallback = null
                callback.onReceiveValue(null)
            }
            return true
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == PICK_FILE) {
            fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
            fileCallback = null
        }
    }

    private companion object {
        const val PICK_FILE = 1
    }
}
