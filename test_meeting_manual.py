"""
手动测试会议功能

使用方法:
1. 准备一个测试音频文件 test_audio.wav (16kHz, mono, PCM)
2. 运行: python test_meeting_manual.py
"""
import asyncio
import os
import sys
from datetime import datetime


async def test_meeting():
    """测试会议记录完整流程"""

    # 导入必要模块
    from app.tools.builtin.meeting import meeting_start, meeting_end, meeting_transcribe
    from app.session import Session, UserConfig

    print("=" * 60)
    print("HiTony 会议记录功能测试")
    print("=" * 60)

    # 创建测试session
    device_id = "test-device-meeting"
    session = Session(device_id=device_id)
    session.config = UserConfig(
        user_id=0,  # 未绑定用户
        openai_base_url="",
        openai_api_key="",
    )
    session._meeting_audio_buffer = bytearray()

    # ============================================================
    # 步骤1: 开始会议
    # ============================================================
    print("\n\n📍 步骤 1/4: 开始会议录制")
    print("-" * 60)

    result = await meeting_start(title="测试会议 - " + datetime.now().strftime("%H:%M"), session=session)

    print(f"✅ meeting.start 结果:")
    print(f"   类型: {result.type}")
    print(f"   消息: {result.text}")
    print(f"   meeting_active: {session.meeting_active}")
    print(f"   meeting_session_id: {session.meeting_session_id}")
    print(f"   meeting_db_id: {session.meeting_db_id}")

    if not session.meeting_active:
        print("❌ 错误: 会议未成功启动")
        return

    # ============================================================
    # 步骤2: 加载测试音频
    # ============================================================
    print("\n\n📍 步骤 2/4: 加载测试音频")
    print("-" * 60)

    # 查找测试音频文件
    test_audio_paths = [
        "test_audio.wav",
        "sample.wav",
        "/tmp/test.wav",
    ]

    audio_loaded = False
    for test_audio_path in test_audio_paths:
        if os.path.exists(test_audio_path):
            try:
                with open(test_audio_path, "rb") as f:
                    # 读取并跳过WAV头（44字节）
                    wav_header = f.read(44)
                    pcm_data = f.read()

                    session._meeting_audio_buffer.extend(pcm_data)

                print(f"✅ 成功加载音频文件: {test_audio_path}")
                print(f"   WAV 头大小: {len(wav_header)} bytes")
                print(f"   PCM 数据: {len(pcm_data)} bytes")
                print(f"   预计时长: {len(pcm_data) / (16000 * 2):.1f} 秒")
                audio_loaded = True
                break
            except Exception as e:
                print(f"⚠️  加载失败: {test_audio_path} - {e}")
                continue

    if not audio_loaded:
        print("⚠️  警告: 未找到测试音频文件")
        print("   将使用空音频进行测试（会议将被标记为过短）")
        print("")
        print("   提示: 创建测试音频的方法:")
        print("   1. 录制任意WAV文件（16kHz, mono）")
        print("   2. 使用 ffmpeg 转换:")
        print("      ffmpeg -i input.mp3 -ar 16000 -ac 1 test_audio.wav")
        print("")

        # 创建最小音频数据（避免过短）
        # 1秒 = 16000 samples * 2 bytes = 32000 bytes
        session._meeting_audio_buffer.extend(b'\x00' * 32000)
        print(f"   已创建 {len(session._meeting_audio_buffer)} bytes 静音数据")

    # ============================================================
    # 步骤3: 结束会议
    # ============================================================
    print("\n\n📍 步骤 3/4: 结束会议")
    print("-" * 60)

    result = await meeting_end(session=session)

    print(f"✅ meeting.end 结果:")
    print(f"   类型: {result.type}")
    print(f"   消息: {result.text}")
    if result.data:
        print(f"   数据: {result.data}")

    # 检查音频文件是否保存
    if result.data and "meeting_id" in result.data:
        meeting_id = result.data["meeting_id"]
        audio_path = f"data/meetings/unbound/{meeting_id}.wav"
        if os.path.exists(audio_path):
            file_size = os.path.getsize(audio_path)
            print(f"   音频文件: {audio_path} ({file_size} bytes)")
        else:
            print(f"   ⚠️  音频文件未找到: {audio_path}")

    # ============================================================
    # 步骤4: 转录会议（如果有有效音频）
    # ============================================================
    duration_s = result.data.get("duration_s", 0) if result.data else 0

    if duration_s >= 1:
        print("\n\n📍 步骤 4/4: 转录并生成AI总结")
        print("-" * 60)
        print("⏳ 正在转录音频，请稍候...")
        print("   (预计耗时: ~2-5秒/分钟音频)")

        try:
            result = await meeting_transcribe(session=session)

            print(f"\n✅ meeting.transcribe 结果:")
            print(f"   类型: {result.type}")
            print(f"   消息: {result.text}")

            if result.data:
                transcript = result.data.get("transcript", "")
                summary = result.data.get("summary", "")

                print(f"\n📝 完整转录 ({len(transcript)} 字符):")
                print("-" * 60)
                if transcript:
                    print(transcript[:500])
                    if len(transcript) > 500:
                        print(f"... (省略 {len(transcript) - 500} 字符)")
                else:
                    print("   (无转录内容)")

                print(f"\n🤖 AI 总结:")
                print("-" * 60)
                if summary:
                    print(summary)
                else:
                    print("   (未生成总结)")

        except Exception as e:
            print(f"\n❌ 转录失败: {e}")
            import traceback
            traceback.print_exc()

    else:
        print("\n\n⏭️  步骤 4/4: 跳过转录")
        print("-" * 60)
        print(f"   音频时长过短 ({duration_s}s < 1s)，无法转录")

    # ============================================================
    # 总结
    # ============================================================
    print("\n\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)

    # 查询数据库中的会议记录
    print("\n📊 数据库中的会议记录 (最近5条):")
    print("-" * 60)
    try:
        from app.database import async_session_factory
        from app.models import Meeting
        from sqlalchemy import select

        async with async_session_factory() as db:
            result = await db.execute(
                select(Meeting)
                .order_by(Meeting.created_at.desc())
                .limit(5)
            )
            meetings = result.scalars().all()

            if meetings:
                for i, m in enumerate(meetings, 1):
                    print(f"\n会议 #{i}:")
                    print(f"   ID: {m.id}")
                    print(f"   Session: {m.session_id}")
                    print(f"   标题: {m.title}")
                    print(f"   设备: {m.device_id}")
                    print(f"   时长: {m.duration_s}s")
                    print(f"   状态: {m.status}")
                    print(f"   音频: {m.audio_path or '无'}")
                    print(f"   转录: {'是' if m.transcript else '否'} ({len(m.transcript or '')} 字符)")
                    print(f"   创建: {m.started_at}")
            else:
                print("   (无会议记录)")

    except Exception as e:
        print(f"   ⚠️  无法查询数据库: {e}")

    print("\n" + "=" * 60)


def main():
    """主函数"""
    try:
        asyncio.run(test_meeting())
    except KeyboardInterrupt:
        print("\n\n⚠️  测试被用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
