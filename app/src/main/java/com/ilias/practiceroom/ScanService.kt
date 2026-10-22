package com.ilias.practiceroom

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.content.ContextCompat

/**
 * Keeps scanning going when you leave the app or turn the screen off.
 *
 * Android freezes apps in the background within seconds, which stopped scans
 * mid-page. While anything is scanning, this foreground service shows a progress
 * notification and holds a CPU wake lock; the scan itself still runs on the Python
 * worker thread in the same process. When the queue is empty it goes away, and a
 * "ready" notification opens the score.
 */
class ScanService : Service() {
    private var wake: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        wake = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "PracticeRoom:scan")
            .apply { setReferenceCounted(false); acquire(WAKE_TIMEOUT_MS) }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val n = progressNotification(this, current)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(ID_PROGRESS, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(ID_PROGRESS, n)
        }
        if (stopRequested) stopNow()
        return START_NOT_STICKY
    }

    override fun onTimeout(startId: Int, fgsType: Int) {
        // Android 15 caps dataSync services at 6 h a day; a scan never gets near that.
        stopNow()
    }

    private fun stopNow() {
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        wake?.let { if (it.isHeld) it.release() }
        if (instance === this) instance = null
        super.onDestroy()
    }

    data class Progress(val id: String, val title: String, val message: String, val percent: Int, val queued: Int)

    companion object {
        private const val TAG = "PracticeRoom"
        private const val CH_PROGRESS = "scan_progress"
        private const val CH_DONE = "scan_done"
        private const val ID_PROGRESS = 1
        private const val WAKE_TIMEOUT_MS = 60 * 60 * 1000L

        @Volatile private var instance: ScanService? = null
        @Volatile private var current = Progress("", "", "Reading the music…", 0, 0)
        @Volatile private var stopRequested = false

        /** Is the app's screen in front? Then the page shows progress and no "ready" notification is needed. */
        @Volatile var appVisible = false

        fun progress(ctx: Context, p: Progress) {
            current = p
            stopRequested = false
            channels(ctx)
            val svc = instance
            if (svc != null) {
                ctx.getSystemService(NotificationManager::class.java).notify(ID_PROGRESS, progressNotification(ctx, p))
                return
            }
            try {
                ContextCompat.startForegroundService(ctx, Intent(ctx, ScanService::class.java))
            } catch (e: Exception) {
                // e.g. started while the app was already in the background: the scan still runs while it can
                Log.w(TAG, "Couldn't start the scan service", e)
            }
        }

        fun finished(ctx: Context, id: String, title: String, ok: Boolean, seconds: Int) {
            channels(ctx)
            if (appVisible) return
            val took = if (seconds >= 60) "${seconds / 60} min ${seconds % 60} s" else "$seconds s"
            val text = if (ok) "Ready to play · read in $took" else "Couldn't read this one. Tap to see why."
            val n = Notification.Builder(ctx, CH_DONE)
                .setSmallIcon(R.drawable.ic_stat_note)
                .setContentTitle(title)
                .setContentText(text)
                .setAutoCancel(true)
                .setContentIntent(open(ctx, if (ok) "#/score/$id" else "#/"))
                .build()
            ctx.getSystemService(NotificationManager::class.java).notify(id.hashCode(), n)
        }

        fun idle() {
            stopRequested = true
            instance?.stopNow()
        }

        private fun progressNotification(ctx: Context, p: Progress): Notification {
            val more = if (p.queued > 0) " · ${p.queued} more waiting" else ""
            return Notification.Builder(ctx, CH_PROGRESS)
                .setSmallIcon(R.drawable.ic_stat_note)
                .setContentTitle(if (p.title.isNotEmpty()) "Scanning “${p.title}”" else "Scanning")
                .setContentText(p.message + more)
                .setProgress(100, p.percent.coerceIn(0, 100), p.percent < 5)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setCategory(Notification.CATEGORY_PROGRESS)
                .setContentIntent(open(ctx, "#/"))
                .apply { if (Build.VERSION.SDK_INT >= 31) setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE) }
                .build()
        }

        private fun open(ctx: Context, route: String): PendingIntent {
            val i = Intent(ctx, MainActivity::class.java)
                .setAction(Intent.ACTION_VIEW)
                .setData(Uri.parse("practiceroom://open/$route"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            return PendingIntent.getActivity(ctx, route.hashCode(), i,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        }

        private fun channels(ctx: Context) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CH_PROGRESS) == null) {
                nm.createNotificationChannel(NotificationChannel(CH_PROGRESS, "Scanning", NotificationManager.IMPORTANCE_LOW)
                    .apply { description = "Progress while sheet music is being read" })
                nm.createNotificationChannel(NotificationChannel(CH_DONE, "Scan finished", NotificationManager.IMPORTANCE_DEFAULT)
                    .apply { description = "When a scanned score is ready to play" })
            }
        }
    }
}
