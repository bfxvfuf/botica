import pymysql
from config import DB


def connect():
    return pymysql.connect(
        host=DB['host'], port=DB['port'], user=DB['user'],
        password=DB['password'], database=DB['database'],
        charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_all(query, params=None):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or [])
            return cur.fetchall()
    finally:
        conn.close()


def fetch_one(query, params=None):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or [])
            return cur.fetchone()
    finally:
        conn.close()


def list_tables():
    rows = fetch_all('SHOW TABLES')
    tables = []
    for r in rows:
        tables.extend(r.values())
    return tables


def table_rows(table):
    # Имя таблицы экранируем обратными кавычками
    safe = '`' + table.replace('`', '') + '`'
    return fetch_all('SELECT * FROM ' + safe)


def stats():
    def scalar(q):
        rows = fetch_all(q)
        if not rows:
            return 0
        return list(rows[0].values())[0]
    return {
        'players': scalar('SELECT COUNT(*) FROM users'),
        'attempts': scalar('SELECT COUNT(*) FROM exam_attempts'),
        'passed': scalar("SELECT COUNT(*) FROM exam_attempts WHERE status='passed'"),
        'violations': scalar('SELECT COUNT(*) FROM violations'),
        'servers': scalar('SELECT COUNT(*) FROM servers'),
    }


