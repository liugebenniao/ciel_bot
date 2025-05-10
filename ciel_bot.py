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

# ログ用関数（メッセージをチャンネルに送る）
async def announce(text):
    channel = discord.utils.get(bot.get_all_channels(), name="living-room")
    if channel:
        await channel.send(text)


def is_currently_active():
    now = datetime.now(JST)
    schedule = memory.get("today_schedule", {})
    wake_str = schedule.get("wake", "09:00")
    sleep_str = schedule.get("sleep", "02:00")

    wake_time = datetime.strptime(wake_str, "%H:%M").time()
    sleep_time = datetime.strptime(sleep_str, "%H:%M").time()

    today = now.date()
    wake_dt = datetime.combine(today, wake_time, tzinfo=JST)
    sleep_dt = datetime.combine(today, sleep_time, tzinfo=JST)

    if sleep_dt <= wake_dt:
        sleep_dt += timedelta(days=1)

    return wake_dt <= now < sleep_dt


def rand_time(start_hour, end_hour):
    hour = random.randint(start_hour, end_hour - 1)
    minute = random.choice([0, 15, 30, 45])
    return f"{hour:02d}:{minute:02d}"


def is_just_back():
    now = datetime.now(JST)
    back_str = memory.get("today_schedule", {}).get("back")
    if not back_str:
        return False
    back_time = datetime.strptime(back_str, "%H:%M").time()
    back_dt = datetime.combine(now.date(), back_time)
    if back_dt > now:
        back_dt -= timedelta(days=1)

    return abs((now - back_dt).total_seconds()) <= 300


def generate_full_schedule(force_pattern=None):
    patterns = [
        {"type": "day_shift", "wake": (7, 9), "leave": (9, 10), "back": (18, 20), "sleep": (23, 1)},
        {"type": "night_shift", "wake": (12, 14), "leave": (20, 22), "back": (5, 6), "sleep": (6, 8)},
        {"type": "off_day", "wake": (9, 12), "sleep": (1, 2)},
    ]

    pattern = next(p for p in patterns if p["type"] == force_pattern) if force_pattern else random.choice(patterns)

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


async def get_gemini_response(user_message):
    try:
        model = genai.GenerativeModel(model_name="gemini-2.0-flash",
            safety_settings=[
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
        )
        full_prompt = f"{prompt}\n\nユーザー: {user_message}\nシエル:"
        response = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, full_prompt),
            timeout=10
        )
        return response.text.strip()
    except asyncio.TimeoutError:
        await announce("Geminiの応答がタイムアウトしました")
        return "ごめん、ちょっと考えすぎちゃったみたい……"
    except Exception as e:
        await announce(f"Geminiエラー: {e}")
        return f"ごめん、今は返事できないみたい……（エラー: {e}）"


@tasks.loop(minutes=30)
async def event_trigger():
    global last_message_time
    if not is_currently_active():
        return
    current_time = time.time()
    if current_time - last_message_time >= 600:
        channel = discord.utils.get(bot.get_all_channels(), name="living-room")
        if channel:
            if is_just_back():
                user_message = "situation: you came back home now"
                response_text = await get_gemini_response(user_message)
                await channel.send(response_text)
            else:
                with open(EVENT_FILE, "r", encoding="utf-8") as f:
                    events_data = json.load(f)
                event_message = random.choice(events_data["events"])
                await channel.send(event_message)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    if "today_schedule" not in memory or not memory["today_schedule"]:
        generate_full_schedule(force_pattern="off_day")
        await announce("きょうはおやすみだよ〜")

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
        await announce(f"[ERROR] スラッシュコマンドの同期に失敗: {e}")

    event_trigger.start()


@bot.tree.command(name="dice", description="サイコロを振る", guild=discord.Object(id=GUILD_ID))
async def dice(interaction: discord.Interaction, message: str):
    result = random.choice(["成功！", "失敗……"])
    await interaction.response.send_message(f"{message}\n判定結果: {result}")


@bot.tree.command(name="schedule", description="今日のスケジュールを表示", guild=discord.Object(id=GUILD_ID))
async def schedule(interaction: discord.Interaction):
    await interaction.response.defer()
    schedule = memory.get("today_schedule", {})
    lines = [f"{key.capitalize()}: {value}" for key, value in schedule.items()]
    await interaction.followup.send("今日のスケジュール：\n" + "\n".join(lines))


@bot.tree.command(name="testmode", description="テストモードをオンまたはオフにします", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(enabled="true でオン、false でオフ")
async def testmode(interaction: discord.Interaction, enabled: bool):
    memory["test_mode"] = enabled
    save_memory(MEMORY_FILE, memory)
    status = "オン" if enabled else "オフ"
    await interaction.response.send_message(f"テストモードを{status}にしました。")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    await bot.process_commands(message)

    if message.content.startswith("はじめまして、シエルです。"):
        return

    if not memory.get("test_mode", False) and not is_currently_active():
        return

    if message.channel.name != "living-room":
        return

    if message.content == memory.get("last_message"):
        return

    user_message = message.content
    response_text = await get_gemini_response(user_message)
    await message.channel.send(response_text)

    memory["last_message"] = message.content
    memory["last_bot_response"] = response_text
    save_memory(MEMORY_FILE, memory)

    global last_message_time
    last_message_time = time.time()


if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Bot Error: {e}")
        asyncio.run(announce(f"[ERROR] Bot 起動エラー: {e}"))
