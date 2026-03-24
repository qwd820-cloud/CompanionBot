package com.companionbot

import android.graphics.ImageFormat
import android.graphics.SurfaceTexture
import android.hardware.camera2.*
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.ImageReader
import android.media.MediaRecorder
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import android.view.TextureView
import android.view.View
import android.widget.RadioGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.gson.JsonObject
import kotlinx.coroutines.*

/**
 * 家庭成员注册引导界面
 *
 * 三步引导:
 *   第 1 步: 填写基本信息 (姓名、称呼、年龄、角色)
 *   第 2 步: 录制 3~5 段声纹 (每段 5~10 秒自然说话)
 *   第 3 步: 拍摄 5~10 张人脸照片 (不同角度)
 *
 * 数据通过 WebSocket 发送到后端完成注册。
 */
class EnrollActivity : AppCompatActivity(), WebSocketClient.WebSocketListener {
    companion object {
        private const val TAG = "CompanionBot.Enroll"
        const val EXTRA_SERVER_URL = "server_url"
        const val EXTRA_CLIENT_ID = "client_id"

        private const val MIN_VOICE_SAMPLES = 3
        private const val MAX_VOICE_SAMPLES = 5
        private const val MIN_PHOTOS = 5
        private const val MAX_PHOTOS = 10

        private const val SAMPLE_RATE = 16000
    }

    // UI 通过 ViewBinding 风格手动 findViewById
    private lateinit var tvStep: android.widget.TextView
    private lateinit var layoutStep1: View
    private lateinit var layoutStep2: View
    private lateinit var layoutStep3: View
    private lateinit var etName: android.widget.EditText
    private lateinit var etNickname: android.widget.EditText
    private lateinit var etAge: android.widget.EditText
    private lateinit var etRelationship: android.widget.EditText
    private lateinit var rgRole: RadioGroup
    private lateinit var tvVoiceProgress: android.widget.TextView
    private lateinit var btnRecord: android.widget.Button
    private lateinit var tvRecordHint: android.widget.TextView
    private lateinit var tvPhotoProgress: android.widget.TextView
    private lateinit var texturePreview: TextureView
    private lateinit var btnCapture: android.widget.Button
    private lateinit var tvEnrollStatus: android.widget.TextView
    private lateinit var btnPrevStep: android.widget.Button
    private lateinit var btnNextStep: android.widget.Button

    private lateinit var wsClient: WebSocketClient
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    private var currentStep = 1
    private var personId = ""

    // 声纹录制
    private val voiceSamples = mutableListOf<ByteArray>()
    private var audioRecord: AudioRecord? = null
    private var isRecording = false
    private var recordingJob: Job? = null

