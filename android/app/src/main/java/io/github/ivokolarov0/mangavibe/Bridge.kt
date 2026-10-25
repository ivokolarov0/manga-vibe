package io.github.ivokolarov0.mangavibe

import android.webkit.JavascriptInterface

/**
 * What the web app can ask of the Android app, as window.MangaVibeAndroid.* (see app/web/static/js/common.js).
 * These run on a background thread, so each hands its work to the main thread.
 * The other direction (volume keys, back button) goes to window.MangaVibeApp in the page.
 */
class Bridge(private val activity: MainActivity) {

    /** The reader opened or closed. While it is open the screen stays on, and the volume keys turn pages if wanted. */
    @JavascriptInterface
    fun readerState(open: Boolean, volumeKeys: Boolean) {
        activity.runOnUiThread { if (activity.showsServer()) activity.setReaderState(open, volumeKeys) }
    }

    /** Settings → Android app → Change server */
    @JavascriptInterface
    fun changeServer() {
        activity.runOnUiThread { if (activity.showsServer()) activity.showSetup(true) }
    }

    /** Read aloud in the reader: read [text] with the phone's text-to-speech; MangaVibeApp.spoke(id) when done */
    @JavascriptInterface
    fun speak(id: Int, text: String, rate: Float) {
        activity.runOnUiThread { if (activity.showsServer()) activity.speak(id, text, rate) }
    }

    @JavascriptInterface
    fun stopSpeaking() {
        activity.runOnUiThread { activity.stopSpeaking() }
    }

    @JavascriptInterface
    fun server(): String = activity.server ?: ""

    @JavascriptInterface
    fun version(): String = activity.appVersion
}
