package com.ilias.practiceroom

import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import java.io.File
import java.io.FileOutputStream
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

    companion object {
        private const val MAX_SIDE = 3500f
    }
}
