package com.jms1717.eightmblocal.history

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase

@Entity(tableName = "compression_history")
data class CompressionHistory(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val createdAt: Long,
    val outputUri: String,
    val targetMb: Double,
    val actualBytes: Long,
    val requestedMime: String,
    val actualEncoder: String,
    val hardwareUsed: Boolean,
    val fallbackOccurred: Boolean,
    val status: String,
)

@Dao
interface HistoryDao {
    @Insert suspend fun insert(item: CompressionHistory)

    @Query("SELECT * FROM compression_history ORDER BY createdAt DESC LIMIT :limit")
    suspend fun recent(limit: Int = 20): List<CompressionHistory>
}

@Database(entities = [CompressionHistory::class], version = 1, exportSchema = false)
abstract class HistoryDatabase : RoomDatabase() {
    abstract fun history(): HistoryDao

    companion object {
        @Volatile private var instance: HistoryDatabase? = null

        fun get(context: Context): HistoryDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                HistoryDatabase::class.java,
                "8mblocal-history.db",
            ).build().also { instance = it }
        }
    }
}
