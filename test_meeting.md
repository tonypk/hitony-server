# 会议记录功能测试指南

## 功能概述

HiTony 会议记录功能提供完整的录音、转录、AI总结流程：

### 三个核心工具

| 工具 | 命令示例 | 功能 |
|------|---------|------|
| `meeting.start` | "开始会议"、"开始录音" | 开始录制对话音频 |
| `meeting.end` | "结束会议"、"停止录音" | 结束录制并保存音频文件 |
| `meeting.transcribe` | "转录"、"转录会议" | 转录音频并生成AI总结 |

---

## 工作流程

```
1. 用户说："开始会议"
   → meeting.start 触发
   → 系统开始录制所有对话音频到内存buffer
   → 提示："开始录制会议，每次对话的语音都会被记录。说'结束会议'来停止。"

2. 用户进行对话...（所有对话都会被录制）
   → 音频自动追加到 session._meeting_audio_buffer

3. 用户说："结束会议"
   → meeting.end 触发
   → 音频保存到 data/meetings/user_{id}/{session_id}.wav
   → 数据库记录更新（duration_s, audio_path, status=ended）
   → 提示："会议录音已结束，共X秒。说'转录'可以获取文字内容。"

4. 用户说："转录"
   → meeting.transcribe 触发（long_running=true）
   → 音频切分为25秒chunks
   → 每个chunk通过Whisper ASR转录
   → 完整转录文本通过LLM生成总结（中文格式）
   → 总结保存到数据库
   → 自动推送到Notion（如果已配置）
   → 提示：语音播报总结（提取关键要点）
```

---

## 数据存储

### 音频文件
```
data/meetings/
├── unbound/              # 未绑定用户的设备
│   └── abc123ef.wav
└── user_1/               # user_id=1 的会议
    ├── def456gh.wav
    └── xyz789ab.wav
```

### 数据库表 (meetings)
```sql
CREATE TABLE meetings (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,              -- 关联用户（可为空）
    device_id VARCHAR(64),        -- 设备ID
    session_id VARCHAR(8),        -- 会议唯一ID（8位UUID）
    title VARCHAR(256),           -- 会议标题
    audio_path VARCHAR(512),      -- 音频文件路径
    duration_s INTEGER,           -- 时长（秒）
    transcript TEXT,              -- 转录文本 + AI总结
    status VARCHAR(16),           -- recording / ended / transcribed
    started_at DATETIME,          -- 开始时间
    ended_at DATETIME,            -- 结束时间
    created_at DATETIME
);
```

---

## AI 总结格式

使用LLM（GPT-4/DeepSeek）生成结构化总结：

```markdown
## 会议主题
[简短描述会议主题]

## 关键要点
- [要点1]
- [要点2]
- [要点3]

## 决策事项
- [决策1]
- [决策2]

## 行动项
- [行动1] - [负责人/时间]
- [行动2] - [负责人/时间]
```

---

## 测试步骤

### 方式1：使用真实设备（推荐）

1. **连接EchoEar设备**
   ```bash
   # 确认设备已连接到服务器
   ws://136.111.249.161:9001/ws
   ```

2. **开始会议录制**
   - 说："小E，开始会议"
   - 听到提示："开始录制会议，每次对话的语音都会被记录。说'结束会议'来停止。"

3. **进行对话**
   - 随意对话，测试各种内容
   - 例如："今天讨论项目进度"、"需要完成三个任务"等

4. **结束会议**
   - 说："小E，结束会议"
   - 听到提示："会议录音已结束，共X秒。说'转录'可以获取文字内容。"

5. **转录并获取总结**
   - 说："小E，转录"
   - 等待处理（可能需要10-30秒）
   - 听到AI总结的语音播报

### 方式2：手动测试（通过Python脚本）

创建测试脚本 `test_meeting_manual.py`：

```python
"""手动测试会议功能（需要真实音频文件）"""
import asyncio
import os
from app.tools.builtin.meeting import meeting_start, meeting_end, meeting_transcribe
from app.session import Session
from app.config import DeviceConfig

async def test_meeting():
    # 创建测试session
    session = Session(device_id="test-device-001", config=DeviceConfig())
    session._meeting_audio_buffer = bytearray()

    # 1. 开始会议
    print("\n=== 测试 meeting.start ===")
    result = await meeting_start(title="测试会议", session=session)
    print(f"结果: {result.text}")
    print(f"meeting_active: {session.meeting_active}")
    print(f"meeting_session_id: {session.meeting_session_id}")

    # 2. 模拟音频数据（这里需要真实的PCM数据）
    # 从WAV文件读取（跳过44字节WAV头）
    test_audio_path = "test_audio.wav"
    if os.path.exists(test_audio_path):
        with open(test_audio_path, "rb") as f:
            f.read(44)  # Skip WAV header
            pcm_data = f.read()
            session._meeting_audio_buffer.extend(pcm_data)
        print(f"\n加载测试音频: {len(pcm_data)} bytes")
    else:
        print("\n警告: test_audio.wav 不存在，使用空音频")

    # 3. 结束会议
    print("\n=== 测试 meeting.end ===")
    result = await meeting_end(session=session)
    print(f"结果: {result.text}")
    print(f"数据: {result.data}")

    # 4. 转录会议
    if len(session._meeting_audio_buffer) > 16000:  # 至少1秒音频
        print("\n=== 测试 meeting.transcribe ===")
        result = await meeting_transcribe(session=session)
        print(f"结果: {result.text[:200]}...")
        if result.data:
            print(f"\n完整转录:")
            print(result.data.get("transcript", "")[:500])
            if "summary" in result.data:
                print(f"\nAI总结:")
                print(result.data.get("summary", ""))

if __name__ == "__main__":
    asyncio.run(test_meeting())
```