def link_account(token, tg_id, tg_username, is_premium=False):
    """Привязывает Telegram-аккаунт к пользователю сайта по одноразовому токену.
    Возвращает nick пользователя при успехе, иначе None."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT user_id FROM tg_link_tokens WHERE token=%s', [token])
            row = cur.fetchone()
            if not row:
                return None
            user_id = row['user_id']
            cur.execute('DELETE FROM telegram_links WHERE user_id=%s OR tg_id=%s', [user_id, tg_id])
            cur.execute(
                'INSERT INTO telegram_links (user_id, tg_id, tg_username, is_premium) VALUES (%s,%s,%s,%s)',
                [user_id, tg_id, tg_username, 1 if is_premium else 0],
            )
            cur.execute('DELETE FROM tg_link_tokens WHERE token=%s', [token])
            conn.commit()
            cur.execute('SELECT nick FROM users WHERE id=%s', [user_id])
            u = cur.fetchone()
            return (u['nick'] if u else str(user_id))
    finally:
        conn.close()


# ===================== Функции для бота (требуют привязки) =====================

def linked_user(tg_id):
    """Возвращает пользователя сайта, привязанного к этому Telegram-ID, или None."""
    return fetch_one(
        'SELECT u.id, u.nick, u.steam_id, u.position, r.name AS role_name, r.rank AS role_rank '
        'FROM telegram_links tl '
        'JOIN users u ON u.id = tl.user_id '
        'LEFT JOIN roles r ON r.id = u.role_id '
        'WHERE tl.tg_id = %s',
        [tg_id],
    )


def is_linked(tg_id):
    return linked_user(tg_id) is not None


def count_users(search=None):
    if search:
        row = fetch_one(
            'SELECT COUNT(*) AS c FROM users WHERE nick LIKE %s OR steam_id LIKE %s',
            ['%' + search + '%', '%' + search + '%'],
        )
    else:
        row = fetch_one('SELECT COUNT(*) AS c FROM users')
    return int(row['c']) if row else 0


def list_users(offset=0, limit=8, search=None):
    """Список игроков (без КМ/часов/смен) — только ник и звание."""
    offset = max(0, int(offset))
    limit = max(1, int(limit))
    if search:
        return fetch_all(
            'SELECT u.id, u.nick, r.name AS role_name '
            'FROM users u LEFT JOIN roles r ON r.id = u.role_id '
            'WHERE u.nick LIKE %s OR u.steam_id LIKE %s '
            'ORDER BY r.rank DESC, u.nick ASC LIMIT %s OFFSET %s',
            ['%' + search + '%', '%' + search + '%', limit, offset],
        )
    return fetch_all(
        'SELECT u.id, u.nick, r.name AS role_name '
        'FROM users u LEFT JOIN roles r ON r.id = u.role_id '
        'ORDER BY r.rank DESC, u.nick ASC LIMIT %s OFFSET %s',
        [limit, offset],
    )


def get_user(user_id):
    return fetch_one(
        'SELECT u.id, u.nick, u.steam_id, u.position, r.name AS role_name '
        'FROM users u LEFT JOIN roles r ON r.id = u.role_id WHERE u.id = %s',
        [user_id],
    )


def list_tickets():
    """Активные билеты (экзамены и тесты)."""
    return fetch_all(
        "SELECT id, title, kind FROM tickets WHERE is_active = 1 ORDER BY kind ASC, title ASC"
    )


def get_ticket(ticket_id):
    return fetch_one('SELECT id, title, kind FROM tickets WHERE id = %s', [ticket_id])


def list_roles():
    """Список званий (от высшего к низшему)."""
    return fetch_all('SELECT id, name, `rank` FROM roles ORDER BY `rank` DESC, name ASC')


def get_role(role_id):
    return fetch_one('SELECT id, name, `rank` FROM roles WHERE id = %s', [role_id])


# ===================== Личный кабинет в ЛС =====================

def user_profile(user_id):
    return fetch_one(
        'SELECT u.id, u.nick, u.steam_id, u.position, u.hours_played, u.shifts, '
        'r.name AS role_name FROM users u LEFT JOIN roles r ON r.id = u.role_id WHERE u.id = %s',
        [user_id],
    )


def user_violations(user_id):
    try:
        return fetch_all(
            'SELECT type, reason, talon_color, points, COALESCE(revoked,0) AS revoked, created_at '
            'FROM violations WHERE user_id = %s ORDER BY created_at DESC LIMIT 30',
            [user_id],
        )
    except Exception:
        return fetch_all(
            'SELECT type, reason, talon_color, points, 0 AS revoked, created_at '
            'FROM violations WHERE user_id = %s ORDER BY created_at DESC LIMIT 30',
            [user_id],
        )


def user_attempts(user_id):
    return fetch_all(
        'SELECT a.id, a.status, a.score, a.total, t.title '
        'FROM exam_attempts a LEFT JOIN tickets t ON t.id = a.ticket_id '
        'WHERE a.user_id = %s ORDER BY a.id DESC LIMIT 15',
        [user_id],
    )


def _nick_of(cur, user_id):
    try:
        cur.execute('SELECT nick FROM users WHERE id=%s', [user_id])
        r = cur.fetchone()
        return r['nick'] if r else str(user_id)
    except Exception:
        return str(user_id)


def _enqueue_action(cur, ref_type, ref_id, text):
    """Кладёт в очередь «действие» — админам придёт сообщение с кнопками."""
    try:
        cur.execute(
            "INSERT INTO bot_outbox (chat_id, text, kind, ref_type, ref_id) VALUES (NULL,%s,'action',%s,%s)",
            [text, ref_type, int(ref_id)],
        )
    except Exception:
        # Старая схема без kind/ref — простое уведомление.
        try:
            cur.execute('INSERT INTO bot_outbox (chat_id, text) VALUES (NULL,%s)', [text])
        except Exception:
            pass


def add_report(user_id, body, hours):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reports (user_id, body, hours, status, created_at) VALUES (%s,%s,%s,'pending',NOW())",
                [user_id, body, int(hours)],
            )
            rid = cur.lastrowid
            nick = _nick_of(cur, user_id)
            text = ('📄 <b>Новый отчёт (актив)</b>\nСотрудник: <b>%s</b>\nЧасов: <b>%s</b>\n\n%s'
                    % (nick, int(hours), (body or '')[:600]))
            _enqueue_action(cur, 'report', rid, text)
            conn.commit()
            return rid
    finally:
        conn.close()


def add_explanatory(user_id, type_, body):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO explanatories (user_id, type, body, status, created_at) VALUES (%s,%s,%s,'pending',NOW())",
                [user_id, type_, body],
            )
            rid = cur.lastrowid
            nick = _nick_of(cur, user_id)
            text = ('📑 <b>Новая объяснительная</b>\nСотрудник: <b>%s</b>\nТип: <b>%s</b>\n\n%s'
                    % (nick, type_, (body or '')[:600]))
            _enqueue_action(cur, 'explanatory', rid, text)
            conn.commit()
            return rid
    finally:
        conn.close()


def add_application(user_id, type_, body):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO applications (user_id, type, body, status, created_at) VALUES (%s,%s,%s,'pending',NOW())",
                [user_id, type_, body],
            )
            rid = cur.lastrowid
            nick = _nick_of(cur, user_id)
            text = ('📝 <b>Новая заявка</b>\nСотрудник: <b>%s</b>\nТип: <b>%s</b>\n\n%s'
                    % (nick, type_, (body or '')[:600]))
            _enqueue_action(cur, 'application', rid, text)
            conn.commit()
            return rid
    finally:
        conn.close()


# ===================== Очередь уведомлений (bot_outbox) =====================

def pop_outbox(limit=10):
    """Возвращает неотправленные сообщения из очереди (с типом и ссылкой на сущность)."""
    try:
        return fetch_all(
            'SELECT id, chat_id, text, kind, ref_type, ref_id '
            'FROM bot_outbox WHERE sent_at IS NULL ORDER BY id ASC LIMIT %s',
            [int(limit)],
        )
    except Exception:
        # Старая схема без kind/ref_type/ref_id.
        try:
            return fetch_all(
                'SELECT id, chat_id, text FROM bot_outbox WHERE sent_at IS NULL ORDER BY id ASC LIMIT %s',
                [int(limit)],
            )
        except Exception:
            return []


def mark_outbox_sent(ids):
    if not ids:
        return
    conn = connect()
    try:
        with conn.cursor() as cur:
            fmt = ','.join(['%s'] * len(ids))
            cur.execute('UPDATE bot_outbox SET sent_at=NOW() WHERE id IN (' + fmt + ')', list(ids))
            conn.commit()
    finally:
        conn.close()


# ===================== Слежение за доставленными сообщениями (bot_messages) =====================

def record_sent_message(ref_type, ref_id, chat_id, message_id):
    """Запоминает отправленное сообщение с кнопками, чтобы потом его удалить."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO bot_messages (ref_type, ref_id, chat_id, message_id) VALUES (%s,%s,%s,%s)',
                [ref_type, int(ref_id), int(chat_id), int(message_id)],
            )
            conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def sent_messages_for_ref(ref_type, ref_id):
    """Все сообщения, отправленные по этой сущности (для удаления)."""
    try:
        return fetch_all(
            'SELECT id, chat_id, message_id FROM bot_messages WHERE ref_type=%s AND ref_id=%s',
            [ref_type, int(ref_id)],
        )
    except Exception:
        return []


