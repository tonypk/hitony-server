# HiTony Native Mode - OpenClaw自建配置指南

## 什么是Native Mode？

**Native Mode（原生模式）** 允许用户使用**自建的OpenClaw实例**（或其他OpenAI-compatible API），而不是使用HiTony默认的云端API。

根据部署需求，Native Mode设计为三种子模式：

| 模式 | 名称 | OpenClaw部署 | ASR来源 | LLM来源 | TTS来源 | 推荐场景 |
|------|------|-------------|---------|---------|---------|----------|
| 🟢 | **Full Native** | ASR + LLM + TTS | 用户OpenClaw | 用户OpenClaw | 用户OpenClaw | 完全数据自主 |
| 🟡 | **Hybrid Native** | LLM only | ASR Plugin / HiTony | 用户OpenClaw | ASR Plugin / HiTony | **推荐**：成本优化 |
| 🔵 | **Cloud Mode** | - | HiTony | HiTony | HiTony | 默认模式 |

---

## 模式详解

### 🟢 模式 A：Full Native（完全自建）

**适用场景**：完全数据自主控制，所有AI处理在本地完成。

**OpenClaw部署要求**：
- ✅ Whisper ASR（语音识别）
- ✅ LLM（GPT-4 / DeepSeek / Qwen）
- ✅ TTS（语音合成）

**配置示例**：
```json
{
  "native_mode": "full",
  "openai_base_url": "https://openclaw-full.example.com/v1",
  "openai_api_key": "sk-xxxxxxxx",
  "openai_chat_model": "gpt-4",
  "openai_asr_model": "whisper-1",
  "openai_tts_model": "tts-1"
}
```

**数据流**：
```
用户语音 → HiTony Server → OpenClaw Whisper → OpenClaw LLM → OpenClaw TTS → 用户设备
```

**优势**：
- 🔒 完全数据隐私
- 🎯 无外部API依赖
- 💪 完全可控性能

**劣势**：
- 💰 部署成本高（需GPU服务器）
- 🛠️ 运维复杂度高

---

### 🟡 模式 B：Hybrid Native（混合模式）⭐ **推荐**

**适用场景**：核心LLM自建，ASR/TTS使用轻量级插件或云端服务。

**OpenClaw部署要求**：
- ✅ LLM only（GPT-4 / DeepSeek / Qwen）
- ❌ 不需要Whisper ASR
- ❌ 不需要TTS

**配置示例**：
```json
{
  "native_mode": "hybrid",
  "openai_base_url": "https://openclaw-lite.example.com/v1",
  "openai_api_key": "sk-xxxxxxxx",
  "openai_chat_model": "deepseek-chat",
  "asr_plugin_url": "http://localhost:8100/v1",  // 可选：ASR Plugin
  "tts_plugin_url": "http://localhost:8200/v1"   // 可选：TTS Plugin
}
```

**数据流**：
```
用户语音 → HiTony Server → ASR Plugin/HiTony → OpenClaw LLM → TTS Plugin/HiTony → 用户设备
```

**ASR/TTS来源优先级**（自动回退）：
1. **ASR Plugin** → OpenClaw（尝试） → HiTony默认API（回退）
2. **TTS Plugin** → OpenClaw（尝试） → HiTony默认API（回退）

**优势**：
- 💰 **成本优化**：仅需部署LLM（降低70%+成本）
- 🚀 **低延迟**：ASR/TTS可使用本地Plugin（<100ms）
- 🔄 **灵活回退**：系统自动选择最优路径
- 🎯 **核心数据自主**：对话逻辑完全可控

**劣势**：
- 🌐 ASR/TTS若使用HiTony回退，音频数据经过云端

---

### 🔵 模式 C：Cloud Mode（云端模式）

**适用场景**：默认模式，无需任何配置。

**配置示例**：
```json
{
  "native_mode": "cloud"
  // 无需其他配置
}
```

**数据流**：
```
用户语音 → HiTony Server → HiTony AI服务 → 用户设备
```

