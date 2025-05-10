#ciel_bot.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import random
import json
import google.generativeai as genai
from my_utils import load_prompt, load_memory, save_memory
from keep_alive import keep_alive
keep_alive()
import time
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["CIEL_TOKEN"]
GUILD_ID = os.environ["GUILD_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Gemini API設定
genai.configure(api_key=GEMINI_API_KEY)

# シエルの設定
PROMPT_FILE = "prompts/ciel.json"
MEMORY_FILE = "memory/ciel.json"
EVENT_FILE = "events/ciel_events.json"

JST = timezone(timedelta(hours=9))

last_message_time = 0

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

prompt = load_prompt(PROMPT_FILE)
memory = load_memory(MEMORY_FILE)

def is_currently_active():
    now = datetime.now(JST)  # JSTタイムゾーンで取得
    schedule = memory.get("today_schedule", {})
    wake_str = schedule.get("wake", "09:00")
    sleep_str = schedule.get("sleep", "02:00")

    wake_time = datetime.strptime(wake_str, "%H:%M").time()
    sleep_time = datetime.strptime(sleep_str, "%H:%M").time()

    today = now.date()
    wake_dt = datetime.combine(today, wake_time, tzinfo=JST)  # タイムゾーンを指定してdatetimeを作成
    sleep_dt = datetime.combine(today, sleep_time, tzinfo=JST)  # タイムゾーンを指定してdatetimeを作成

    # 深夜2時とかなら sleep_dt は次の日にしないとおかしい
    if sleep_dt <= wake_dt:
        sleep_dt += timedelta(days=1)

    return wake_dt <= now < sleep_dt



def is_just_back():
    now = datetime.now(JST)
    back_str = memory.get("today_schedule", {}).get("back")
    if not back_str:
        return False
    back_time = datetime.strptime(back_str, "%H:%M").time()
    
    # 同じ日で一度 datetime に変換
    back_dt = datetime.combine(now.date(), back_time)
    
    # 深夜（帰宅時間が now より未来）だったら前日扱いにする
    if back_dt > now:
        back_dt -= timedelta(days=1)

    return abs((now - back_dt).total_seconds()) <= 300  # 5分以内

def generate_full_schedule(force_pattern=None):
    patterns = [
        {"type": "day_shift", "wake": (7, 9), "leave": (9, 10), "back": (18, 20), "sleep": (23, 1)},
        {"type": "night_shift", "wake": (12, 14), "leave": (20, 22), "back": (5, 6), "sleep": (6, 8)},
        {"type": "off_day", "wake": (9, 12), "sleep": (1, 2)},
    ]

    if force_pattern:
        pattern = next(p for p in patterns if p["type"] == force_pattern)
    else:
        pattern = random.choice(patterns)

    schedule = {
        "pattern": pattern["type"],
        "wake": rand_time(*pattern["wake"]),
        "sleep": rand_time(*pattern["sleep"]),
    }
    if "leave" in pattern:
        schedule["leave"] = rand_time(*pattern["leave"])
    if "back" in pattern:
        schedule["back"] = rand_time(*pattern["back"])

    memory["today_schedule"] = schedule
    save_memory(MEMORY_FILE, memory)


# Geminiへのリクエスト関数
async def get_gemini_response(user_message):
    try:
        model = genai.GenerativeModel(model_name="gemini-2.0-flash",
        safety_settings=[
        {
            "category": "HARM_CATEGORY_HARASSMENT",
            "threshold": "BLOCK_NONE"
        },
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_NONE"
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_NONE"
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_NONE"
        },
    ]
)
        full_prompt = f"{prompt}\n\nユーザー: {user_message}\nシエル:"
        response = await asyncio.wait_for(
    asyncio.to_thread(model.generate_content, full_prompt),
    timeout=10
)
        return response.text.strip()
    except asyncio.TimeoutError:
        print("Geminiの応答がタイムアウトしました")
        return "ごめん、ちょっと考えすぎちゃったみたい……"
    except Exception as e:
        error_message = f"ごめん、今は返事できないみたい……（エラー: {e}）"
        print(f"Geminiエラー: {e}", flush=True)
        return error_message


# イベントを自動発生させる
@tasks.loop(minutes=30)
async def event_trigger():
    global last_message_time
    
    if not is_currently_active():
        return
    
    # 現在時刻を取得
    current_time = time.time()
    
    # 最後のメッセージから10分以上経っていれば定型文を送信
    if current_time - last_message_time >= 600:  # 600秒 = 10分
        channel = discord.utils.get(bot.get_all_channels(), name="living-room")
        if channel:
            # 定型文を送信
            with open(EVENT_FILE, "r", encoding="utf-8") as f:
                events_data = json.load(f)
            event_message = random.choice(events_data["events"])
            await channel.send(event_message)
        
# 起動時に実行
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

   if "today_schedule" not in memory or not memory["today_schedule"]:
    generate_full_schedule(force_pattern="off_day")

    
    # 初回起動チェック
if memory.get("is_first_login", True):
    channel = discord.utils.get(bot.get_all_channels(), name="living-room")
    if channel:
        await channel.send("はじめまして、シエルです。今日からこちらでお世話になります。よろしくお願いします。")
    memory["is_first_login"] = False
    save_memory(MEMORY_FILE, memory)

    
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"Synced {len(synced)} command(s)!")
    except Exception as e:
        print(e)
    event_trigger.start()


# スラッシュコマンド: ダイスを振る
@bot.tree.command(name="dice",
                description="サイコロを振る",
                guild=discord.Object(id=GUILD_ID))
async def dice(interaction: discord.Interaction, message: str):
    result = random.choice(["成功！", "失敗……"])
    await interaction.response.send_message(f"{message}\n判定結果: {result}")


# メッセージに反応
@bot.event
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # 初回挨拶ならスキップ（無限ループ対策）
    if message.content.startswith("はじめまして、シエルです。"):
        return

    if not is_currently_active():
        return

    if message.channel.name != "living-room":
        return

    user_message = message.content
    response_text = await get_gemini_response(user_message)

 if message.content == memory.get("last_message"):
        return
     
    # シエルらしい応答
    await message.channel.send(response_text)

    # メモリに保存
    memory["last_message"] = message.content
    save_memory(MEMORY_FILE, memory)
    
    # ユーザーからのメッセージや他のBotが送ったメッセージがあったかどうか
    # 他のメッセージがあればその時点でlast_message_timeを更新
    if message.content:
        global last_message_time
        last_message_time = time.time()


if __name__ == "__main__":
    try:
        bot.run(TOKEN)  # Discord bot起動
    except Exception as e:
        print(f"Bot Error: {e}")
        os.system("kill")
