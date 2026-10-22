package com.ilias.practiceroom

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.roundToInt

/** Phone-only services the Python backend calls (desktop tests use PyMuPDF instead). */
class PlatformBridge(private val context: Context) {
    // ---- scan progress (called from the Python scan worker) ----
    fun scanProgress(id: String, title: String, message: String, percent: Int, queued: Int) =
        ScanService.progress(context, ScanService.Progress(id, title, message, percent, queued))

    fun scanFinished(id: String, title: String, ok: Boolean, queued: Int, seconds: Int) =
        ScanService.finished(context, id, title, ok, seconds)

    /** A snapshot for the scan details: is the phone hot, short of memory, is the app in front... */
    fun deviceState(): String {
        val pm = context.getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as android.app.ActivityManager
        val mem = android.app.ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }
        val freqs = JSONArray()
        for (i in 0 until Runtime.getRuntime().availableProcessors()) {
            val cur = runCatching { File("/sys/devices/system/cpu/cpu$i/cpufreq/scaling_cur_freq").readText().trim().toLong() / 1000 }.getOrNull()
            val max = runCatching { File("/sys/devices/system/cpu/cpu$i/cpufreq/cpuinfo_max_freq").readText().trim().toLong() / 1000 }.getOrNull()
            freqs.put(if (cur != null && max != null) "$cur/$max" else "?")
        }
        val rt = Runtime.getRuntime()
        val native = android.os.Debug.getNativeHeapAllocatedSize() / 1_000_000
        return JSONObject()
            .put("phone", "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}")
            .put("android", android.os.Build.VERSION.RELEASE)
            .put("app_in_front", ScanService.appVisible)
            .put("thermal", pm.currentThermalStatus)              // 0 none … 3 severe, 4+ critical
            .put("battery_saver", pm.isPowerSaveMode)
            .put("battery_optimised", !pm.isIgnoringBatteryOptimizations(context.packageName))
            .put("free_ram_mb", mem.availMem / 1_000_000)
            .put("low_memory", mem.lowMemory)
            .put("app_native_mb", native)
            .put("app_java_mb", (rt.totalMemory() - rt.freeMemory()) / 1_000_000)
            .put("cpu_mhz_now_max", freqs)
            .toString()
    }

    /** Scan threads: a little above normal priority, so they keep their CPU share when the app is in the background. */
    fun boostThread() = android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_FOREGROUND)

    fun scanIdle() = ScanService.idle()

    /** Render every page of a PDF to PNG with Android's PdfRenderer. Returns the PNG paths. */
    fun renderPdf(pdfPath: String, outDir: String, dpi: Int): Array<String> {
        val out = mutableListOf<String>()
        ParcelFileDescriptor.open(File(pdfPath), ParcelFileDescriptor.MODE_READ_ONLY).use { fd ->
            PdfRenderer(fd).use { renderer ->
                for (i in 0 until renderer.pageCount) {
                    renderer.openPage(i).use { page ->
                        var scale = dpi / 72f
                        val longest = max(page.width, page.height) * scale
                        if (longest > MAX_SIDE) scale *= MAX_SIDE / longest
                        val w = (page.width * scale).roundToInt().coerceAtLeast(1)
                        val h = (page.height * scale).roundToInt().coerceAtLeast(1)
                        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
                        bmp.eraseColor(Color.WHITE)
                        page.render(bmp, null, null, PdfRenderer.Page.RENDER_MODE_FOR_PRINT)
                        val file = File(outDir, "p%03d.png".format(i))
                        FileOutputStream(file).use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
                        bmp.recycle()
                        out += file.absolutePath
                    }
                }
            }
        }
        return out.toTypedArray()
    }

    /**
     * Words on a page image with their boxes (ML Kit, on device, offline), as JSON:
     * [{"t": "laq", "x0": 142, "y0": 455, "x1": 160, "y1": 475}, ...] in image pixels.
     * Used to put lyrics, chord symbols and the tempo back into scanned scores.
     */
    fun ocr(imagePath: String): String {
        val bmp = BitmapFactory.decodeFile(imagePath) ?: return "[]"
        val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
        try {
            val result = Tasks.await(recognizer.process(InputImage.fromBitmap(bmp, 0)), 90, TimeUnit.SECONDS)
            val out = JSONArray()
            for (block in result.textBlocks) for (line in block.lines) for (el in line.elements) {
                val b = el.boundingBox ?: continue
                out.put(JSONObject().put("t", el.text).put("x0", b.left).put("y0", b.top).put("x1", b.right).put("y1", b.bottom))
            }
            return out.toString()
        } finally {
            recognizer.close()
            bmp.recycle()
        }
    }

    companion object {
        private const val MAX_SIDE = 3500f
    }
}
