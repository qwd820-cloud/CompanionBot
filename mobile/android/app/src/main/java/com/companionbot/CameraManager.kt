package com.companionbot

import android.content.Context
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.hardware.camera2.*
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import java.io.ByteArrayOutputStream

/**
 * 摄像头帧采集 — 定期抓取 JPEG 帧通过 WebSocket 发送
 *
 * 使用 Camera2 API 获取低延迟视频帧。
 * 默认 2fps 以平衡带宽和识别效果。
 */
class CameraFrameCapture(private val context: Context) {
    companion object {
        private const val TAG = "CompanionBot.Camera"
        private const val TARGET_FPS = 2
        private const val JPEG_QUALITY = 70
        private const val IMAGE_WIDTH = 640
        private const val IMAGE_HEIGHT = 480
    }

    private var cameraDevice: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null
    private var backgroundThread: HandlerThread? = null
    private var backgroundHandler: Handler? = null
    private var isCapturing = false
    private var lastFrameTime = 0L
    private val frameIntervalMs = 1000L / TARGET_FPS

    var onFrameCapture: ((ByteArray) -> Unit)? = null

    fun startCapture() {
        if (isCapturing) return
        isCapturing = true

        backgroundThread = HandlerThread("CameraBackground").also { it.start() }
        backgroundHandler = Handler(backgroundThread!!.looper)

        imageReader = ImageReader.newInstance(
            IMAGE_WIDTH, IMAGE_HEIGHT,
            ImageFormat.JPEG, 2
        )
        imageReader?.setOnImageAvailableListener({ reader ->
            val now = System.currentTimeMillis()
            if (now - lastFrameTime < frameIntervalMs) {
                reader.acquireLatestImage()?.close()
                return@setOnImageAvailableListener
            }
            lastFrameTime = now

            val image = reader.acquireLatestImage() ?: return@setOnImageAvailableListener
            try {
                val buffer = image.planes[0].buffer
                val jpegData = ByteArray(buffer.remaining())
                buffer.get(jpegData)
                onFrameCapture?.invoke(jpegData)
            } finally {
                image.close()
            }
        }, backgroundHandler)

        openCamera()
    }

    fun stopCapture() {
        isCapturing = false
        captureSession?.close()
        cameraDevice?.close()
        imageReader?.close()
        backgroundThread?.quitSafely()
        Log.i(TAG, "摄像头停止")
    }

    private fun openCamera() {
        val manager = context.getSystemService(Context.CAMERA_SERVICE) as android.hardware.camera2.CameraManager
        try {
            val cameraId = manager.cameraIdList.firstOrNull { id ->
                val characteristics = manager.getCameraCharacteristics(id)
                characteristics.get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_FRONT
            } ?: manager.cameraIdList[0]

            manager.openCamera(cameraId, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    cameraDevice = camera
                    createCaptureSession()
                    Log.i(TAG, "摄像头已打开: $cameraId")
                }

                override fun onDisconnected(camera: CameraDevice) {
                    camera.close()
                }

                override fun onError(camera: CameraDevice, error: Int) {
                    camera.close()
                    Log.e(TAG, "摄像头错误: $error")
                }
            }, backgroundHandler)
        } catch (e: SecurityException) {
            Log.e(TAG, "摄像头权限不足", e)
        }
    }

    private fun createCaptureSession() {
        val camera = cameraDevice ?: return
        val reader = imageReader ?: return

        val surface = reader.surface
        val captureRequest = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
            addTarget(surface)
            set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
        }

        camera.createCaptureSession(
            listOf(surface),
            object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(session: CameraCaptureSession) {
                    captureSession = session
                    session.setRepeatingRequest(captureRequest.build(), null, backgroundHandler)
                    Log.i(TAG, "摄像头采集开始: ${TARGET_FPS}fps")
                }

                override fun onConfigureFailed(session: CameraCaptureSession) {
                    Log.e(TAG, "摄像头会话配置失败")
                }
            },
            backgroundHandler
        )
    }
}
