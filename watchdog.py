#!/usr/bin/env python3
"""Watchdog — перезапускает bsc_sniper_bot.py при падении. НЕ ОСТАНАВЛИВАТЬ."""
import subprocess, time, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE       = os.path.dirname(os.path.abspath(__file__))
BOT        = os.path.join(HERE, "bsc_sniper_bot.py")
LOGFILE    = os.path.join(HERE, "watchdog.log")
STDOUT_LOG = os.path.join(HERE, "sniper_stdout.txt")
HANG_TIMEOUT = 900  # 15 мин без активности лога = зависание

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

n = 0
while True:
    n += 1
    log(f"restart #{n} — запуск бота")
    stdout_log = open(STDOUT_LOG, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-u", BOT],
        stdout=stdout_log,
        stderr=subprocess.STDOUT,
    )
    last_size = 0
    last_change = time.time()
    while True:
        try:
            proc.wait(timeout=30)
            break  # бот завершился сам
        except subprocess.TimeoutExpired:
            pass
        try:
            size = os.path.getsize(STDOUT_LOG)
        except Exception:
            size = last_size
        if size != last_size:
            last_size = size
            last_change = time.time()
        elif time.time() - last_change > HANG_TIMEOUT:
            log(f"[HANG] лог не менялся {HANG_TIMEOUT}с — принудительная остановка")
            proc.kill()
            break
    stdout_log.close()
    code = proc.returncode if proc.returncode is not None else -1
    log(f"Бот завершился (код {code}), перезапуск через 5с...")
    time.sleep(5)