def clear_sent_messages(ref_type, ref_id):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM bot_messages WHERE ref_type=%s AND ref_id=%s', [ref_type, int(ref_id)])
            conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ===================== Рассмотрение документов из бота =====================

_REF_TABLE = {'application': 'applications', 'explanatory': 'explanatories', 'report': 'reports'}


def get_doc(ref_type, ref_id):
    """Возвращает документ (заявка/объяснительная/отчёт) с ником и статусом."""
    table = _REF_TABLE.get(ref_type)
    if not table:
        return None
    try:
        return fetch_one(
            'SELECT d.*, u.nick FROM ' + table + ' d JOIN users u ON u.id=d.user_id WHERE d.id=%s',
            [int(ref_id)],
        )
    except Exception:
        return None


def review_doc(ref_type, ref_id, decision, reviewer_user_id=None, note=None):
    """Одобряет/отклоняет документ. Возвращает (ok, owner_user_id, doc).
    Для отчёта с decision=approved актив учитывается автоматически (status=approved)."""
    table = _REF_TABLE.get(ref_type)
    if not table or decision not in ('approved', 'rejected'):
        return (False, None, None)
    doc = get_doc(ref_type, ref_id)
    if not doc:
        return (False, None, None)
    # Уже рассмотрено — не трогаем повторно.
    if (doc.get('status') or 'pending') != 'pending':
        return (False, doc.get('user_id'), doc)
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE ' + table + ' SET status=%s, reviewer_id=%s, review_note=%s, reviewed_at=NOW() WHERE id=%s',
                [decision, reviewer_user_id, note, int(ref_id)],
            )
            conn.commit()
        return (True, doc.get('user_id'), doc)
    except Exception:
        return (False, doc.get('user_id'), doc)
    finally:
        conn.close()


def user_chat_id(user_id):
    """Telegram chat_id привязанного пользователя сайта или None."""
    try:
        row = fetch_one('SELECT tg_id FROM telegram_links WHERE user_id=%s ORDER BY id DESC LIMIT 1', [int(user_id)])
        return int(row['tg_id']) if row and row.get('tg_id') is not None else None
    except Exception:
        return None


def record_exam(user_id, ticket_id, passed, examiner_nick, note=None, role_id=None, position=None):
    """Записывает результат экзамена, проведённого экзаменатором через бота.
    При сдаче обновляет звание (role_id) и/или должность (position).
    Возвращает id созданной попытки."""
    status = 'passed' if passed else 'failed'
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO exam_attempts '
                '(user_id, ticket_id, status, note, examiner_nick, started_at, finished_at) '
                'VALUES (%s,%s,%s,%s,%s,NOW(),NOW())',
                [user_id, ticket_id, status, note, examiner_nick],
            )
            attempt_id = cur.lastrowid
            if passed:
                sets, params = [], []
                if role_id is not None:
                    sets.append('role_id = %s')
                    params.append(role_id)
                if position is not None and position != '':
                    sets.append('position = %s')
                    params.append(position)
                if sets:
                    params.append(user_id)
                    cur.execute('UPDATE users SET ' + ', '.join(sets) + ' WHERE id = %s', params)
            conn.commit()
            return attempt_id
    finally:
        conn.close()
