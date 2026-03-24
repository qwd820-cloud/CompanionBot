package com.companionbot

import android.content.Context
import android.telephony.SmsManager
import android.util.Log

/**
 * 短信通知器 — 接收后端的短信指令，通过系统 API 静默发送
 *
 * Android SmsManager 可以无需用户确认直接发送短信，
 * 适用于紧急通知场景 (P0 级别)。
 */
class SmsNotifier(private val context: Context) {
    companion object {
        private const val TAG = "CompanionBot.SMS"
        private const val MAX_SMS_LENGTH = 160
    }

    fun sendSms(phone: String, message: String): Boolean {
        return try {
            val smsManager = context.getSystemService(SmsManager::class.java)

            if (message.length > MAX_SMS_LENGTH) {
                val parts = smsManager.divideMessage(message)
                smsManager.sendMultipartTextMessage(phone, null, parts, null, null)
                Log.i(TAG, "已发送长短信: $phone (${parts.size} 段)")
            } else {
                smsManager.sendTextMessage(phone, null, message, null, null)
                Log.i(TAG, "已发送短信: $phone")
            }
            true
        } catch (e: Exception) {
            Log.e(TAG, "短信发送失败: $phone", e)
            false
        }
    }
}
