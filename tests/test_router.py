"""Tests for tools/router.py — regex-based intent routing."""
from app.tools.router import route, _strip_punctuation, RouteMatch


class TestStripPunctuation:
    def test_chinese_punctuation(self):
        assert _strip_punctuation("你好。") == "你好"
        assert _strip_punctuation("播放！") == "播放"
        assert _strip_punctuation("天气？") == "天气"
        assert _strip_punctuation("列表，") == "列表"

    def test_english_punctuation(self):
        assert _strip_punctuation("hello!") == "hello"
        assert _strip_punctuation("query?") == "query"
        assert _strip_punctuation("end.") == "end"

    def test_no_punctuation(self):
        assert _strip_punctuation("你好") == "你好"
        assert _strip_punctuation("") == ""

    def test_multiple_trailing(self):
        assert _strip_punctuation("好的。。。") == "好的"


class TestMusicRouting:
    def test_play_with_query_chinese(self):
        r = route("播放周杰伦的歌")
        assert r is not None
        assert r.tool == "youtube.play"
        assert r.args["query"] == "周杰伦的歌"

    def test_play_with_prefix(self):
        r = route("帮我播放邓紫棋")
        assert r is not None
        assert r.tool == "youtube.play"
        assert "邓紫棋" in r.args["query"]

    def test_listen_to(self):
        r = route("我想听jazz")
        assert r is not None
        assert r.tool == "youtube.play"
        assert "jazz" in r.args["query"]

    def test_play_english(self):
        r = route("play Taylor Swift")
        assert r is not None
        assert r.tool == "youtube.play"
        assert "Taylor Swift" in r.args["query"]

    def test_generic_music(self):
        r = route("放音乐")
        assert r is not None
        assert r.tool == "youtube.play"

    def test_play_music(self):
        r = route("听歌")
        assert r is not None
        assert r.tool == "youtube.play"

    def test_lai_yi_shou(self):
        r = route("来一首爱情歌曲")
        assert r is not None
        assert r.tool == "youtube.play"
        assert "爱情歌曲" in r.args["query"]


class TestPlayerControls:
    def test_pause(self):
        r = route("暂停")
        assert r is not None
        assert r.tool == "player.pause"

    def test_pause_english(self):
        r = route("pause")
        assert r is not None
        assert r.tool == "player.pause"

    def test_resume(self):
        r = route("继续播放")
        assert r is not None
        assert r.tool == "player.resume"

    def test_resume_english(self):
        r = route("resume")
        assert r is not None
        assert r.tool == "player.resume"

    def test_stop(self):
        r = route("停止播放")
        assert r is not None
        assert r.tool == "player.stop"

    def test_stop_english(self):
        r = route("stop")
        assert r is not None
        assert r.tool == "player.stop"

    def test_next(self):
        r = route("下一首")
        assert r is not None
        assert r.tool == "player.next"

    def test_skip(self):
        r = route("skip")
        assert r is not None
        assert r.tool == "player.next"


class TestVolumeControls:
    def test_volume_set_number(self):
        r = route("音量设为50")
        assert r is not None
        assert r.tool == "volume.set"
        assert r.args["level"] == 50

    def test_volume_up(self):
        r = route("大一点")
        assert r is not None
        assert r.tool == "volume.up"

    def test_volume_down(self):
        r = route("小一点")
        assert r is not None
        assert r.tool == "volume.down"

    def test_mute(self):
        r = route("静音")
        assert r is not None
        assert r.tool == "volume.set"
        assert r.args["level"] == 0

    def test_volume_up_english(self):
        r = route("volume up")
        assert r is not None
        assert r.tool == "volume.up"

    def test_volume_down_english(self):
        r = route("volume down")
        assert r is not None
        assert r.tool == "volume.down"


class TestTimerRouting:
    def test_timer_minutes(self):
        r = route("倒计时5分钟")
        assert r is not None
        assert r.tool == "timer.set"
        assert r.args["seconds"] == "300"

    def test_timer_seconds(self):
        r = route("倒计时30秒")
        assert r is not None
        assert r.tool == "timer.set"
        assert r.args["seconds"] == "30"

    def test_timer_remind_pattern(self):
        r = route("3分钟后提醒我")
        assert r is not None
        assert r.tool == "timer.set"
        assert r.args["seconds"] == "180"

    def test_timer_cancel(self):
        r = route("取消倒计时")
        assert r is not None
        assert r.tool == "timer.cancel"


