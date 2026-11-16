package chat.kelo.app.device

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.request.AggregateGroupByPeriodRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import java.time.Instant
import java.time.LocalDate
import java.time.Period
import java.time.ZoneId
import kotlin.reflect.KClass

/** Health Connect, read only: daily summaries of the kinds the user ticked (steps, sleep, heart rate, weight, exercise). */
object HealthReader {
    const val PROVIDER = "com.google.android.apps.healthdata"

    fun available(context: Context): Boolean =
        runCatching { HealthConnectClient.getSdkStatus(context, PROVIDER) == HealthConnectClient.SDK_AVAILABLE }.getOrDefault(false)

    /** Health Connect is not there but can be installed or updated from the store. */
    fun installable(context: Context): Boolean =
        runCatching { HealthConnectClient.getSdkStatus(context, PROVIDER) == HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED }.getOrDefault(false)

    private fun recordOf(type: String): KClass<out Record>? = when (type) {
        "steps" -> StepsRecord::class
        "sleep" -> SleepSessionRecord::class
        "heart_rate" -> HeartRateRecord::class
        "weight" -> WeightRecord::class
        "exercise" -> ExerciseSessionRecord::class
        else -> null
    }

    /** The read permissions for these kinds (what Kelo asks for: nothing more). */
    fun permissions(types: Set<String>): Set<String> = types.mapNotNull { recordOf(it) }.map { HealthPermission.getReadPermission(it) }.toSet()

    suspend fun granted(context: Context, types: Set<String>): Set<String> {
        if (!available(context)) return emptySet()
        val have = runCatching { HealthConnectClient.getOrCreate(context).permissionController.getGrantedPermissions() }.getOrDefault(emptySet())
        return types.filter { t -> recordOf(t)?.let { HealthPermission.getReadPermission(it) in have } == true }.toSet()
    }

    /** The last [days] days of the granted kinds, as raw readings for DeviceRules.healthDays. */
    suspend fun read(context: Context, types: Set<String>, zone: ZoneId, days: Int = DeviceRules.HEALTH_DAYS): HealthReadings {
        val client = HealthConnectClient.getOrCreate(context)
        val today = LocalDate.now(zone)
        val startDay = today.minusDays(days.toLong() - 1)
        val startLocal = startDay.atStartOfDay()
        val endLocal = today.plusDays(1).atStartOfDay()
        val start = startDay.atStartOfDay(zone).toInstant()
        val end = Instant.now()
        var r = HealthReadings()
        if ("steps" in types) {
            val groups = client.aggregateGroupByPeriod(
                AggregateGroupByPeriodRequest(setOf(StepsRecord.COUNT_TOTAL), TimeRangeFilter.between(startLocal, endLocal), Period.ofDays(1)),
            )
            r = r.copy(steps = groups.associate { g -> g.startTime.toLocalDate() to (g.result[StepsRecord.COUNT_TOTAL] ?: 0L) })
        }
        if ("heart_rate" in types) {
            val groups = client.aggregateGroupByPeriod(
                AggregateGroupByPeriodRequest(setOf(HeartRateRecord.BPM_MIN, HeartRateRecord.BPM_AVG, HeartRateRecord.BPM_MAX),
                    TimeRangeFilter.between(startLocal, endLocal), Period.ofDays(1)),
            )
            r = r.copy(heart = groups.mapNotNull { g ->
                val avg = g.result[HeartRateRecord.BPM_AVG] ?: return@mapNotNull null
                g.startTime.toLocalDate() to Triple((g.result[HeartRateRecord.BPM_MIN] ?: avg).toDouble(), avg.toDouble(), (g.result[HeartRateRecord.BPM_MAX] ?: avg).toDouble())
            }.toMap())
        }
        val range = TimeRangeFilter.between(start, end)
        if ("sleep" in types) r = r.copy(sleep = all(client, SleepSessionRecord::class, range).map { it.startTime.toEpochMilli() to it.endTime.toEpochMilli() })
        if ("weight" in types) r = r.copy(weight = all(client, WeightRecord::class, range).map { it.time.toEpochMilli() to it.weight.inKilograms })
        if ("exercise" in types) r = r.copy(exercise = all(client, ExerciseSessionRecord::class, range).map {
            ExerciseRow(exerciseName(it.exerciseType), it.title.orEmpty(), it.startTime.toEpochMilli(), it.endTime.toEpochMilli())
        })
        return r
    }

    private suspend fun <T : Record> all(client: HealthConnectClient, type: KClass<T>, range: TimeRangeFilter): List<T> {
        val out = mutableListOf<T>()
        var token: String? = null
        do {
            val page = client.readRecords(ReadRecordsRequest(type, range, pageSize = 1000, pageToken = token))
            out += page.records
            token = page.pageToken
        } while (token != null && out.size < 5000)
        return out
    }

    /** A few common kinds in plain words; the rest are "exercise" (the title, when there is one, says more). */
    private fun exerciseName(type: Int): String = when (type) {
        ExerciseSessionRecord.EXERCISE_TYPE_WALKING -> "walking"
        ExerciseSessionRecord.EXERCISE_TYPE_RUNNING, ExerciseSessionRecord.EXERCISE_TYPE_RUNNING_TREADMILL -> "running"
        ExerciseSessionRecord.EXERCISE_TYPE_BIKING, ExerciseSessionRecord.EXERCISE_TYPE_BIKING_STATIONARY -> "cycling"
        ExerciseSessionRecord.EXERCISE_TYPE_SWIMMING_POOL, ExerciseSessionRecord.EXERCISE_TYPE_SWIMMING_OPEN_WATER -> "swimming"
        ExerciseSessionRecord.EXERCISE_TYPE_HIKING -> "hiking"
        ExerciseSessionRecord.EXERCISE_TYPE_YOGA -> "yoga"
        ExerciseSessionRecord.EXERCISE_TYPE_STRENGTH_TRAINING, ExerciseSessionRecord.EXERCISE_TYPE_WEIGHTLIFTING -> "strength training"
        ExerciseSessionRecord.EXERCISE_TYPE_PILATES -> "pilates"
        ExerciseSessionRecord.EXERCISE_TYPE_HIGH_INTENSITY_INTERVAL_TRAINING -> "HIIT"
        else -> "exercise"
    }
}
