package com.companionbot

import android.content.Context
import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.DividerItemDecoration
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.gson.JsonObject
import java.text.SimpleDateFormat
import java.util.*

/**
 * 家庭成员详情 — 档案查看/编辑 + 沟通记录
 */
class MemberDetailActivity : AppCompatActivity(), WebSocketClient.WebSocketListener {
    companion object {
        private const val TAG = "CompanionBot.Detail"
    }

    private lateinit var etName: EditText
    private lateinit var etNickname: EditText
    private lateinit var etAge: EditText
    private lateinit var etRelationship: EditText
    private lateinit var tvRole: TextView
    private lateinit var btnEdit: Button
    private lateinit var btnSave: Button
    private lateinit var tvNoEpisodes: TextView
    private lateinit var rvEpisodes: RecyclerView
    private lateinit var btnDelete: Button
    private lateinit var wsClient: WebSocketClient

    private var personId = ""
    private var currentRole = "adult"
    private var isEditing = false
    private val episodes = mutableListOf<EpisodeItem>()
    private lateinit var adapter: EpisodeAdapter

    data class EpisodeItem(val summary: String, val emotionTag: String, val timestamp: Double)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_member_detail)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        personId = intent.getStringExtra("person_id") ?: run { finish(); return }

        etName = findViewById(R.id.etName)
        etNickname = findViewById(R.id.etNickname)
        etAge = findViewById(R.id.etAge)
        etRelationship = findViewById(R.id.etRelationship)
        tvRole = findViewById(R.id.tvRole)
        btnEdit = findViewById(R.id.btnEdit)
        btnSave = findViewById(R.id.btnSave)
        tvNoEpisodes = findViewById(R.id.tvNoEpisodes)
        rvEpisodes = findViewById(R.id.rvEpisodes)
        btnDelete = findViewById(R.id.btnDelete)

        adapter = EpisodeAdapter(episodes)
        rvEpisodes.layoutManager = LinearLayoutManager(this)
        rvEpisodes.addItemDecoration(DividerItemDecoration(this, DividerItemDecoration.VERTICAL))
        rvEpisodes.adapter = adapter

        btnEdit.setOnClickListener { setEditMode(true) }
        btnSave.setOnClickListener { saveProfile() }
        btnDelete.setOnClickListener { confirmDelete() }

        wsClient = WebSocketClient(this)
        val prefs = getSharedPreferences(SettingsActivity.PREFS_NAME, Context.MODE_PRIVATE)
        val url = prefs.getString(SettingsActivity.KEY_SERVER_URL, SettingsActivity.DEFAULT_SERVER)!!
        val botId = prefs.getString(SettingsActivity.KEY_BOT_ID, SettingsActivity.DEFAULT_BOT_ID)!!
        wsClient.connect(url, botId, "android_detail_$personId")
    }

    private fun setEditMode(editing: Boolean) {
        isEditing = editing
        etName.isEnabled = editing
        etNickname.isEnabled = editing
        etAge.isEnabled = editing
        etRelationship.isEnabled = editing
        btnEdit.visibility = if (editing) View.GONE else View.VISIBLE
        btnSave.visibility = if (editing) View.VISIBLE else View.GONE
    }

    private fun saveProfile() {
        val name = etName.text.toString().trim()
        if (name.isEmpty()) {
            Toast.makeText(this, "姓名不能为空", Toast.LENGTH_SHORT).show()
            return
        }
        val age = etAge.text.toString().trim().toIntOrNull() ?: 0
        wsClient.requestUpdateMember(
            personId = personId,
            name = name,
            nickname = etNickname.text.toString().trim(),
            role = currentRole,
            age = age,
            relationship = etRelationship.text.toString().trim()
        )
    }

    private fun confirmDelete() {
        AlertDialog.Builder(this)
            .setTitle("删除成员")
            .setMessage("确定删除？将同时删除所有沟通记录。")
            .setPositiveButton("删除") { _, _ -> wsClient.requestDeleteMember(personId) }
            .setNegativeButton("取消", null)
            .show()
    }

    // ========== WebSocket callbacks ==========

    override fun onConnected() { wsClient.requestMemberDetail(personId) }
    override fun onDisconnected(reason: String) {}

    override fun onJsonMessage(json: JsonObject) {
        val type = json.get("type")?.asString ?: return
        when (type) {
            "member_detail" -> {
                val profile = json.getAsJsonObject("profile") ?: return
                runOnUiThread {
                    val name = profile.get("name")?.asString ?: personId
                    supportActionBar?.title = name
                    etName.setText(name)
                    etNickname.setText(profile.get("nickname")?.asString ?: "")
                    etAge.setText(profile.get("age")?.asInt?.let { if (it > 0) it.toString() else "" } ?: "")
                    etRelationship.setText(profile.get("relationship")?.asString ?: "")
                    currentRole = profile.get("role")?.asString ?: "adult"
                    tvRole.text = when (currentRole) {
                        "elder" -> "老人"; "child" -> "小孩"; else -> "成人"
                    }
                }

                episodes.clear()
                val arr = json.getAsJsonArray("episodes")
                if (arr != null) {
                    for (el in arr) {
                        val obj = el.asJsonObject
                        episodes.add(EpisodeItem(
                            summary = obj.get("summary")?.asString ?: "",
                            emotionTag = obj.get("emotion_tag")?.asString ?: "neutral",
                            timestamp = obj.get("timestamp")?.asDouble ?: 0.0
                        ))
                    }
                }
                runOnUiThread {
                    tvNoEpisodes.visibility = if (episodes.isEmpty()) View.VISIBLE else View.GONE
                    adapter.notifyDataSetChanged()
                }
            }
            "member_updated" -> {
                runOnUiThread {
                    setEditMode(false)
                    Toast.makeText(this, "已保存", Toast.LENGTH_SHORT).show()
                }
            }
            "member_deleted" -> {
                runOnUiThread { setResult(RESULT_OK); finish() }
            }
        }
    }

    override fun onBinaryMessage(type: Byte, data: ByteArray) {}
    override fun onError(message: String) { Log.e(TAG, "WS错误: $message") }
    override fun onSupportNavigateUp(): Boolean { finish(); return true }

    override fun onDestroy() {
        wsClient.disconnect()
        super.onDestroy()
    }

    // ========== Adapter ==========

    class EpisodeAdapter(private val items: List<EpisodeItem>) :
        RecyclerView.Adapter<EpisodeAdapter.VH>() {

        private val dateFormat = SimpleDateFormat("MM/dd HH:mm", Locale.CHINA)

        class VH(view: View) : RecyclerView.ViewHolder(view) {
            val tvTime: TextView = view.findViewById(R.id.tvTime)
            val tvEmotion: TextView = view.findViewById(R.id.tvEmotion)
            val tvSummary: TextView = view.findViewById(R.id.tvSummary)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
            val view = LayoutInflater.from(parent.context)
                .inflate(R.layout.item_episode, parent, false)
            return VH(view)
        }

        override fun onBindViewHolder(holder: VH, position: Int) {
            val item = items[position]
            holder.tvTime.text = dateFormat.format(Date((item.timestamp * 1000).toLong()))
            holder.tvEmotion.text = when (item.emotionTag) {
                "happy" -> "开心"; "concerned" -> "担心"; "curious" -> "好奇"
                "tired" -> "疲倦"; "neutral" -> ""; else -> item.emotionTag
            }
            holder.tvSummary.text = item.summary
        }

        override fun getItemCount() = items.size
    }
}