    // 人脸采集
    private val photos = mutableListOf<ByteArray>()
    private var cameraDevice: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null
    private var bgThread: HandlerThread? = null
    private var bgHandler: Handler? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_enroll)
        title = "注册家庭成员"

        initViews()
        setupListeners()

        wsClient = WebSocketClient(this)
        val serverUrl = intent.getStringExtra(EXTRA_SERVER_URL) ?: "ws://192.168.1.100:8765"
        val clientId = intent.getStringExtra(EXTRA_CLIENT_ID) ?: "android_enroll"
        wsClient.connect(serverUrl, clientId)

        showStep(1)
    }

    private fun initViews() {
        tvStep = findViewById(R.id.tvStep)
        layoutStep1 = findViewById(R.id.layoutStep1)
        layoutStep2 = findViewById(R.id.layoutStep2)
        layoutStep3 = findViewById(R.id.layoutStep3)
        etName = findViewById(R.id.etName)
        etNickname = findViewById(R.id.etNickname)
        etAge = findViewById(R.id.etAge)
        etRelationship = findViewById(R.id.etRelationship)
        rgRole = findViewById(R.id.rgRole)
        tvVoiceProgress = findViewById(R.id.tvVoiceProgress)
        btnRecord = findViewById(R.id.btnRecord)
        tvRecordHint = findViewById(R.id.tvRecordHint)
        tvPhotoProgress = findViewById(R.id.tvPhotoProgress)
        texturePreview = findViewById(R.id.texturePreview)
        btnCapture = findViewById(R.id.btnCapture)
        tvEnrollStatus = findViewById(R.id.tvEnrollStatus)
        btnPrevStep = findViewById(R.id.btnPrevStep)
        btnNextStep = findViewById(R.id.btnNextStep)
    }

    private fun setupListeners() {
        btnNextStep.setOnClickListener { onNextStep() }
        btnPrevStep.setOnClickListener { onPrevStep() }

        // 录音按钮: 点击开始/停止
        btnRecord.setOnClickListener {
            if (isRecording) {
                stopVoiceRecording()
            } else {
                if (voiceSamples.size < MAX_VOICE_SAMPLES) {
                    startVoiceRecording()
                } else {
                    Toast.makeText(this, "已录满 $MAX_VOICE_SAMPLES 段", Toast.LENGTH_SHORT).show()
                }
            }
        }

        // 拍照按钮
        btnCapture.setOnClickListener {
            if (photos.size < MAX_PHOTOS) {
                capturePhoto()
            } else {
                Toast.makeText(this, "已拍满 $MAX_PHOTOS 张", Toast.LENGTH_SHORT).show()
            }
        }
    }

    // ========== 步骤导航 ==========

    private fun showStep(step: Int) {
        currentStep = step
        layoutStep1.visibility = if (step == 1) View.VISIBLE else View.GONE
        layoutStep2.visibility = if (step == 2) View.VISIBLE else View.GONE
        layoutStep3.visibility = if (step == 3) View.VISIBLE else View.GONE
        btnPrevStep.visibility = if (step > 1) View.VISIBLE else View.GONE
        tvEnrollStatus.text = ""

        val stepNames = arrayOf("填写信息", "录制声纹", "拍摄照片")
        tvStep.text = "第 $step 步 / 共 3 步: ${stepNames[step - 1]}"

        btnNextStep.text = if (step == 3) "完成注册" else "下一步"

        if (step == 3) startCameraPreview()
        if (step != 3) stopCameraPreview()
    }

    private fun onNextStep() {
        when (currentStep) {
            1 -> {
                val name = etName.text.toString().trim()
                if (name.isEmpty()) {
                    Toast.makeText(this, "请输入姓名", Toast.LENGTH_SHORT).show()
                    return
                }
                personId = name.replace(" ", "_").lowercase()
                showStep(2)
            }
            2 -> {
                if (voiceSamples.size < MIN_VOICE_SAMPLES) {
                    Toast.makeText(this, "至少录制 $MIN_VOICE_SAMPLES 段声纹", Toast.LENGTH_SHORT).show()
                    return
                }
                showStep(3)
            }
            3 -> {
                if (photos.size < MIN_PHOTOS) {
                    Toast.makeText(this, "至少拍摄 $MIN_PHOTOS 张照片", Toast.LENGTH_SHORT).show()
                    return
                }
                submitEnrollment()
            }
        }
    }

    private fun onPrevStep() {
        if (currentStep > 1) showStep(currentStep - 1)
    }

    // ========== 第 2 步: 声纹录制 ==========

    private fun startVoiceRecording() {
        val bufferSize = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize * 2
        )

        if (audioRecord?.state != AudioRecord.STATE_INITIALIZED) {
            Toast.makeText(this, "麦克风初始化失败", Toast.LENGTH_SHORT).show()
            return
        }

        isRecording = true
        audioRecord?.startRecording()
        btnRecord.text = "停止录音"
        btnRecord.setBackgroundColor(0xFFF44336.toInt())
        tvRecordHint.text = "正在录音... 请自然说话 5~10 秒"

        recordingJob = scope.launch(Dispatchers.IO) {
            val allBytes = mutableListOf<Byte>()
            val buffer = ByteArray(bufferSize)
            val startTime = System.currentTimeMillis()

            while (isRecording) {
                val bytesRead = audioRecord?.read(buffer, 0, buffer.size) ?: -1
                if (bytesRead > 0) {
                    allBytes.addAll(buffer.take(bytesRead))
                }
                // 自动停止: 超过 10 秒
                if (System.currentTimeMillis() - startTime > 10_000) {
                    withContext(Dispatchers.Main) { stopVoiceRecording() }
                    return@launch
                }
            }

            val audioData = allBytes.toByteArray()
            val durationMs = (audioData.size.toFloat() / (SAMPLE_RATE * 2) * 1000).toLong()

            if (durationMs < 3000) {
                withContext(Dispatchers.Main) {
                    tvRecordHint.text = "录音太短 (${durationMs / 1000}秒)，至少 3 秒，请重录"
                }
                return@launch
            }

            withContext(Dispatchers.Main) {
                voiceSamples.add(audioData)
                updateVoiceProgress()
                tvRecordHint.text = "第 ${voiceSamples.size} 段录制完成 (${durationMs / 1000}秒)"
            }
        }
    }

    private fun stopVoiceRecording() {
        isRecording = false
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null
        btnRecord.text = "开始录音"
        btnRecord.setBackgroundColor(0xFF4CAF50.toInt())
    }

    private fun updateVoiceProgress() {
        tvVoiceProgress.text = "已录制: ${voiceSamples.size} / $MIN_VOICE_SAMPLES 段"
    }

    // ========== 第 3 步: 人脸采集 ==========

    private fun startCameraPreview() {
        bgThread = HandlerThread("EnrollCamera").also { it.start() }
        bgHandler = Handler(bgThread!!.looper)

        if (texturePreview.isAvailable) {
            openCameraForPreview()
        } else {
            texturePreview.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                override fun onSurfaceTextureAvailable(st: SurfaceTexture, w: Int, h: Int) {
                    openCameraForPreview()
                }
                override fun onSurfaceTextureSizeChanged(st: SurfaceTexture, w: Int, h: Int) {}
                override fun onSurfaceTextureDestroyed(st: SurfaceTexture) = true
                override fun onSurfaceTextureUpdated(st: SurfaceTexture) {}
            }
        }
    }

    private fun openCameraForPreview() {
        val manager = getSystemService(CAMERA_SERVICE) as android.hardware.camera2.CameraManager
        try {
            // 优先前置摄像头
            val cameraId = manager.cameraIdList.firstOrNull { id ->
                val chars = manager.getCameraCharacteristics(id)
                chars.get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_FRONT
            } ?: manager.cameraIdList[0]

            imageReader = ImageReader.newInstance(640, 480, ImageFormat.JPEG, 2)

            manager.openCamera(cameraId, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    cameraDevice = camera
                    createPreviewSession()
                }
                override fun onDisconnected(camera: CameraDevice) { camera.close() }
                override fun onError(camera: CameraDevice, error: Int) {
                    camera.close()
                    Log.e(TAG, "摄像头错误: $error")
                }
            }, bgHandler)
        } catch (e: SecurityException) {
            Log.e(TAG, "摄像头权限不足", e)
            Toast.makeText(this, "需要摄像头权限", Toast.LENGTH_SHORT).show()
        }
    }

    private fun createPreviewSession() {
        val camera = cameraDevice ?: return
        val reader = imageReader ?: return
        val texture = texturePreview.surfaceTexture ?: return
        texture.setDefaultBufferSize(640, 480)
        val previewSurface = Surface(texture)

        val captureRequest = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
            addTarget(previewSurface)
            set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
        }

        camera.createCaptureSession(
            listOf(previewSurface, reader.surface),
            object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(session: CameraCaptureSession) {
                    captureSession = session
                    session.setRepeatingRequest(captureRequest.build(), null, bgHandler)
                }
                override fun onConfigureFailed(session: CameraCaptureSession) {
                    Log.e(TAG, "预览会话配置失败")
                }
            },
            bgHandler
        )
    }

    private fun capturePhoto() {
        val camera = cameraDevice ?: return
        val reader = imageReader ?: return

        reader.setOnImageAvailableListener({ imgReader ->
            val image = imgReader.acquireLatestImage() ?: return@setOnImageAvailableListener
            try {
                val buffer = image.planes[0].buffer
                val jpegData = ByteArray(buffer.remaining())
                buffer.get(jpegData)

                runOnUiThread {
                    photos.add(jpegData)
                    updatePhotoProgress()

                    val hints = arrayOf("正面", "左转头", "右转头", "微笑", "稍抬头",
                        "稍低头", "换个表情", "侧身", "再来一张", "完美")
                    val hint = if (photos.size < hints.size) hints[photos.size] else "再来一张"
                    findViewById<android.widget.TextView>(R.id.tvPhotoHint).text =
                        "下一张建议: $hint"
                }
            } finally {
                image.close()
            }
            // 拍完一张就移除监听，等下次点击
            reader.setOnImageAvailableListener(null, null)
        }, bgHandler)

        // 触发单次拍照
        val captureBuilder = camera.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
            addTarget(reader.surface)
            set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
            set(CaptureRequest.JPEG_QUALITY, 85.toByte())
        }
        captureSession?.capture(captureBuilder.build(), null, bgHandler)
    }

    private fun updatePhotoProgress() {
        tvPhotoProgress.text = "已拍摄: ${photos.size} / $MIN_PHOTOS 张"
    }

    private fun stopCameraPreview() {
        captureSession?.close()
        captureSession = null
        cameraDevice?.close()
        cameraDevice = null
        imageReader?.close()
        imageReader = null
        bgThread?.quitSafely()
        bgThread = null
    }

    // ========== 提交注册 ==========

    private fun submitEnrollment() {
        if (!wsClient.isConnected) {
            tvEnrollStatus.text = "未连接到服务器，无法提交"
            return
        }

        val name = etName.text.toString().trim()
        val nickname = etNickname.text.toString().trim().ifEmpty { name }
        val age = etAge.text.toString().trim().toIntOrNull() ?: 0
        val relationship = etRelationship.text.toString().trim()
        val role = when (rgRole.checkedRadioButtonId) {
            R.id.rbElder -> "elder"
            R.id.rbChild -> "child"
            else -> "adult"
        }

        btnNextStep.isEnabled = false
        tvEnrollStatus.text = "正在提交注册信息..."

        // 依次发送: 档案 → 声纹 → 人脸
        wsClient.sendEnrollProfile(personId, name, nickname, role, age, relationship)
        tvEnrollStatus.text = "正在上传声纹数据 (${voiceSamples.size} 段)..."
        wsClient.sendEnrollVoice(personId, voiceSamples)
        tvEnrollStatus.text = "正在上传人脸数据 (${photos.size} 张)..."
        wsClient.sendEnrollFace(personId, photos)
        tvEnrollStatus.text = "注册数据已提交，等待后端处理..."
    }

    // ========== WebSocket callbacks ==========

    override fun onConnected() {
        runOnUiThread {
            tvEnrollStatus.text = "已连接到服务器"
        }
    }

    override fun onDisconnected(reason: String) {
        runOnUiThread {
            tvEnrollStatus.text = "与服务器断开: $reason"
        }
    }

    override fun onJsonMessage(json: JsonObject) {
        val type = json.get("type")?.asString ?: return
        when (type) {
            "enroll_result" -> {
                val success = json.get("success")?.asBoolean ?: false
                val message = json.get("message")?.asString ?: ""
                runOnUiThread {
                    if (success) {
                        tvEnrollStatus.text = "注册成功! $message"
                        Toast.makeText(this, "家庭成员注册成功!", Toast.LENGTH_LONG).show()
                        // 延迟关闭页面
                        scope.launch {
                            delay(1500)
                            setResult(RESULT_OK)
                            finish()
                        }
                    } else {
                        tvEnrollStatus.text = "注册失败: $message"
                        btnNextStep.isEnabled = true
                    }
                }
            }
        }
    }

    override fun onBinaryMessage(type: Byte, data: ByteArray) {}

    override fun onError(message: String) {
        runOnUiThread {
            tvEnrollStatus.text = "错误: $message"
            btnNextStep.isEnabled = true
        }
    }

    // ========== Lifecycle ==========

    override fun onDestroy() {
        stopVoiceRecording()
        stopCameraPreview()
        wsClient.disconnect()
        scope.cancel()
        super.onDestroy()
    }
}
