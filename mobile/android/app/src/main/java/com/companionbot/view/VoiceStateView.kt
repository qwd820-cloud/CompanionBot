package com.companionbot.view

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.*
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator
import com.companionbot.ConversationState
import com.companionbot.R
import kotlin.math.*

/**
 * 语音状态动画视图 — 根据对话状态显示不同动画
 *
 * DISCONNECTED:  灰色静态圆 + 断开线
 * CONNECTING:    旋转弧线
 * LISTENING:     脉冲涟漪（3层同心圆）
 * PROCESSING:    3个弹跳圆点
 * SPEAKING:      音频波形柱
 * RESUMING:      波形柱渐弱 → 涟漪
 */
class VoiceStateView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private var currentState = ConversationState.DISCONNECTED
    private var animationFraction = 0f
    private var emotionColor = Color.parseColor("#5C9CE6") // neutral

    private val animator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 2000
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener {
            animationFraction = it.animatedFraction
            invalidate()
        }
    }

    // Paints
    private val circlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3f
    }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        maskFilter = BlurMaskFilter(40f, BlurMaskFilter.Blur.NORMAL)
    }
    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        strokeCap = Paint.Cap.ROUND
    }
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 4f
        color = Color.parseColor("#666666")
    }

    init {
        // 启用软件渲染以支持 BlurMaskFilter
        setLayerType(LAYER_TYPE_SOFTWARE, null)
    }

    fun setState(state: ConversationState) {
        if (currentState == state) return
        currentState = state

        when (state) {
            ConversationState.DISCONNECTED -> {
                animator.cancel()
                invalidate()
            }
            else -> {
                if (!animator.isRunning) animator.start()
            }
        }
    }

    fun setEmotionColor(emotion: String) {
        emotionColor = when (emotion) {
            "happy" -> Color.parseColor("#FFD54F")
            "concerned" -> Color.parseColor("#FF9800")
            "tired" -> Color.parseColor("#9575CD")
            "curious" -> Color.parseColor("#4DB6AC")
            "slightly_annoyed" -> Color.parseColor("#EF9A9A")
            else -> Color.parseColor("#5C9CE6") // neutral
        }
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val cx = width / 2f
        val cy = height / 2f
        val baseRadius = min(width, height) / 5f

        when (currentState) {
            ConversationState.DISCONNECTED -> drawDisconnected(canvas, cx, cy, baseRadius)
            ConversationState.CONNECTING -> drawConnecting(canvas, cx, cy, baseRadius)
            ConversationState.LISTENING -> drawListening(canvas, cx, cy, baseRadius)
            ConversationState.PROCESSING -> drawProcessing(canvas, cx, cy, baseRadius)
            ConversationState.SPEAKING -> drawSpeaking(canvas, cx, cy, baseRadius)
            ConversationState.RESUMING -> drawListening(canvas, cx, cy, baseRadius) // 渐变回听
        }
    }

    private fun drawDisconnected(canvas: Canvas, cx: Float, cy: Float, r: Float) {
        // 灰色圆
        circlePaint.color = Color.parseColor("#444444")
        circlePaint.strokeWidth = 4f
        canvas.drawCircle(cx, cy, r, circlePaint)

        // 斜线（断开标志）
        val offset = r * 0.6f
        linePaint.color = Color.parseColor("#666666")
        canvas.drawLine(cx - offset, cy - offset, cx + offset, cy + offset, linePaint)
    }

    private fun drawConnecting(canvas: Canvas, cx: Float, cy: Float, r: Float) {
        // 旋转弧线
        val sweepAngle = 90f
        val startAngle = animationFraction * 360f

        circlePaint.color = emotionColor
        circlePaint.strokeWidth = 5f
        val rect = RectF(cx - r, cy - r, cx + r, cy + r)
        canvas.drawArc(rect, startAngle, sweepAngle, false, circlePaint)

        // 第二段弧
        circlePaint.alpha = 100
        canvas.drawArc(rect, startAngle + 180f, sweepAngle, false, circlePaint)
        circlePaint.alpha = 255
    }

    private fun drawListening(canvas: Canvas, cx: Float, cy: Float, r: Float) {
        val f = animationFraction * 2 * PI

        // 中心发光
        glowPaint.color = emotionColor
        glowPaint.alpha = 40
        canvas.drawCircle(cx, cy, r * 0.6f, glowPaint)

        // 中心实心圆
        fillPaint.color = emotionColor
        fillPaint.alpha = 180
        val pulseScale = 1f + 0.08f * sin(f).toFloat()
        canvas.drawCircle(cx, cy, r * 0.35f * pulseScale, fillPaint)

        // 3 层涟漪圆
        for (i in 0..2) {
            val phase = f + i * 2.094 // 2π/3 间隔
            val scale = 0.6f + 0.5f * ((sin(phase).toFloat() + 1f) / 2f)
            val alpha = (180 * (1f - scale / 1.1f)).toInt().coerceIn(0, 255)

            circlePaint.color = emotionColor
            circlePaint.alpha = alpha
            circlePaint.strokeWidth = 2.5f
            canvas.drawCircle(cx, cy, r * scale, circlePaint)
        }
    }

    private fun drawProcessing(canvas: Canvas, cx: Float, cy: Float, r: Float) {
        val f = animationFraction * 2 * PI
        val dotRadius = r * 0.12f
        val spacing = r * 0.5f

        for (i in 0..2) {
            val phase = f + i * 0.7
            val yOffset = -r * 0.2f * sin(phase).toFloat()
            val alpha = (200 + 55 * sin(phase)).toInt().coerceIn(0, 255)

            fillPaint.color = emotionColor
            fillPaint.alpha = alpha
            val dotX = cx + (i - 1) * spacing
            canvas.drawCircle(dotX, cy + yOffset, dotRadius, fillPaint)
        }
    }

    private fun drawSpeaking(canvas: Canvas, cx: Float, cy: Float, r: Float) {
        val f = animationFraction * 2 * PI
        val barCount = 7
        val barWidth = r * 0.14f
        val totalWidth = barCount * barWidth + (barCount - 1) * barWidth * 0.5f
        val startX = cx - totalWidth / 2f
        val maxHeight = r * 1.4f

        barPaint.color = emotionColor

        for (i in 0 until barCount) {
            val phase = f + i * 0.8
            // 使用多个正弦叠加让波形更有机感
            val heightFactor = 0.3f + 0.35f * sin(phase).toFloat() + 0.2f * sin(phase * 1.7 + 0.5).toFloat()
            val barHeight = maxHeight * heightFactor.coerceIn(0.15f, 0.85f)
            val x = startX + i * (barWidth * 1.5f)

            barPaint.alpha = (180 + 75 * sin(phase + 0.3)).toInt().coerceIn(0, 255)
            val rect = RectF(x, cy - barHeight / 2, x + barWidth, cy + barHeight / 2)
            canvas.drawRoundRect(rect, barWidth / 2, barWidth / 2, barPaint)
        }
    }

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        animator.cancel()
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (currentState != ConversationState.DISCONNECTED) {
            animator.start()
        }
    }
}
