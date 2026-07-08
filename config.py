import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

BOT_TOKEN = os.getenv('TG_BOT_TOKEN', '').strip()

# Прокси для обхода блокировки Telegram (актуально для РФ).
# Примеры значений:
#   http://IP:PORT
#   http://USER:PASS@IP:PORT
#   socks5://IP:PORT          (нужно: pip install aiohttp_socks)
#   socks5://USER:PASS@IP:PORT
# Пусто = подключаться напрямую (если включён системный VPN).
TG_PROXY = os.getenv('TG_PROXY', '').strip()


def _ids(raw: str):
    out = []
    for part in (raw or '').replace(';', ',').split(','):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


ADMIN_IDS = _ids(os.getenv('TG_ADMIN_IDS', ''))
EXPORT_CHAT_ID = os.getenv('EXPORT_CHAT_ID', '').strip()
EXPORT_INTERVAL_MIN = int(os.getenv('EXPORT_INTERVAL_MIN', '60') or '0')

DB = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'database': os.getenv('DB_DATABASE', 'metrostroi'),
    'user': os.getenv('DB_USERNAME', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
}
