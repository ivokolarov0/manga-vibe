package io.github.ivokolarov0.mangavibe

import android.content.Context
import android.net.Uri
import java.net.HttpURLConnection
import java.net.URL

/** The MangaVibe server the app shows: stored as an origin, e.g. "http://192.168.1.20:8090". */
object Server {
    private const val PREFS = "mangavibe"
    private const val KEY = "server"
    private const val KNOWN = "servers"
    private const val MAX_KNOWN = 8

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun saved(ctx: Context): String? = prefs(ctx).getString(KEY, null)

    /** The current server, also put first in the saved list. */
    fun save(ctx: Context, origin: String) {
        val list = listOf(origin) + known(ctx).filter { it != origin }
        prefs(ctx).edit()
            .putString(KEY, origin)
            .putString(KNOWN, list.take(MAX_KNOWN).joinToString("\n"))
            .apply()
    }

    /**
     * Every address the app has connected to, last used first: e.g. the home network's and the
     * Tailscale one, to switch between them (and to fall back to when one doesn't answer).
     */
    fun known(ctx: Context): List<String> {
        val list = prefs(ctx).getString(KNOWN, null)?.split("\n")?.filter { it.isNotEmpty() } ?: emptyList()
        // from before the list existed: just the one server
        val current = saved(ctx)
        return if (current != null && current !in list) listOf(current) + list else list
    }

    fun forget(ctx: Context, origin: String) {
        prefs(ctx).edit().putString(KNOWN, known(ctx).filter { it != origin }.joinToString("\n")).apply()
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
    fun answers(origin: String, timeoutMs: Int = 5000): Boolean = try {
        val c = URL("$origin/api/health").openConnection() as HttpURLConnection
        c.connectTimeout = timeoutMs
        c.readTimeout = timeoutMs
        try {
            c.responseCode == 200 && c.inputStream.bufferedReader().use { it.readText() }.contains("\"ok\"")
        } finally {
            c.disconnect()
        }
    } catch (e: Exception) {
        false
    }
}
