package com.companionbot

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.DividerItemDecoration
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.gson.JsonObject

/**
 * 家庭成员管理 — 查看、详情、删除
 *
 * 通过独立 WebSocket 连接与服务端通信，获取成员列表。
 */
class FamilyActivity : AppCompatActivity(), WebSocketClient.WebSocketListener {
    companion object {
        private const val TAG = "CompanionBot.Family"
    }

    private lateinit var rvMembers: RecyclerView
    private lateinit var tvEmpty: TextView
    private lateinit var wsClient: WebSocketClient
    private val members = mutableListOf<MemberItem>()
    private lateinit var adapter: MemberAdapter

    data class MemberItem(
        val personId: String,
        val name: String,
        val role: String,
        val age: Int,
        val relationship: String
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_family)
        supportActionBar?.title = "家庭成员管理"
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        rvMembers = findViewById(R.id.rvMembers)
        tvEmpty = findViewById(R.id.tvEmpty)

        adapter = MemberAdapter(members,
            onClick = { member -> openDetail(member.personId) },
            onLongClick = { member -> confirmDelete(member) }
        )
        rvMembers.layoutManager = LinearLayoutManager(this)
        rvMembers.addItemDecoration(DividerItemDecoration(this, DividerItemDecoration.VERTICAL))
        rvMembers.adapter = adapter

        findViewById<Button>(R.id.btnAddMember).setOnClickListener {
            val prefs2 = getSharedPreferences(SettingsActivity.PREFS_NAME, Context.MODE_PRIVATE)
            val enrollUrl = prefs2.getString(SettingsActivity.KEY_SERVER_URL, SettingsActivity.DEFAULT_SERVER)!!
            val intent = Intent(this, EnrollActivity::class.java).apply {
                putExtra(EnrollActivity.EXTRA_SERVER_URL, enrollUrl)
                putExtra(EnrollActivity.EXTRA_CLIENT_ID, "android_enroll")
            }
            startActivity(intent)
        }

        wsClient = WebSocketClient(this)
        val prefs = getSharedPreferences(SettingsActivity.PREFS_NAME, Context.MODE_PRIVATE)
        val url = prefs.getString(SettingsActivity.KEY_SERVER_URL, SettingsActivity.DEFAULT_SERVER)!!
        val botId = prefs.getString(SettingsActivity.KEY_BOT_ID, SettingsActivity.DEFAULT_BOT_ID)!!
        wsClient.connect(url, botId, "android_family_mgr")
    }

    override fun onResume() {
        super.onResume()
        if (wsClient.isConnected) {
            wsClient.requestMembersList()
        }
    }

    private fun openDetail(personId: String) {
        val intent = Intent(this, MemberDetailActivity::class.java)
        intent.putExtra("person_id", personId)
        startActivity(intent)
    }

    private fun confirmDelete(member: MemberItem) {
        AlertDialog.Builder(this)
            .setTitle("删除成员")
            .setMessage("确定删除 ${member.name}？将同时删除所有沟通记录。")
            .setPositiveButton("删除") { _, _ ->
                wsClient.requestDeleteMember(member.personId)
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun updateUI() {
        runOnUiThread {
            tvEmpty.visibility = if (members.isEmpty()) View.VISIBLE else View.GONE
            rvMembers.visibility = if (members.isEmpty()) View.GONE else View.VISIBLE
            adapter.notifyDataSetChanged()
        }
    }

    // ========== WebSocket callbacks ==========

    override fun onConnected() {
        wsClient.requestMembersList()
    }

    override fun onDisconnected(reason: String) {
        Log.i(TAG, "WS断开: $reason")
    }

    override fun onJsonMessage(json: JsonObject) {
        val type = json.get("type")?.asString ?: return
        when (type) {
            "members_list" -> {
                members.clear()
                val arr = json.getAsJsonArray("members") ?: return
                for (el in arr) {
                    val obj = el.asJsonObject
                    members.add(MemberItem(
                        personId = obj.get("person_id")?.asString ?: "",
                        name = obj.get("name")?.asString ?: "",
                        role = obj.get("role")?.asString ?: "adult",
                        age = obj.get("age")?.asInt ?: 0,
                        relationship = obj.get("relationship")?.asString ?: ""
                    ))
                }
                updateUI()
            }
            "member_deleted" -> {
                val pid = json.get("person_id")?.asString ?: ""
                members.removeAll { it.personId == pid }
                updateUI()
            }
        }
    }

    override fun onBinaryMessage(type: Byte, data: ByteArray) {}
    override fun onError(message: String) {
        Log.e(TAG, "WS错误: $message")
    }

    override fun onSupportNavigateUp(): Boolean {
        finish(); return true
    }

    override fun onDestroy() {
        wsClient.disconnect()
        super.onDestroy()
    }

    // ========== Adapter ==========

    class MemberAdapter(
        private val items: List<MemberItem>,
        private val onClick: (MemberItem) -> Unit,
        private val onLongClick: (MemberItem) -> Unit
    ) : RecyclerView.Adapter<MemberAdapter.VH>() {

        class VH(view: View) : RecyclerView.ViewHolder(view) {
            val tvName: TextView = view.findViewById(R.id.tvName)
            val tvInfo: TextView = view.findViewById(R.id.tvInfo)
            val tvRole: TextView = view.findViewById(R.id.tvRole)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
            val view = LayoutInflater.from(parent.context)
                .inflate(R.layout.item_family_member, parent, false)
            return VH(view)
        }

        override fun onBindViewHolder(holder: VH, position: Int) {
            val item = items[position]
            holder.tvName.text = item.name
            holder.tvInfo.text = buildString {
                if (item.relationship.isNotEmpty()) append(item.relationship)
                if (item.age > 0) {
                    if (isNotEmpty()) append(" | ")
                    append("${item.age}岁")
                }
            }
            holder.tvRole.text = when (item.role) {
                "elder" -> "老人"
                "child" -> "小孩"
                else -> "成人"
            }
            holder.itemView.setOnClickListener { onClick(item) }
            holder.itemView.setOnLongClickListener { onLongClick(item); true }
        }

        override fun getItemCount() = items.size
    }
}
