package com.companionbot

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.companionbot.databinding.ActivitySettingsBinding

/**
 * 设置页面 — 服务器配置、Bot ID、成员管理入口、调试开关
 */
class SettingsActivity : AppCompatActivity() {
    companion object {
        const val PREFS_NAME = "companionbot_prefs"
        const val KEY_SERVER_URL = "server_url"
        const val KEY_CLIENT_ID = "client_id"
        const val KEY_BOT_ID = "bot_id"
        const val KEY_SHOW_CHAT = "show_chat"
        const val DEFAULT_SERVER = "ws://192.168.0.127:8765"
        const val DEFAULT_CLIENT_ID = "android_client_01"
        const val DEFAULT_BOT_ID = "default"
    }

    private lateinit var binding: ActivitySettingsBinding
    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        supportActionBar?.title = "设置"
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        loadSettings()

        binding.btnSave.setOnClickListener { saveSettings() }

        binding.btnEnroll.setOnClickListener {
            startActivity(Intent(this, FamilyActivity::class.java))
        }

        binding.btnCheckUpdate.setOnClickListener {
            val url = binding.etServerUrl.text.toString().trim().ifEmpty { DEFAULT_SERVER }
            AppUpdater(this).checkAndUpdate(url)
        }
    }

    private fun loadSettings() {
        binding.etServerUrl.setText(prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER))
        binding.etClientId.setText(prefs.getString(KEY_CLIENT_ID, DEFAULT_CLIENT_ID))
        binding.etBotId.setText(prefs.getString(KEY_BOT_ID, DEFAULT_BOT_ID))
        binding.switchShowChat.isChecked = prefs.getBoolean(KEY_SHOW_CHAT, false)
    }

    private fun saveSettings() {
        val url = binding.etServerUrl.text.toString().trim().ifEmpty { DEFAULT_SERVER }
        val clientId = binding.etClientId.text.toString().trim().ifEmpty { DEFAULT_CLIENT_ID }
        val botId = binding.etBotId.text.toString().trim().ifEmpty { DEFAULT_BOT_ID }

        prefs.edit()
            .putString(KEY_SERVER_URL, url)
            .putString(KEY_CLIENT_ID, clientId)
            .putString(KEY_BOT_ID, botId)
            .putBoolean(KEY_SHOW_CHAT, binding.switchShowChat.isChecked)
            .apply()

        Toast.makeText(this, "设置已保存", Toast.LENGTH_SHORT).show()
        setResult(RESULT_OK)
        finish()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
}
