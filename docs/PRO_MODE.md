# HiTony Pro 模式 - 自建OpenClaw配置指南

## 什么是Pro模式？

Pro模式允许用户使用**自建的OpenClaw实例**（或其他OpenAI-compatible API），而不是使用HiTony默认的API端点。

所有AI请求（ASR、LLM、TTS）将直接发送到您的OpenClaw实例，确保数据完全掌控。

---

## 配置步骤

### 1. 后台API配置

通过管理后台API配置自建OpenClaw：

**API端点**: `PUT /api/user/settings`

**请求体**:
```json
{
  "openai_base_url": "https://your-openclaw-instance.com/v1",
  "openai_api_key": "your-openclaw-token",
  "openai_chat_model": "gpt-4",
  "openai_asr_model": "whisper-1",
  "openai_tts_model": "tts-1",
  "openai_tts_voice": "alloy"
}
```

### 2. 字段说明

| 字段 | 说明 | 示例 |
|------|------|------|
| `openai_base_url` | OpenClaw实例的Base URL（必填） | `https://openclaw.example.com/v1` |
| `openai_api_key` | OpenClaw的API token（必填） | `sk-xxxxxxxx` |
| `openai_chat_model` | 聊天模型名称（可选） | `gpt-4` / `deepseek-chat` |
| `openai_asr_model` | 语音识别模型（可选） | `whisper-1` |
| `openai_tts_model` | TTS模型（可选） | `tts-1` / `tts-1-hd` |
| `openai_tts_voice` | TTS语音（可选） | `alloy` / `nova` / `shimmer` |

### 3. 验证配置

配置后，系统会自动检测：
- 如果 `openai_base_url` 和 `openai_api_key` 都已配置 → **Pro模式激活**
- 如果未配置 → 使用默认API端点

---

## 支持的功能

✅ 所有功能均支持Pro模式：

| 功能 | API调用 | Pro模式支持 |
|------|---------|------------|
| 语音识别（ASR） | Whisper API | ✅ 支持 |
| 智能对话（LLM） | Chat Completions | ✅ 支持 |
| 语音合成（TTS） | TTS API | ✅ 支持 |
| 会议总结 | Chat Completions | ✅ 支持 |
| 意图识别 | Chat Completions | ✅ 支持 |

---

## OpenClaw兼容性

Pro模式支持任何OpenAI-compatible API，包括但不限于：

- **OpenClaw**（官方）
- **Ollama**（本地部署）
- **DeepSeek API**
- **Groq API**
- **OpenRouter**
- **Azure OpenAI**
- **自建vLLM / FastChat**

只需确保您的API端点兼容OpenAI的接口规范即可。

---

## 安全性说明

1. **数据隐私**：所有AI请求直接发送到您的OpenClaw实例，HiTony服务器不保存任何API响应内容
2. **Token加密**：API Key在数据库中使用AES-256加密存储
3. **传输安全**：建议使用HTTPS端点确保传输层安全

---

## 常见问题

### Q: 如何切换回默认API？
A: 将 `openai_base_url` 和 `openai_api_key` 设置为空字符串即可。

### Q: 支持流式响应吗？
A: 是的，TTS支持流式输出，确保低延迟播放。

### Q: OpenClaw需要部署哪些模型？
A: 至少需要：
- Whisper（ASR）
- GPT-4 / DeepSeek / Qwen（LLM）
- TTS模型（可选，可回退到Edge TTS）

### Q: 如何验证Pro模式是否生效？
A: 查看服务器日志，会显示：
```
Using Pro mode LLM: https://your-openclaw-instance.com/v1
```

---

## 性能优化建议

1. **部署位置**：将OpenClaw部署在距离HiTony服务器较近的区域，减少网络延迟
2. **模型选择**：
   - ASR：Whisper-large-v3（高精度）或 Whisper-medium（平衡）
   - LLM：GPT-4（高质量）或 DeepSeek-V3（性价比）
   - TTS：Edge TTS（免费）或 OpenAI TTS（高质量）
3. **并发限制**：根据OpenClaw实例的性能调整并发请求数

---

## 技术架构

```
┌─────────────┐
│  HiTony设备  │
└──────┬──────┘
       │ WebSocket
       ▼
┌─────────────────┐
│ HiTony Server   │
│  (路由层)       │
└──────┬──────────┘
       │ HTTPS
       ▼
┌─────────────────┐
│ 用户自建OpenClaw │
│  - Whisper ASR  │
│  - GPT-4 LLM    │
│  - TTS          │
└─────────────────┘
```

**数据流**：
1. 用户语音 → HiTony Server（Opus解码）
2. PCM音频 → OpenClaw Whisper（转录）
3. 文本 → OpenClaw GPT-4（理解+生成）
4. 响应文本 → OpenClaw TTS（合成）
5. 音频流 → HiTony设备（播放）

---

## 更新日志

- **v2.8.0** (2026-02-16): 新增会议总结Pro模式支持
- **v2.0.0** (2025-12): 初始Pro模式支持（ASR + LLM + TTS）
