# utils.py
import os
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

def load_prompt(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_memory(file_path):
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        return {}

def save_memory(file_path, memory):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2, ensure_ascii=False)
    except Exception as e:
        # announce は非同期なのでエラー処理としては print のほうが無難
        print(f"[ERROR] メモリ保存に失敗: {e}")


def is_active(start_hour, end_hour):
    now = datetime.now()
    hour = now.hour
    if start_hour <= end_hour:
        return start_hour <= hour < end_hour
    else:
        return hour >= start_hour or hour < end_hour