**优势**：
- ✅ 零配置开箱即用
- ✅ 稳定高可用
- ✅ 持续更新维护

---

## ASR Plugin 部署指南（Hybrid Native推荐）

**ASR Plugin**是一个轻量级Whisper服务，专为Hybrid Native模式设计。

### 方式1：使用Docker镜像（推荐）

```bash
# 拉取预构建镜像（支持GPU/CPU）
docker pull hitony/asr-plugin:whisper-large-v3

# 启动服务（GPU版本）
docker run -d \
  --name asr-plugin \
  --gpus all \
  -p 8100:8100 \
  -e MODEL_SIZE=large-v3 \
  -e DEVICE=cuda \
  hitony/asr-plugin:whisper-large-v3

# 启动服务（CPU版本）
docker run -d \
  --name asr-plugin \
  -p 8100:8100 \
  -e MODEL_SIZE=medium \
  -e DEVICE=cpu \
  hitony/asr-plugin:whisper-large-v3

# 测试连接
curl -X POST http://localhost:8100/v1/audio/transcriptions \
  -F "file=@test.wav" \
  -F "model=whisper-1"
```

### 方式2：使用docker-compose（推荐生产环境）

创建 `docker-compose.yml`：

```yaml
version: '3.8'

services:
  asr-plugin:
    image: hitony/asr-plugin:whisper-large-v3
    container_name: asr-plugin
    restart: unless-stopped
    ports:
      - "8100:8100"
    environment:
      - MODEL_SIZE=large-v3       # large-v3 / medium / small
      - DEVICE=cuda               # cuda / cpu
      - COMPUTE_TYPE=float16      # float16 / int8 (量化)
      - BATCH_SIZE=16
      - NUM_WORKERS=4
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    volumes:
      - ./cache:/root/.cache/huggingface  # 模型缓存
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8100/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  tts-plugin:  # 可选：TTS Plugin
    image: hitony/tts-plugin:edge-tts
    container_name: tts-plugin
    restart: unless-stopped
    ports:
      - "8200:8200"
    environment:
      - VOICE=zh-CN-XiaoxiaoNeural
      - RATE=+0%
      - VOLUME=+0%
```

启动：
```bash
docker-compose up -d
```

### 方式3：从源码部署

```bash
# 克隆模板仓库
git clone https://github.com/hitony/asr-plugin-template.git
cd asr-plugin-template

# 安装依赖
pip install -r requirements.txt

# 下载模型
python download_model.py --model large-v3

# 启动服务
python server.py --host 0.0.0.0 --port 8100 --device cuda
```

### ASR Plugin配置

在HiTony后台配置ASR Plugin URL：

```json
{
  "native_mode": "hybrid",
  "openai_base_url": "https://openclaw.example.com/v1",
  "openai_api_key": "sk-xxxxxxxx",
  "asr_plugin_url": "http://192.168.1.100:8100/v1",  // 内网地址
  "asr_plugin_fallback": true  // 失败时回退到HiTony
}
```

---

## 配置步骤

### 1. 后台API配置

**API端点**: `PUT /api/user/settings`

**Full Native配置**:
```json
{
  "native_mode": "full",
  "openai_base_url": "https://openclaw-full.example.com/v1",
  "openai_api_key": "sk-xxxxxxxx",
  "openai_chat_model": "gpt-4",
  "openai_asr_model": "whisper-1",
  "openai_tts_model": "tts-1",
  "openai_tts_voice": "alloy"
}
```

**Hybrid Native配置（推荐）**:
```json
{
  "native_mode": "hybrid",
  "openai_base_url": "https://openclaw-lite.example.com/v1",
  "openai_api_key": "sk-xxxxxxxx",
  "openai_chat_model": "deepseek-chat",
  "asr_plugin_url": "http://asr-plugin:8100/v1",
  "tts_plugin_url": "http://tts-plugin:8200/v1"
}
```

**Cloud Mode配置**:
```json
{
  "native_mode": "cloud"
}
```

### 2. 字段说明

