package com.orzzzl.networth_app

import android.os.SystemClock
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodChannel
import java.io.File

/**
 * The one platform reading `DESIGN.md` §9.1 needs and Dart cannot take itself.
 *
 * This is deliberately **not** a pub.dev plugin. `AGENTS.md` admits no new
 * dependency without an explicit OK in the task spec and task `22` carries none;
 * what it needs is two values the platform already exposes, so the channel lives
 * in this app's own Android host and `pubspec.yaml` does not change.
 *
 * The Dart counterpart is `PlatformClockContinuitySource`, and the contract it
 * implements (`ClockContinuitySource`) has two obligations:
 *
 *  1. **The elapsed reading counts suspended time.** `SystemClock` exposes
 *     `elapsedRealtime()` beside `uptimeMillis()` precisely because the two
 *     differ across deep sleep, and only the first is documented to include it.
 *     Days a phone spent asleep still age the copy on it, so `uptimeMillis()`
 *     answers a different question than the one being asked.
 *  2. **The reading names its run.** `elapsedRealtime()` restarts at boot, so a
 *     reading larger than a stored one proves nothing by itself.
 *
 * **Nothing here is verified by CI**: the `app` job runs `flutter analyze` and
 * `flutter test`, neither of which compiles Kotlin. What pins this file is the
 * pair of literals its Dart counterpart's test reads out of it, plus the
 * device run recorded in that PR — not the test suite.
 */
object MonotonicClockChannel {
    /** Must match `monotonicClockChannel` in the Dart source, literally. */
    const val NAME: String = "com.orzzzl.networth_app/monotonic_clock"

    private const val METHOD_READ = "read"

    /**
     * The kernel's per-boot UUID — the run identity, and the whole reason this
     * channel can answer at all.
     *
     * **Measured on an API 36 device rather than assumed**, because an app does
     * not get to read most of `/proc`: from this app's own SELinux domain the
     * read succeeds and returns the same UUID the shell sees, and across a real
     * reboot it changed while `elapsedRealtime()` reset.
     *
     * Chosen over `Settings.Global.BOOT_COUNT`, the other candidate, on the one
     * property that matters here: **this cannot be read stale.** The kernel mints
     * it before anything runs — it was already present 6.7s into boot, at the
     * first moment the device answered at all, while the settings service was
     * still not serving `BOOT_COUNT`. A counter maintained by a system component
     * has, in principle, a window where it still reads as the *previous* boot's
     * value next to an `elapsedRealtime()` that has already reset: two different
     * runs claiming one identity, which is the single failure this field exists
     * to prevent and the only one whose direction is unsafe.
     *
     * A device that refuses this read gets `null`, and therefore
     * `COPY_UNKNOWN` — *"couldn't tell how old this is"* — for every copy. That
     * is the state the app already ships, it is visible on screen rather than
     * silent, and it is the honest answer for a device that will not say which
     * run it is in.
     */
    private const val BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"

    fun register(messenger: BinaryMessenger) {
        MethodChannel(messenger, NAME).setMethodCallHandler { call, result ->
            when (call.method) {
                METHOD_READ -> result.success(read())
                else -> result.notImplemented()
            }
        }
    }

    /**
     * One reading, or `null` when this device will not say which run it is in.
     *
     * The run id is read first so that a device which refuses it costs no clock
     * read at all. The order is not load bearing beyond that: the run cannot
     * change between the two lines, because ending it ends this process.
     */
    private fun read(): Map<String, Any>? {
        val bootId = readBootId() ?: return null
        return mapOf(
            // Prefixed with the scheme that produced it, so two ids from
            // different schemes can never compare equal. A build that changes
            // where the identity comes from makes every stored anchor mismatch,
            // which loses the evidence rather than claiming a run nothing proved.
            "runId" to "boot-id:$bootId",
            "elapsedMillis" to SystemClock.elapsedRealtime(),
        )
    }

    /**
     * `procfs` is in memory, so this is not the blocking disk read on the main
     * thread that reading a file from a channel handler usually is.
     */
    private fun readBootId(): String? =
        try {
            File(BOOT_ID_PATH).readText().trim().ifEmpty { null }
        } catch (error: Throwable) {
            // `Throwable` on purpose: the failures here are a SELinux denial
            // (`SecurityException`), an absent path (`FileNotFoundException`)
            // and a read error, and the caller's answer to all three is the
            // same. Catching narrowly would let an unlisted one escape into a
            // contract whose whole point is having an answer for everything.
            null
        }
}
