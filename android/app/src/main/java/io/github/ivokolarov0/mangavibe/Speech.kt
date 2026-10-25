package io.github.ivokolarov0.mangavibe

import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.widget.Toast

/**
 * Read aloud in the reader, with the phone's own text-to-speech (Android settings → Text-to-speech):
 * the WebView has no speech synthesis of its own. The web app hands over a text with a number, one
 * part per line. [onStart] gets the number and the part's index as each part starts (so a change of
 * speed can carry on from there), [onDone] the number once all of it is read, on the main thread.
 * Started the first time something is read, so nobody else pays for it.
 */
class Speech(
    context: Context,
    private val onStart: (Int, Int) -> Unit,
    private val onDone: (Int) -> Unit,
) {

    private val app = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private var tts: TextToSpeech? = null
    private var ready = false
    /** what was asked before the engine was ready */
    private var waiting: Triple<Int, String, Float>? = null

    init {
        tts = TextToSpeech(app) { status ->
            main.post {
                ready = status == TextToSpeech.SUCCESS
                if (!ready) {
                    Toast.makeText(app, "This phone has no text-to-speech voice", Toast.LENGTH_LONG).show()
                }
                waiting?.let { (id, text, rate) ->
                    waiting = null
                    if (ready) speak(id, text, rate)
                }
            }
        }
        tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) = started(utteranceId ?: "")
            override fun onDone(utteranceId: String?) = finished(utteranceId ?: "")
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) = finished(utteranceId ?: "")
        })
    }

    /** part 3 of text 12 ("12:3", or "12:3:last") started */
    private fun started(utteranceId: String) {
        val bits = utteranceId.split(":")
        val id = bits.getOrNull(0)?.toIntOrNull() ?: return
        val part = bits.getOrNull(1)?.toIntOrNull() ?: return
        main.post { onStart(id, part) }
    }

    /** a part was read: when it was the last one ("12:3:last"), the whole text is done */
    private fun finished(utteranceId: String) {
        if (!utteranceId.endsWith(":last")) return
        val id = utteranceId.substringBefore(":").toIntOrNull() ?: return
        main.post { onDone(id) }
    }

    /** read [text] at [rate] (1 = normal), instead of whatever is being read now */
    fun speak(id: Int, text: String, rate: Float) {
        val t = tts ?: return
        if (!ready) {
            waiting = Triple(id, text, rate)
            return
        }
        t.setSpeechRate(rate.coerceIn(0.5f, 3f))
        // one part per line (as the web app counts them), so the engine pauses between them; a line
        // over the engine's limit is cut, which the web app never sends
        val limit = TextToSpeech.getMaxSpeechInputLength() - 1
        val parts = text.lines().map { it.trim() }.filter { it.isNotEmpty() }.flatMap { it.chunked(limit) }
        if (parts.isEmpty()) {
            main.post { onDone(id) }
            return
        }
        parts.forEachIndexed { i, part ->
            val mode = if (i == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD
            t.speak(part, mode, Bundle(), if (i == parts.lastIndex) "$id:$i:last" else "$id:$i")
        }
    }

    fun stop() {
        waiting = null
        if (ready) tts?.stop()
    }

    fun shutdown() {
        tts?.shutdown()
        tts = null
    }
}