运行测试：
```bash
cd /home/tonypk25/hitony-server
source .venv/bin/activate
python test_meeting_manual.py
```

---

## 检查测试结果

### 1. 检查数据库
```bash
# SSH到服务器
ssh echoear-gce

# 查询会议记录
cd /home/tonypk25/hitony-server
.venv/bin/python -c "
import asyncio
from app.database import async_session_factory
from app.models import Meeting
from sqlalchemy import select

async def check_meetings():
    async with async_session_factory() as db:
        result = await db.execute(select(Meeting).order_by(Meeting.created_at.desc()).limit(5))
        meetings = result.scalars().all()

        for m in meetings:
            print(f'\n会议 ID: {m.id}')
            print(f'  Session: {m.session_id}')
            print(f'  标题: {m.title}')
            print(f'  时长: {m.duration_s}s')
            print(f'  状态: {m.status}')
            print(f'  音频: {m.audio_path}')
            print(f'  转录: {m.transcript[:100] if m.transcript else \"无\"}...')

asyncio.run(check_meetings())
"
```

### 2. 检查音频文件
```bash
ls -lh /home/tonypk25/hitony-server/data/meetings/*/
```

### 3. 查看服务器日志
```bash
journalctl -u hitony-server.service -f | grep -i meeting
```

---

## Notion 集成（可选）

如果配置了 Notion，会议转录完成后会自动推送到 Notion 数据库。

### 配置步骤

1. **获取 Notion Token**
   - 访问 https://www.notion.so/my-integrations
   - 创建新的 Integration
   - 复制 Internal Integration Token

2. **创建 Notion Database**
   - 在 Notion 中创建一个 Database
   - 确保包含以下属性：
     - Title (标题)
     - Duration (时长，Number)
     - Transcript (转录，Text)
     - Created (创建时间，Date)

3. **配置到 HiTony**
   ```bash
   curl -X PUT https://api.hitony.ai/api/user/settings \
     -H "Authorization: Bearer $TOKEN" \
     -d '{
       "notion_token": "secret_xxxxxxxxx",
       "notion_database_id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
     }'
   ```

4. **测试推送**
   - 完成一次会议转录
   - 检查 Notion Database 是否收到新条目

---

## Pro Mode / Native Mode 支持

会议总结功能完全支持 Native Mode：

- **Full Native**: 转录（Whisper）+ 总结（LLM）均使用用户自建 OpenClaw
- **Hybrid Native**: 转录使用 ASR Plugin 或回退，总结使用用户自建 LLM
- **Cloud Mode**: 全部使用 HiTony 默认 API

配置示例：
```json
{
  "native_mode": "hybrid",
  "openai_base_url": "https://your-openclaw.com/v1",
  "openai_api_key": "sk-xxxxxxxx",
  "openai_chat_model": "deepseek-chat"
}
```

总结生成会自动使用配置的 LLM：
- 优先使用 `session.config.openai_base_url` + `session.config.openai_chat_model`
- 回退到 `settings.openai_chat_model` 或 `gpt-4-turbo`

---

## 故障排查

### 问题1：提示"当前没有在录音"

**原因**: `session.meeting_active` 为 False

**解决**:
- 先说"开始会议"触发 meeting.start
- 确认听到开始录制的提示
- 再进行对话

### 问题2：转录为空

**原因**: 音频数据太短或没有清晰语音

**解决**:
- 确保会议至少1秒以上
- 检查麦克风是否正常工作
- 查看日志: `grep "Transcribe chunk" /var/log/hitony/server.log`

### 问题3：AI总结失败

**原因**: LLM API调用失败或转录内容太短

**解决**:
- 检查 OpenAI API key 配置
- 确保转录内容 >100 字符
- 查看日志: `grep "Meeting summary" /var/log/hitony/server.log`

### 问题4：音频文件未保存

**原因**: 目录权限或磁盘空间问题

**解决**:
```bash
# 检查目录权限
ls -la /home/tonypk25/hitony-server/data/meetings/

# 检查磁盘空间
df -h

# 手动创建目录（如果不存在）
mkdir -p /home/tonypk25/hitony-server/data/meetings/{unbound,user_0}
chmod 755 /home/tonypk25/hitony-server/data/meetings
```

---

## 性能指标

| 指标 | 数值 |
|------|------|
| 音频格式 | PCM 16kHz mono |
| 每秒音频大小 | 32KB (16000 * 2 bytes) |
| 转录延迟 | ~2-5秒/分钟音频 |
| AI总结延迟 | ~3-8秒 |
| 存储空间 | ~2MB/分钟（WAV） |

**示例**:
- 30分钟会议 → ~60MB WAV 文件
- 转录时间 → ~60-150秒
- 总处理时间 → ~70-160秒

---

## 下一步优化

1. **流式转录**: 边录边转录，降低结束后等待时间
2. **音频压缩**: 使用 Opus 存储，减少空间占用（~1/10 大小）
3. **分段总结**: 超长会议（>1小时）分段总结后合并
4. **实时字幕**: WebSocket 推送实时转录结果
5. **关键词提取**: NLP 提取会议关键词和实体
6. **多语言支持**: 自动检测语言，支持英文/日文等

---

## 相关文件

- `app/tools/builtin/meeting.py` - 会议工具实现
- `app/models.py:68` - Meeting 数据模型
- `app/asr.py` - Whisper ASR 封装
- `app/llm.py` - LLM 调用封装
- `data/meetings/` - 音频文件存储目录