| 字段 | 说明 | 示例 | 必填 |
|------|------|------|------|
| `native_mode` | Native模式选择 | `full` / `hybrid` / `cloud` | ✅ |
| `openai_base_url` | OpenClaw Base URL | `https://openclaw.example.com/v1` | Full/Hybrid必填 |
| `openai_api_key` | OpenClaw API token | `sk-xxxxxxxx` | Full/Hybrid必填 |
| `openai_chat_model` | LLM模型名称 | `gpt-4` / `deepseek-chat` | 可选 |
| `openai_asr_model` | ASR模型（Full Native） | `whisper-1` | 可选 |
| `openai_tts_model` | TTS模型（Full Native） | `tts-1` / `tts-1-hd` | 可选 |
| `openai_tts_voice` | TTS语音 | `alloy` / `nova` / `shimmer` | 可选 |
| `asr_plugin_url` | ASR Plugin地址（Hybrid） | `http://localhost:8100/v1` | 可选 |
| `tts_plugin_url` | TTS Plugin地址（Hybrid） | `http://localhost:8200/v1` | 可选 |
| `asr_plugin_fallback` | ASR失败时回退 | `true` / `false` | 可选，默认true |
| `tts_plugin_fallback` | TTS失败时回退 | `true` / `false` | 可选，默认true |

### 3. 验证配置

配置后，系统会自动检测模式：

```python
# Full Native检测
if native_mode == "full" and openai_base_url and openai_api_key:
    → 所有请求 → 用户OpenClaw

# Hybrid Native检测
if native_mode == "hybrid":
    → LLM请求 → 用户OpenClaw
    → ASR/TTS → 尝试Plugin → 回退HiTony

# Cloud Mode
if native_mode == "cloud" or not configured:
    → 所有请求 → HiTony默认API
```

查看服务器日志验证：
```
[INFO] Native mode: hybrid
[INFO] Using Pro mode LLM: https://openclaw.example.com/v1
[INFO] ASR: trying plugin at http://asr-plugin:8100/v1
[INFO] TTS: falling back to default API (plugin not configured)
```

---

## 自动回退机制

### 工作原理

**Hybrid Native模式**内置智能回退：

1. **ASR路径**：
   ```
   音频输入 → ASR Plugin（优先）
            ↓ 失败/404
            → 用户OpenClaw（尝试）
            ↓ 失败/404
            → HiTony默认API（回退）
   ```

2. **TTS路径**：
   ```
   文本输入 → TTS Plugin（优先）
            ↓ 失败/404
            → 用户OpenClaw（尝试）
            ↓ 失败/404
            → HiTony默认API（回退）
   ```

### 日志示例

```
[INFO] ASR: trying plugin at http://asr-plugin:8100/v1
[INFO] ASR: plugin succeeded (120ms)

[WARN] TTS: plugin failed (connection refused), trying OpenClaw
[WARN] TTS: OpenClaw failed (404 not found), falling back to default API
[INFO] TTS: default API succeeded (250ms)
```

### 优势

- 🎯 **零配置智能**：系统自动选择最优路径
- 🔄 **透明回退**：用户无感知，服务不中断
- 💰 **成本灵活**：按需部署Plugin，未部署时自动使用云端
- 🚀 **性能保证**：优先使用低延迟本地Plugin

---

## 性能对比

| 模式 | ASR延迟 | LLM延迟 | TTS延迟 | 总延迟 | 月成本估算 |
|------|---------|---------|---------|--------|-----------|
| **Full Native** | 80-150ms | 200-500ms | 100-200ms | **380-850ms** | ¥3000-8000 |
| **Hybrid Native + Plugin** | 80-150ms | 200-500ms | 100-200ms | **380-850ms** | ¥800-2000 |
| **Hybrid Native（回退）** | 150-300ms | 200-500ms | 200-400ms | **550-1200ms** | ¥800-2000 |
| **Cloud Mode** | 150-300ms | 200-500ms | 200-400ms | **550-1200ms** | ¥0（包含在订阅） |

