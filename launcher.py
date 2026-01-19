import subprocess
import sys
import time
import os

# ---------- Пути к main файлам ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_FILES = [
    ("main_music.py", "music"),
    ("main_sfx.py", "sfx")
]

processes = []

try:
    for main_file, mode in MAIN_FILES:
        print(f"🚀 Запуск {main_file} ({mode})")
        p = subprocess.Popen([sys.executable, os.path.join(BASE_DIR, main_file), mode])
        processes.append(p)
        time.sleep(1.5)  # небольшая пауза между запусками

    print("✅ Все боты запущены.")

    # Ждём пока процессы не завершатся (по Ctrl+C)
    for p in processes:
        p.wait()

except KeyboardInterrupt:
    print("\n🛑 Остановка всех ботов...")
    for p in processes:
        p.terminate()
    print("✅ Все боты остановлены.")
