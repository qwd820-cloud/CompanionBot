package com.companionbot

import android.app.AlertDialog
import android.app.ProgressDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import android.widget.Toast
import androidx.core.content.FileProvider
import kotlinx.coroutines.*
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit

/**
 * App 自更新 — 检查版本、下载 APK、安装
 */
class AppUpdater(private val context: Context) {
    companion object {
        private const val TAG = "CompanionBot.Updater"
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    fun checkAndUpdate(serverUrl: String) {
        scope.launch {
            try {
                val baseUrl = serverUrl.replace("ws://", "http://").replace("wss://", "https://")
                    .trimEnd('/')
                Toast.makeText(context, "正在检查更新...", Toast.LENGTH_SHORT).show()

                val versionInfo = withContext(Dispatchers.IO) { fetchVersion(baseUrl) }
                if (versionInfo == null) {
                    Toast.makeText(context, "检查更新失败", Toast.LENGTH_SHORT).show()
                    return@launch
                }

                val localCode = try {
                    context.packageManager.getPackageInfo(context.packageName, 0).longVersionCode.toInt()
                } catch (e: Exception) { 1 }

                val remoteCode = versionInfo.optInt("version_code", 0)
                val remoteVersion = versionInfo.optString("version", "")
                val changelog = versionInfo.optString("changelog", "")

                if (remoteCode <= localCode) {
                    Toast.makeText(context, "已是最新版本 (v${remoteVersion})", Toast.LENGTH_SHORT).show()
                    return@launch
                }

                // 有新版本
                AlertDialog.Builder(context)
                    .setTitle("发现新版本 v$remoteVersion")
                    .setMessage(changelog.ifEmpty { "有新版本可用" })
                    .setPositiveButton("更新") { _, _ ->
                        downloadAndInstall("$baseUrl/api/app/download")
                    }
                    .setNegativeButton("稍后", null)
                    .show()

            } catch (e: Exception) {
                Log.e(TAG, "检查更新异常", e)
                Toast.makeText(context, "检查更新失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun fetchVersion(baseUrl: String): JSONObject? {
        val request = Request.Builder().url("$baseUrl/api/app/version").build()
        val response = client.newCall(request).execute()
        if (!response.isSuccessful) return null
        val body = response.body?.string() ?: return null
        return JSONObject(body)
    }

    @Suppress("DEPRECATION")
    private fun downloadAndInstall(url: String) {
        val progressDialog = ProgressDialog(context).apply {
            setTitle("下载更新")
            setMessage("正在下载...")
            setProgressStyle(ProgressDialog.STYLE_HORIZONTAL)
            max = 100
            setCancelable(false)
            show()
        }

        scope.launch {
            try {
                val file = withContext(Dispatchers.IO) {
                    downloadApk(url) { progress ->
                        launch(Dispatchers.Main) {
                            progressDialog.progress = progress
                        }
                    }
                }
                progressDialog.dismiss()

                if (file != null) {
                    installApk(file)
                } else {
                    Toast.makeText(context, "下载失败", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                progressDialog.dismiss()
                Log.e(TAG, "下载失败", e)
                Toast.makeText(context, "下载失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun downloadApk(url: String, onProgress: (Int) -> Unit): File? {
        val request = Request.Builder().url(url).build()
        val response = client.newCall(request).execute()
        if (!response.isSuccessful) return null

        val body = response.body ?: return null
        val totalBytes = body.contentLength()
        val file = File(context.getExternalFilesDir(null), "update.apk")

        FileOutputStream(file).use { output ->
            body.byteStream().use { input ->
                val buffer = ByteArray(8192)
                var downloaded = 0L
                var read: Int
                while (input.read(buffer).also { read = it } != -1) {
                    output.write(buffer, 0, read)
                    downloaded += read
                    if (totalBytes > 0) {
                        onProgress((downloaded * 100 / totalBytes).toInt())
                    }
                }
            }
        }

        return file
    }

    private fun installApk(file: File) {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }
}