**推荐配置**：
- 高性能需求：Full Native（完全本地，<850ms）
- **平衡推荐**：**Hybrid Native + ASR Plugin**（核心自主，低成本）
- 快速上手：Cloud Mode（零配置）

---

## 支持的功能

✅ **所有功能均支持Native Mode**：

| 功能 | API调用 | Full Native | Hybrid Native | Cloud Mode |
|------|---------|-------------|---------------|------------|
| 语音识别（ASR） | Whisper API | ✅ OpenClaw | ✅ Plugin/回退 | ✅ HiTony |
| 智能对话（LLM） | Chat Completions | ✅ OpenClaw | ✅ OpenClaw | ✅ HiTony |
| 语音合成（TTS） | TTS API | ✅ OpenClaw | ✅ Plugin/回退 | ✅ HiTony |
| 会议总结 | Chat Completions | ✅ OpenClaw | ✅ OpenClaw | ✅ HiTony |
| 意图识别 | Chat Completions | ✅ OpenClaw | ✅ OpenClaw | ✅ HiTony |
| 工具调用 | Function Calling | ✅ 支持 | ✅ 支持 | ✅ 支持 |

---

## OpenClaw兼容性

Native Mode支持任何OpenAI-compatible API：

- **OpenClaw**（官方）
- **Ollama**（本地部署）
- **DeepSeek API**
- **Groq API**
- **OpenRouter**
- **Azure OpenAI**
- **自建vLLM / FastChat**

只需确保API端点兼容OpenAI的接口规范即可。

---

## 安全性说明

1. **数据隐私**：
   - Full Native：所有AI请求直接发送到用户OpenClaw，HiTony服务器不保存任何API响应
   - Hybrid Native：仅LLM请求发送到用户OpenClaw，ASR/TTS可选Plugin或回退云端
   - Cloud Mode：所有请求经过HiTony云端处理

2. **Token加密**：API Key在数据库中使用AES-256加密存储

3. **传输安全**：建议使用HTTPS端点确保传输层安全

4. **Plugin安全**：
   - ASR/TTS Plugin建议部署在内网（如Docker内部网络）
   - 不对公网暴露Plugin端口
   - 使用API Key或JWT进行认证

---

## 常见问题

### Q: 推荐哪种模式？
A: **Hybrid Native + ASR Plugin**（🟡模式B）：
- 核心LLM自主可控
- ASR/TTS使用轻量Plugin（可选）
- 成本比Full Native降低70%+
- 性能与Full Native相当

### Q: ASR Plugin需要GPU吗？
A: 推荐配置：
- **GPU版本**：Whisper-large-v3，延迟80-120ms（推荐）
- **CPU版本**：Whisper-medium，延迟200-400ms（可接受）

### Q: 如何切换模式？
A: 通过API更新 `native_mode` 字段即可：
```bash
curl -X PUT https://api.hitony.ai/api/user/settings \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"native_mode": "hybrid"}'
```

### Q: OpenClaw需要部署哪些模型？
A: 根据模式选择：
- **Full Native**: Whisper + LLM + TTS（完整）
- **Hybrid Native**: 仅LLM（推荐）
- **Cloud Mode**: 无需部署

### Q: ASR Plugin可以使用其他Whisper服务吗？
A: 可以，只需兼容OpenAI Whisper API即可：
- Faster-Whisper
- WhisperX
- Groq Whisper API
- 自建Whisper服务

### Q: 如何验证Native Mode是否生效？
A: 查看服务器日志：
```
[INFO] Native mode: hybrid
[INFO] Using Pro mode LLM: https://openclaw.example.com/v1
[INFO] ASR: trying plugin at http://asr-plugin:8100/v1
[INFO] ASR: plugin succeeded (95ms)
```

### Q: Plugin服务挂了会怎样？
A: 系统自动回退到HiTony默认API，服务不中断。日志会记录：
```
[WARN] ASR: plugin failed (connection refused), falling back to default API
```

---

## 技术架构

### Full Native架构

