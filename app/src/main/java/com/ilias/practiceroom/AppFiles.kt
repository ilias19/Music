package com.ilias.practiceroom

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import java.io.File

/**
 * Copies the bundled web app, gallery and recognition models out of the APK
 * into app storage (ONNX Runtime and Python need real files). Runs again only
 * when the app version changes. Scores, recordings and the practice log live
 * in [data] and are never touched by updates.
 */
class AppFiles(private val context: Context) {
    val root: File = context.filesDir
    val www = File(root, "www")
    val gallery = File(root, "gallery")
    val models = File(root, "models")
    val data = File(root, "data")

    fun install(progress: (String) -> Unit) {
        data.mkdirs()
        val stamp = File(root, "assets.version")
        val version = versionCode().toString()
        if (stamp.exists() && stamp.readText() == version && models.list()?.size == 3) return
        progress("Setting up (first launch after install or update)…")
        for (dir in listOf("www", "gallery", "models")) {
            val target = File(root, dir)
            target.deleteRecursively()
            copyTree(dir, target)
        }
        stamp.writeText(version)
    }

    private fun copyTree(assetPath: String, target: File) {
        val children = context.assets.list(assetPath).orEmpty()
        if (children.isEmpty()) {
            target.parentFile?.mkdirs()
            context.assets.open(assetPath).use { input ->
                target.outputStream().use { input.copyTo(it, 1 shl 20) }
            }
            return
        }
        target.mkdirs()
        for (child in children) copyTree("$assetPath/$child", File(target, child))
    }

    private fun versionCode(): Long {
        val info = if (Build.VERSION.SDK_INT >= 33) {
            context.packageManager.getPackageInfo(context.packageName, PackageManager.PackageInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION") context.packageManager.getPackageInfo(context.packageName, 0)
        }
        return info.longVersionCode xor info.lastUpdateTime
    }
}
