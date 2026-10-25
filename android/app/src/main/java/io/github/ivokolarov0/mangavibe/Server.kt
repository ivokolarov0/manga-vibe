package io.github.ivokolarov0.mangavibe

import android.content.Context
import android.net.Uri
import java.net.HttpURLConnection
import java.net.URL

/** The MangaVibe server the app shows: stored as an origin, e.g. "http://192.168.1.20:8090". */
object Server {
    private const val PREFS = "mangavibe"
    private const val KEY = "server"

    fun saved(ctx: Context): String? =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null)

    fun save(ctx: Context, origin: String) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, origin).apply()
    }

    /**
     * What the user typed ("192.168.1.20:8090", "http://nas.local:8090/#/") as an origin, or null if
     * it isn't a web address. Only the origin is kept: the web app always uses paths from the root.
     */
    fun normalize(input: String): String? {
        var s = input.trim()
        if (s.isEmpty()) return null
        if (!s.contains("://")) s = "http://$s"
        return origin(Uri.parse(s))
    }

    /** "http://host:port" of an http(s) address, or null for anything else (mailto:, intent:, ...) */
    fun origin(uri: Uri?): String? {
        val scheme = uri?.scheme?.lowercase() ?: return null
        val host = uri.host?.lowercase()
        if (scheme != "http" && scheme != "https" || host.isNullOrEmpty()) return null
        return if (uri.port == -1) "$scheme://$host" else "$scheme://$host:${uri.port}"
    }

    /** Whether a MangaVibe answers at this origin. Blocks: call it off the main thread. */
    fun answers(origin: String): Boolean = try {
        val c = URL("$origin/api/health").openConnection() as HttpURLConnection
        c.connectTimeout = 5000
        c.readTimeout = 5000
        try {
            c.responseCode == 200 && c.inputStream.bufferedReader().use { it.readText() }.contains("\"ok\"")
        } finally {
            c.disconnect()
        }
    } catch (e: Exception) {
        false
    }
}
