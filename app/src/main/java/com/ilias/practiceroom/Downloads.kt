package com.ilias.practiceroom

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import android.webkit.CookieManager
import java.net.HttpURLConnection
import java.net.URL

/** Saves files the web page "downloads" (exports, recordings) into the phone's Downloads folder. */
object Downloads {
    /** Fetches [url] from the local server and stores it. Returns the saved file's Uri. */
    fun save(context: Context, url: String, fileName: String, mime: String): Uri {
        val conn = URL(url).openConnection() as HttpURLConnection
        CookieManager.getInstance().getCookie(url)?.let { conn.setRequestProperty("Cookie", it) }
        conn.connectTimeout = 15_000
        conn.readTimeout = 120_000
        try {
            if (conn.responseCode != 200) throw IllegalStateException("HTTP ${conn.responseCode}")
            val resolver = context.contentResolver
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, fileName)
                put(MediaStore.Downloads.MIME_TYPE, mime)
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Practice Room")
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: throw IllegalStateException("Couldn't create the file")
            resolver.openOutputStream(uri)!!.use { out -> conn.inputStream.use { it.copyTo(out) } }
            values.clear()
            values.put(MediaStore.Downloads.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            return uri
        } finally {
            conn.disconnect()
        }
    }
}