class TestWeatherRouting:
    def test_weather_chinese(self):
        r = route("今天天气怎么样")
        assert r is not None
        assert r.tool == "weather.query"

    def test_weather_tomorrow(self):
        r = route("明天天气")
        assert r is not None
        assert r.tool == "weather.query"

    def test_weather_english(self):
        r = route("weather")
        assert r is not None
        assert r.tool == "weather.query"


class TestMeetingRouting:
    def test_start_meeting(self):
        r = route("开始会议")
        assert r is not None
        assert r.tool == "meeting.start"

    def test_end_meeting(self):
        r = route("结束会议")
        assert r is not None
        assert r.tool == "meeting.end"

    def test_transcribe(self):
        r = route("转录")
        assert r is not None
        assert r.tool == "meeting.transcribe"

    def test_start_recording_english(self):
        r = route("start meeting")
        assert r is not None
        assert r.tool == "meeting.start"


class TestSearchRouting:
    def test_search_chinese(self):
        r = route("搜索最新iPhone价格")
        assert r is not None
        assert r.tool == "web.search"
        assert "iPhone" in r.args["query"]

    def test_search_help(self):
        r = route("帮我查一下明天的航班")
        assert r is not None
        assert r.tool == "web.search"


class TestConversationRouting:
    def test_reset_chinese(self):
        r = route("清空对话")
        assert r is not None
        assert r.tool == "conversation.reset"

    def test_new_chat(self):
        r = route("新对话")
        assert r is not None
        assert r.tool == "conversation.reset"

    def test_reset_english(self):
        r = route("clear chat")
        assert r is not None
        assert r.tool == "conversation.reset"


class TestNoteRouting:
    def test_note_chinese(self):
        r = route("记一下明天开会")
        assert r is not None
        assert r.tool == "note.save"
        assert "明天开会" in r.args["content"]

    def test_note_beiwang(self):
        r = route("备忘 买牛奶")
        assert r is not None
        assert r.tool == "note.save"
        assert "买牛奶" in r.args["content"]


class TestAlarmRouting:
    def test_alarm_set(self):
        r = route("设置闹钟7点30")
        assert r is not None
        assert r.tool == "alarm.set"
        assert r.args["time"] == "07:30"

    def test_alarm_list(self):
        r = route("查看闹钟")
        assert r is not None
        assert r.tool == "alarm.list"

    def test_alarm_cancel(self):
        r = route("取消闹钟")
        assert r is not None
        assert r.tool == "alarm.cancel"


class TestBriefingRouting:
    def test_briefing_chinese(self):
        r = route("今天有什么安排")
        assert r is not None
        assert r.tool == "briefing.daily"

    def test_briefing_english(self):
        r = route("daily briefing")
        assert r is not None
        assert r.tool == "briefing.daily"


class TestReminderRouting:
    def test_reminder_list(self):
        r = route("查看提醒")
        assert r is not None
        assert r.tool == "reminder.list"

    def test_reminder_cancel(self):
        r = route("取消提醒")
        assert r is not None
        assert r.tool == "reminder.cancel"


class TestGreeting:
    def test_hello_zh(self):
        r = route("你好")
        assert r is not None
        assert r.tool == "chat"
        assert r.args["response"] == "你好！有什么可以帮你的吗？"

    def test_thanks_zh(self):
        r = route("谢谢你")
        assert r is not None
        assert r.tool == "chat"
        assert r.args["response"] == "不客气！"

    def test_bye_zh(self):
        r = route("拜拜")
        assert r is not None
        assert r.tool == "chat"
        assert r.args["response"] == "再见！"

    def test_hello_en(self):
        r = route("hello")
        assert r is not None
        assert r.tool == "chat"


class TestNoMatch:
    def test_question(self):
        assert route("今天是什么日子") is None

    def test_empty(self):
        assert route("") is None

    def test_random(self):
        assert route("我好累啊") is None