```
┌─────────────┐
│  HiTony设备  │
└──────┬──────┘
       │ WebSocket (Opus)
       ▼
┌─────────────────────┐
│   HiTony Server     │
│   (路由层)          │
└──────┬──────────────┘
       │ HTTPS
       ▼
┌─────────────────────┐
│  用户自建OpenClaw    │
│  ┌───────────────┐  │
│  │ Whisper ASR   │  │
│  │ GPT-4 LLM     │  │
│  │ TTS           │  │
│  └───────────────┘  │
└─────────────────────┘
```

### Hybrid Native架构（推荐）

```
┌─────────────┐
│  HiTony设备  │
└──────┬──────┘
       │ WebSocket (Opus)
       ▼
┌─────────────────────────────────────────┐
│           HiTony Server                 │
│           (智能路由)                    │
└─┬──────────────┬──────────────┬─────────┘
  │              │              │
  │ ASR          │ LLM          │ TTS
  ▼              ▼              ▼
┌────────┐  ┌─────────┐  ┌────────┐
│ Plugin │  │OpenClaw │  │ Plugin │
│ 优先   │  │ LLM     │  │ 优先   │
└───┬────┘  │ (核心)  │  └───┬────┘
    │       └─────────┘      │
    │ 失败                   │ 失败
    ▼                        ▼
┌─────────────────────────────────┐
│     HiTony默认API（回退）        │
└─────────────────────────────────┘
```

### Cloud Mode架构

```
┌─────────────┐
│  HiTony设备  │
└──────┬──────┘
       │ WebSocket (Opus)
       ▼
┌─────────────────────┐
│   HiTony Server     │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│  HiTony AI服务      │
│  - Whisper ASR      │
│  - GPT-4 LLM        │
│  - TTS              │
└─────────────────────┘
```

---

## 部署成本估算

### Full Native（完全自建）

**硬件需求**：
- GPU服务器：NVIDIA RTX 4090 / A100
- 内存：32GB+
- 存储：500GB SSD

**月成本**：
- 云GPU实例（AWS p3.2xlarge）：约¥4000-6000/月
- 自建服务器（一次性）：约¥20000-50000

**软件成本**：
- OpenClaw开源版：免费
- LLM API调用：自建模型免费

### Hybrid Native + Plugin（推荐）

**硬件需求**：
- GPU服务器（仅LLM）：NVIDIA RTX 3090 / 4090
- ASR Plugin：CPU 4核 或 入门GPU（如RTX 3060）
- 内存：16GB+
- 存储：200GB SSD

**月成本**：
- 云GPU实例（AWS g5.xlarge）：约¥1500-2500/月
- ASR Plugin（CPU）：约¥200-500/月
- 自建服务器（一次性）：约¥10000-20000

**软件成本**：
- OpenClaw + Plugin：免费（开源）
- DeepSeek API：约¥0.01/1K tokens（可选）

### Cloud Mode

**月成本**：¥0（包含在HiTony订阅中）

**推荐配置**：
- 🎯 个人用户：Cloud Mode（零成本）
- 🏢 小团队/企业：Hybrid Native + Plugin（平衡）
- 🔒 高安全需求：Full Native（完全自主）

---

## 更新日志

- **v3.0.0** (2026-02-16): 重构为Native Mode三子模式架构（Full/Hybrid/Cloud）
- **v2.8.0** (2026-02-16): 新增会议总结Pro模式支持
- **v2.5.0** (2026-02-15): 新增ASR/TTS自动回退机制
- **v2.0.0** (2025-12): 初始Pro模式支持（ASR + LLM + TTS）

---

## 资源链接

- **ASR Plugin Template**: https://github.com/hitony/asr-plugin-template
- **TTS Plugin Template**: https://github.com/hitony/tts-plugin-template
- **Docker镜像**: https://hub.docker.com/r/hitony/asr-plugin
- **OpenClaw官方文档**: https://github.com/openclaw/openclaw
- **DeepSeek API**: https://platform.deepseek.com
- **技术支持**: support@hitony.ai
