package com.ilias.practiceroom

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
class PlatformBridge {
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
