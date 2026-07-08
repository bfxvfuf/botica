import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.exceptions import TelegramNetworkError

import config
import db
import exporter

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('metrostroi-bot')

dp = Dispatcher()

PREMIUM_BADGE = '⭐️'

USERS_PER_PAGE = 8
TICKETS_PER_PAGE = 8

# Тексты кнопок меню
BTN_EXAM = '🎓 Провести экзамен'
BTN_STATS = '📊 Статистика'
BTN_EXPORT = '📑 Экспорт'
BTN_HELP = 'ℹ️ Помощь'
# Личный кабинет сотрудника
BTN_PROFILE = '👤 Профиль'
BTN_VIOLATIONS = '⚠️ Нарушения и талоны'
BTN_TESTS = '📝 Мои тесты'
BTN_REPORT = '🗒 Отчёт об работе'
BTN_EXPLANATORY = '✍️ Объяснительная'
BTN_APPLICATION = '📄 Заявление'

EXPLANATORY_TYPES = ['Неявка', 'Неактив']
APPLICATION_TYPES = ['Отпуск', 'Повышение', 'Увольнение по ПСЖ']
TALON_NAMES = {'green': '🟢 Зелёный', 'yellow': '🟡 Жёлтый', 'red': '🔴 Красный'}


class ExamFlow(StatesGroup):
    pick_user = State()
    pick_ticket = State()
    pick_result = State()
    pick_role = State()
    enter_position = State()
    enter_description = State()


class DocFlow(StatesGroup):
    report_body = State()
    report_hours = State()
    expl_body = State()
    appl_body = State()


def build_bot() -> Bot:
    """Создаёт Bot с прокси (если задан) и HTML по умолчанию."""
    session = AiohttpSession(proxy=config.TG_PROXY or None, timeout=60)
    return Bot(
        config.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def is_admin(uid: int) -> bool:
    return not config.ADMIN_IDS or uid in config.ADMIN_IDS


def premium_badge(user) -> str:
    return f' {PREMIUM_BADGE}' if getattr(user, 'is_premium', False) else ''


# ===================== Клавиатуры =====================

def main_menu(adm: bool) -> ReplyKeyboardMarkup:
    rows = []
    if adm:
        rows.append([KeyboardButton(text=BTN_EXAM)])
        rows.append([KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_EXPORT)])
    rows.append([KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_VIOLATIONS)])
    rows.append([KeyboardButton(text=BTN_TESTS)])
    rows.append([KeyboardButton(text=BTN_REPORT)])
    rows.append([KeyboardButton(text=BTN_EXPLANATORY), KeyboardButton(text=BTN_APPLICATION)])
    rows.append([KeyboardButton(text=BTN_HELP)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def types_keyboard(prefix: str, types) -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(text=t, callback_data=f'{prefix}:{i}')] for i, t in enumerate(types)]
    kb.append([InlineKeyboardButton(text='✖ Отмена', callback_data='doccancel')])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def users_keyboard(users, page: int, total: int) -> InlineKeyboardMarkup:
    kb = []
    for u in users:
        role = u.get('role_name') or 'Игрок'
        kb.append([InlineKeyboardButton(
            text=f"{u['nick']} — {role}",
            callback_data=f"exuser:{u['id']}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='◀', callback_data=f"exu:{page-1}"))
    if (page + 1) * USERS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text='▶', callback_data=f"exu:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text='✖ Отмена', callback_data='excancel')])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def tickets_keyboard(tickets, page: int) -> InlineKeyboardMarkup:
    total = len(tickets)
    chunk = tickets[page * TICKETS_PER_PAGE:(page + 1) * TICKETS_PER_PAGE]
    kb = []
    for t in chunk:
        mark = '📘' if t.get('kind') == 'exam' else '✔'
        kb.append([InlineKeyboardButton(
            text=f"{mark} {t['title']}",
            callback_data=f"exticket:{t['id']}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='◀', callback_data=f"ext:{page-1}"))
    if (page + 1) * TICKETS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text='▶', callback_data=f"ext:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text='✖ Отмена', callback_data='excancel')])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def roles_keyboard(roles) -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(text=r['name'], callback_data=f"exrole:{r['id']}")] for r in roles]
    kb.append([InlineKeyboardButton(text='✖ Отмена', callback_data='excancel')])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='✅ Сдал', callback_data='exres:pass'),
            InlineKeyboardButton(text='❌ Не сдал', callback_data='exres:fail'),
        ],
        [InlineKeyboardButton(text='✖ Отмена', callback_data='excancel')],
    ])


def skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⏭ Пропустить', callback_data='exskip')],
        [InlineKeyboardButton(text='✖ Отмена', callback_data='excancel')],
    ])


# ===================== Привязка / меню =====================

async def require_linked(m: Message):
    """Возвращает пользователя сайта или None (и шлёт подсказку)."""
    try:
        user = await asyncio.to_thread(db.linked_user, m.from_user.id)
    except Exception as e:
        await m.answer('Ошибка БД: ' + str(e))
        return None
    if not user:
        await m.answer(
            '⛔ Сначала привяжите Telegram к аккаунту на сайте.\n'
            'Откройте профиль → «Привязать Telegram».'
        )
        return None
    return user


async def send_welcome(m: Message):
    try:
        user = await asyncio.to_thread(db.linked_user, m.from_user.id)
    except Exception as e:
        await m.answer('Ошибка БД: ' + str(e))
        return
    badge = premium_badge(m.from_user)
    if not user:
        await m.answer(
            f'🚇 <b>Global Metrostroi — бот проекта</b>{badge}\n\n'
            '⛔ Ваш Telegram ещё не привязан.\n'
            'Откройте профиль на сайте и нажмите «Привязать Telegram» — и бот заработает.'
        )
        return
    adm = is_admin(m.from_user.id)
    lines = [
        f'🚇 <b>Global Metrostroi</b>{badge}',
        f'Привязан к аккаунту: <b>{user["nick"]}</b>',
        '',
    ]
    if adm:
        lines.append('🎓 <b>Провести экзамен</b> — выбрать игрока, билет, отметить сдал/не сдал.')
        lines.append('/stats — сводка · /export — выгрузка в Excel')
    else:
        lines.append('Аккаунт привязан ✅. Проведение экзаменов доступно только админам.')
    await m.answer('\n'.join(lines), reply_markup=main_menu(adm))


@dp.message(Command('start'))
async def cmd_start(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    token = (command.args or '').strip()
    if token:
        try:
            nick = await asyncio.to_thread(
                db.link_account,
                token,
                m.from_user.id,
                m.from_user.username or '',
                bool(getattr(m.from_user, 'is_premium', False)),
            )
        except Exception as e:
            await m.answer('Ошибка привязки: ' + str(e))
            return
        if nick:
            badge = premium_badge(m.from_user)
            await m.answer(
                f'✅ Telegram привязан к аккаунту <b>{nick}</b> на сайте.{badge}'
            )
            await send_welcome(m)
        else:
            await m.answer(
                '⛔ Ссылка привязки недействительна или устарела. '
                'Сгенерируйте новую в профиле на сайте.'
            )
        return
    await send_welcome(m)


@dp.message(Command('help'))
async def cmd_help(m: Message):
    await send_welcome(m)


@dp.message(F.text == BTN_HELP)
async def btn_help(m: Message, state: FSMContext):
    await state.clear()
    await send_welcome(m)


@dp.message(Command('stats'))
async def cmd_stats(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer('⛔ Нет доступа.')
        return
    if not await require_linked(m):
        return
    try:
        s = await asyncio.to_thread(db.stats)
    except Exception as e:
        await m.answer('Ошибка БД: ' + str(e))
        return
    await m.answer(
        '📊 <b>Статистика</b>\n'
        f"Игроков: <b>{s['players']}</b>\n"
        f"Попыток экзамена: <b>{s['attempts']}</b> (сдано {s['passed']})\n"
        f"Нарушений: <b>{s['violations']}</b>\n"
        f"Серверов: <b>{s['servers']}</b>"
    )


@dp.message(F.text == BTN_STATS)
async def btn_stats(m: Message):
    await cmd_stats(m)


@dp.message(Command('export'))
async def cmd_export(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer('⛔ Нет доступа.')
        return
    if not await require_linked(m):
        return
    await m.answer('⏳ Формирую выгрузку…')
    try:
        path = await asyncio.to_thread(exporter.build_workbook)
        await m.answer_document(FSInputFile(path), caption='📑 Выгрузка всех таблиц БД')
    except Exception as e:
        await m.answer('Ошибка экспорта: ' + str(e))


@dp.message(F.text == BTN_EXPORT)
async def btn_export(m: Message):
    await cmd_export(m)


# ===================== Проведение экзамена =====================

async def start_exam(m: Message, state: FSMContext):
    if m.chat.type != 'private':
        await m.answer('Проведение экзамена доступно только в личке с ботом.')
        return
    user = await require_linked(m)
    if not user:
        return
    if not is_admin(m.from_user.id):
        await m.answer('⛔ Проводить экзамены могут только администраторы бота.')
        return
    try:
        total = await asyncio.to_thread(db.count_users)
        users = await asyncio.to_thread(db.list_users, 0, USERS_PER_PAGE)
    except Exception as e:
        await m.answer('Ошибка БД: ' + str(e))
        return
    if not users:
        await m.answer('В базе пока нет игроков.')
        return
    await state.clear()
    await state.update_data(examiner_nick=user['nick'])
    await state.set_state(ExamFlow.pick_user)
    await m.answer(
        '🎓 <b>Проведение экзамена</b>\nВыберите игрока:',
        reply_markup=users_keyboard(users, 0, total),
    )


@dp.message(Command('exam'))
async def cmd_exam(m: Message, state: FSMContext):
    await start_exam(m, state)


@dp.message(F.text == BTN_EXAM)
async def btn_exam(m: Message, state: FSMContext):
    await start_exam(m, state)


@dp.callback_query(F.data == 'excancel')
async def exam_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await c.message.answer('Отменено.')
    await c.answer()


@dp.callback_query(F.data.startswith('exu:'))
async def exam_user_page(c: CallbackQuery):
    page = int(c.data.split(':')[1])
    total = await asyncio.to_thread(db.count_users)
    users = await asyncio.to_thread(db.list_users, page * USERS_PER_PAGE, USERS_PER_PAGE)
    try:
        await c.message.edit_reply_markup(reply_markup=users_keyboard(users, page, total))
    except Exception:
        pass
    await c.answer()


@dp.callback_query(ExamFlow.pick_user, F.data.startswith('exuser:'))
async def exam_pick_user(c: CallbackQuery, state: FSMContext):
    uid = int(c.data.split(':')[1])
    u = await asyncio.to_thread(db.get_user, uid)
    if not u:
        await c.answer('Игрок не найден', show_alert=True)
        return
    tickets = await asyncio.to_thread(db.list_tickets)
    if not tickets:
        await c.answer()
        await c.message.answer('Нет активных билетов. Добавьте билет в админке.')
        await state.clear()
        return
    await state.update_data(user_id=uid, user_nick=u['nick'])
    await state.set_state(ExamFlow.pick_ticket)
    await c.message.answer(
        f"Игрок: <b>{u['nick']}</b>\nВыберите билет (📘 экзамен / ✔ тест):",
        reply_markup=tickets_keyboard(tickets, 0),
    )
    await c.answer()


@dp.callback_query(ExamFlow.pick_ticket, F.data.startswith('ext:'))
async def exam_ticket_page(c: CallbackQuery):
    page = int(c.data.split(':')[1])
    tickets = await asyncio.to_thread(db.list_tickets)
    try:
        await c.message.edit_reply_markup(reply_markup=tickets_keyboard(tickets, page))
    except Exception:
        pass
    await c.answer()


@dp.callback_query(ExamFlow.pick_ticket, F.data.startswith('exticket:'))
async def exam_pick_ticket(c: CallbackQuery, state: FSMContext):
    tid = int(c.data.split(':')[1])
    t = await asyncio.to_thread(db.get_ticket, tid)
    if not t:
        await c.answer('Билет не найден', show_alert=True)
        return
    await state.update_data(ticket_id=tid, ticket_title=t['title'], ticket_kind=t['kind'])
    await state.set_state(ExamFlow.pick_result)
    data = await state.get_data()
    await c.message.answer(
        f"Игрок: <b>{data['user_nick']}</b>\nБилет: <b>{t['title']}</b>\nРезультат?",
        reply_markup=result_keyboard(),
    )
    await c.answer()


@dp.callback_query(ExamFlow.pick_result, F.data.startswith('exres:'))
async def exam_result(c: CallbackQuery, state: FSMContext):
    res = c.data.split(':')[1]
    if res == 'pass':
        roles = await asyncio.to_thread(db.list_roles)
        if not roles:
            await c.answer()
            await c.message.answer('Нет званий в базе.')
            await state.clear()
            return
        await state.update_data(passed=True)
        await state.set_state(ExamFlow.pick_role)
        await c.message.answer('✅ Сдал. Выберите звание для присвоения:', reply_markup=roles_keyboard(roles))
    else:
        await state.update_data(passed=False, role_id=None, position=None)
        await state.set_state(ExamFlow.enter_description)
        await c.message.answer('❌ Не сдал. Добавьте описание/комментарий (или «Пропустить»):', reply_markup=skip_keyboard())
    await c.answer()


@dp.callback_query(ExamFlow.pick_role, F.data.startswith('exrole:'))
async def exam_pick_role(c: CallbackQuery, state: FSMContext):
    rid = int(c.data.split(':')[1])
    role = await asyncio.to_thread(db.get_role, rid)
    if not role:
        await c.answer('Звание не найдено', show_alert=True)
        return
    await state.update_data(role_id=rid, role_name=role['name'])
    await state.set_state(ExamFlow.enter_position)
    await c.message.answer(
        f"Звание: <b>{role['name']}</b>\nТеперь укажите <b>должность</b> (текстом), либо «Пропустить»:",
        reply_markup=skip_keyboard(),
    )
    await c.answer()


@dp.message(ExamFlow.enter_position)
async def exam_position(m: Message, state: FSMContext):
    pos = (m.text or '').strip()[:96]
    await state.update_data(position=pos)
    await state.set_state(ExamFlow.enter_description)
    await m.answer('Введите описание/комментарий к экзамену (или «Пропустить»):', reply_markup=skip_keyboard())


@dp.message(ExamFlow.enter_description)
async def exam_description(m: Message, state: FSMContext):
    note = (m.text or '').strip()[:255]
    await state.update_data(note=note)
    await finalize_exam(m, state)


@dp.callback_query(F.data == 'exskip')
async def exam_skip(c: CallbackQuery, state: FSMContext):
    cur = await state.get_state()
    if cur == ExamFlow.enter_position.state:
        await state.update_data(position=None)
        await state.set_state(ExamFlow.enter_description)
        await c.message.answer('Введите описание/комментарий к экзамену (или «Пропустить»):', reply_markup=skip_keyboard())
        await c.answer()
    elif cur == ExamFlow.enter_description.state:
        await state.update_data(note=None)
        await c.answer()
        await finalize_exam(c.message, state)
    else:
        await c.answer()


async def finalize_exam(target: Message, state: FSMContext):
    data = await state.get_data()
    try:
        await asyncio.to_thread(
            db.record_exam,
            data['user_id'], data['ticket_id'], bool(data.get('passed')),
            data.get('examiner_nick'), data.get('note'),
            data.get('role_id'), data.get('position'),
        )
    except Exception as e:
        await target.answer('Ошибка записи: ' + str(e))
        await state.clear()
        return
    passed = bool(data.get('passed'))
    kind = 'тест' if data.get('ticket_kind') == 'test' else 'экзамен'
    verdict = 'Сдал ✅' if passed else 'Не сдал ❌'
    lines = [
        '✅ <b>Экзамен записан</b>' if passed else '📝 <b>Результат записан</b>',
        f"Игрок: <b>{data.get('user_nick')}</b>",
        f"Билет: <b>{data.get('ticket_title')}</b> ({kind})",
        f"Итог: {verdict}",
    ]
    if passed and data.get('role_name'):
        lines.append(f"Звание: <b>{data.get('role_name')}</b>")
    if passed and data.get('position'):
        lines.append(f"Должность: <b>{data.get('position')}</b>")
    if data.get('note'):
        lines.append(f"Описание: {data.get('note')}")
    lines.append('\n📌 Результат отобразится в профиле игрока на сайте.')
    await target.answer('\n'.join(lines))
    await state.clear()


# ===================== Личный кабинет (ЛС) =====================

@dp.message(F.text == BTN_PROFILE)
async def btn_profile(m: Message, state: FSMContext):
    await state.clear()
    user = await require_linked(m)
    if not user:
        return
    try:
        p = await asyncio.to_thread(db.user_profile, user['id'])
    except Exception as e:
        await m.answer('Ошибка БД: ' + str(e))
        return
    if not p:
        await m.answer('Профиль не найден.')
        return
    lines = [
        f'👤 <b>{p["nick"]}</b>',
        f'Роль: <b>{p.get("role_name") or "—"}</b>',
    ]
    if p.get('position'):
        lines.append(f'Должность: {p["position"]}')
    if p.get('steam_id'):
        lines.append(f'Steam ID: <code>{p["steam_id"]}</code>')
    lines.append(f'Часов: <b>{int(p.get("hours_played") or 0)}</b> · Смен: <b>{int(p.get("shifts") or 0)}</b>')
    await m.answer('\n'.join(lines))


@dp.message(F.text == BTN_VIOLATIONS)
async def btn_violations(m: Message, state: FSMContext):
    await state.clear()
    user = await require_linked(m)
    if not user:
        return
    try:
        rows = await asyncio.to_thread(db.user_violations, user['id'])
    except Exception as e:
        await m.answer('Ошибка БД: ' + str(e))
        return
    if not rows:
        await m.answer('✅ Нарушений и талонов нет.')
        return
    out = ['⚠️ <b>Нарушения и талоны</b>', '']
    for v in rows:
        talon = TALON_NAMES.get(v.get('talon_color') or '', '')
        flag = ' <i>(талон забран)</i>' if int(v.get('revoked') or 0) else ''
        line = f'• {talon} <b>{v["type"]}</b>'
        if v.get('reason'):
            line += f' — {v["reason"]}'
        if v.get('points'):
            line += f' [{int(v["points"])} б.]'
        out.append(line + flag)
    await m.answer('\n'.join(out))


@dp.message(F.text == BTN_TESTS)
async def btn_tests(m: Message, state: FSMContext):
    await state.clear()
    user = await require_linked(m)
    if not user:
        return
    try:
        rows = await asyncio.to_thread(db.user_attempts, user['id'])
    except Exception as e:
        await m.answer('Ошибка БД: ' + str(e))
        return
    if not rows:
        await m.answer('Пока нет пройденных тестов и экзаменов.')
        return
    out = ['📝 <b>Мои тесты и экзамены</b>', '']
    for a in rows:
        st = a.get('status')
        mark = '✅' if st == 'passed' else ('❌' if st == 'failed' else '⏳')
        title = a.get('title') or 'Билет'
        score = ''
        if a.get('total'):
            score = f' ({int(a.get("score") or 0)}/{int(a["total"])})'
        out.append(f'{mark} {title}{score}')
    await m.answer('\n'.join(out))


@dp.message(F.text == BTN_REPORT)
async def btn_report(m: Message, state: FSMContext):
    await state.clear()
    if not await require_linked(m):
        return
    await state.set_state(DocFlow.report_body)
    await m.answer('🗒 <b>Отчёт об работе</b>\nОпишите проделанную работу одним сообщением:')


@dp.message(DocFlow.report_body)
async def report_body(m: Message, state: FSMContext):
    await state.update_data(body=(m.text or '').strip()[:4000])
    await state.set_state(DocFlow.report_hours)
    await m.answer('Сколько часов отработано? Введите число:')


@dp.message(DocFlow.report_hours)
async def report_hours(m: Message, state: FSMContext):
    data = await state.get_data()
    raw = (m.text or '').strip()
    hours = int(raw) if raw.isdigit() else 0
    user = await require_linked(m)
    if not user:
        await state.clear()
        return
    try:
        await asyncio.to_thread(db.add_report, user['id'], data.get('body', ''), hours)
    except Exception as e:
        await m.answer('Ошибка сохранения: ' + str(e))
        await state.clear()
        return
    await state.clear()
    await m.answer('✅ Отчёт отправлен на проверку.')


@dp.message(F.text == BTN_EXPLANATORY)
async def btn_explanatory(m: Message, state: FSMContext):
    await state.clear()
    if not await require_linked(m):
        return
    await m.answer('✍️ <b>Объяснительная</b>\nВыберите тип:', reply_markup=types_keyboard('expl', EXPLANATORY_TYPES))


@dp.message(F.text == BTN_APPLICATION)
async def btn_application(m: Message, state: FSMContext):
    await state.clear()
    if not await require_linked(m):
        return
    await m.answer('📄 <b>Заявление</b>\nВыберите тип:', reply_markup=types_keyboard('appl', APPLICATION_TYPES))


@dp.callback_query(F.data == 'doccancel')
async def doc_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await c.message.answer('Отменено.')
    await c.answer()


@dp.callback_query(F.data.startswith('expl:'))
async def expl_type(c: CallbackQuery, state: FSMContext):
    idx = int(c.data.split(':')[1])
    t = EXPLANATORY_TYPES[idx] if 0 <= idx < len(EXPLANATORY_TYPES) else EXPLANATORY_TYPES[0]
    await state.update_data(doc_type=t)
    await state.set_state(DocFlow.expl_body)
    await c.message.answer(f'Тип: <b>{t}</b>\nНапишите текст объяснительной одним сообщением:')
    await c.answer()


@dp.message(DocFlow.expl_body)
async def expl_body(m: Message, state: FSMContext):
    data = await state.get_data()
    user = await require_linked(m)
    if not user:
        await state.clear()
        return
    try:
        await asyncio.to_thread(db.add_explanatory, user['id'], data.get('doc_type', EXPLANATORY_TYPES[0]), (m.text or '').strip()[:4000])
    except Exception as e:
        await m.answer('Ошибка сохранения: ' + str(e))
        await state.clear()
        return
    await state.clear()
    await m.answer('✅ Объяснительная отправлена на проверку.')


@dp.callback_query(F.data.startswith('appl:'))
async def appl_type(c: CallbackQuery, state: FSMContext):
    idx = int(c.data.split(':')[1])
    t = APPLICATION_TYPES[idx] if 0 <= idx < len(APPLICATION_TYPES) else APPLICATION_TYPES[0]
    await state.update_data(doc_type=t)
    await state.set_state(DocFlow.appl_body)
    await c.message.answer(f'Тип: <b>{t}</b>\nНапишите текст заявления одним сообщением:')
    await c.answer()


@dp.message(DocFlow.appl_body)
async def appl_body(m: Message, state: FSMContext):
    data = await state.get_data()
    user = await require_linked(m)
    if not user:
        await state.clear()
        return
    try:
        await asyncio.to_thread(db.add_application, user['id'], data.get('doc_type', APPLICATION_TYPES[0]), (m.text or '').strip()[:4000])
    except Exception as e:
        await m.answer('Ошибка сохранения: ' + str(e))
        await state.clear()
        return
    await state.clear()
    await m.answer('✅ Заявление отправлено на проверку.')


async def setup_commands(bot: Bot):
    """Регистрирует меню команд и в личке, и в группах."""
    private_cmds = [
        BotCommand(command='start', description='Привязать аккаунт / меню'),
        BotCommand(command='exam', description='Провести экзамен'),
        BotCommand(command='stats', description='Сводка по БД'),
        BotCommand(command='export', description='Выгрузка таблиц в Excel'),
        BotCommand(command='help', description='Справка'),
    ]
    group_cmds = [
        BotCommand(command='stats', description='Сводка по БД'),
        BotCommand(command='export', description='Выгрузка таблиц в Excel'),
        BotCommand(command='help', description='Справка'),
    ]
    await bot.set_my_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_cmds, scope=BotCommandScopeAllGroupChats())
    await bot.set_my_commands(private_cmds, scope=BotCommandScopeDefault())
    log.info('Команды зарегистрированы (личка + группы)')


# ===================== Модерация из бота (заявки/объяснительные/отчёты) =====================

REF_WORD = {'application': 'Заявка', 'explanatory': 'Объяснительная', 'report': 'Отчёт'}


def action_keyboard(ref_type: str, ref_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='✅ Одобрить', callback_data=f'mod:approve:{ref_type}:{ref_id}'),
        InlineKeyboardButton(text='❌ Отклонить', callback_data=f'mod:reject:{ref_type}:{ref_id}'),
    ]])


async def revoke_ref_messages(bot: Bot, ref_type: str, ref_id: int):
    """Удаляет все сообщения с кнопками по сущности во всех чатах."""
    rows = await asyncio.to_thread(db.sent_messages_for_ref, ref_type, ref_id)
    for r in rows:
        try:
            await bot.delete_message(int(r['chat_id']), int(r['message_id']))
        except Exception:
            # Если удалить нельзя (старое) — хотя бы убираем кнопки.
            try:
                await bot.edit_message_reply_markup(int(r['chat_id']), int(r['message_id']), reply_markup=None)
            except Exception:
                pass
    await asyncio.to_thread(db.clear_sent_messages, ref_type, ref_id)


@dp.callback_query(F.data.startswith('mod:'))
async def mod_decision(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer('Недостаточно прав.', show_alert=True)
        return
    parts = (c.data or '').split(':')
    if len(parts) != 4:
        await c.answer()
        return
    _, decision_key, ref_type, ref_id_s = parts
    try:
        ref_id = int(ref_id_s)
    except ValueError:
        await c.answer()
        return
    if ref_type not in REF_WORD:
        await c.answer()
        return
    decision = 'approved' if decision_key == 'approve' else 'rejected'
    reviewer = await asyncio.to_thread(db.linked_user, c.from_user.id)
    reviewer_id = reviewer['id'] if reviewer else None
    ok, owner_id, doc = await asyncio.to_thread(db.review_doc, ref_type, ref_id, decision, reviewer_id, None)
    word = REF_WORD.get(ref_type, 'Документ')
    if not ok:
        # Уже рассмотрено (напр. на сайте) — просто убираем кнопки.
        await revoke_ref_messages(c.bot, ref_type, ref_id)
        await c.answer('Уже рассмотрено.', show_alert=True)
        return
    # Убираем кнопки/сообщения у всех админов.
    await revoke_ref_messages(c.bot, ref_type, ref_id)
    # Уведомляем сотрудника.
    if owner_id:
        chat = await asyncio.to_thread(db.user_chat_id, owner_id)
        if chat:
            if ref_type == 'report':
                hours = (doc or {}).get('hours') or 0
                umsg = (f'✅ <b>Актив учтён</b>\nОтчёт одобрен (+{hours} ч.).'
                        if decision == 'approved' else '❌ <b>Отчёт отклонён</b>.')
            else:
                res = '✅ одобрена' if decision == 'approved' else '❌ отклонена'
                dtype = (doc or {}).get('type') or ''
                umsg = f'{word} (<b>{dtype}</b>) {res}.'
            try:
                await c.bot.send_message(int(chat), umsg)
            except Exception:
                pass
    # Уведомляем админа, кто нажал.
    nick = (doc or {}).get('nick') or ''
    res_admin = 'одобрено ✅' if decision == 'approved' else 'отклонено ❌'
    try:
        await c.message.answer(f'{word} от <b>{nick}</b> — {res_admin}.')
    except Exception:
        pass
    await c.answer('Готово.')


async def outbox_loop(bot: Bot):
    """Опрашивает очередь bot_outbox и разбирает три типа сообщений:
      - text   : простое уведомление (chat_id пуст — всем админам);
      - action : уведомление с кнопками Одобрить/Отклонить (заявки и т.п.);
      - revoke : удалить ранее отправленные сообщения (уже рассмотрели на сайте)."""
    await asyncio.sleep(5)
    while True:
        try:
            rows = await asyncio.to_thread(db.pop_outbox, 10)
            sent_ids = []
            for row in rows:
                kind = row.get('kind') or 'text'
                ref_type = row.get('ref_type')
                ref_id = row.get('ref_id')
                text = row.get('text') or ''
                chat_id = row.get('chat_id')
                try:
                    if kind == 'revoke' and ref_type and ref_id:
                        await revoke_ref_messages(bot, ref_type, int(ref_id))
                    elif kind == 'action' and ref_type and ref_id:
                        kb = action_keyboard(ref_type, int(ref_id))
                        targets = [chat_id] if chat_id else list(config.ADMIN_IDS)
                        for tgt in targets:
                            if not tgt:
                                continue
                            try:
                                msg = await bot.send_message(int(tgt), text, reply_markup=kb)
                                await asyncio.to_thread(db.record_sent_message, ref_type, int(ref_id), int(tgt), int(msg.message_id))
                            except Exception as e:
                                log.error('outbox action send failed to %s: %s', tgt, e)
                    else:
                        targets = [chat_id] if chat_id else list(config.ADMIN_IDS)
                        for tgt in targets:
                            if not tgt:
                                continue
                            try:
                                await bot.send_message(int(tgt), text)
                            except Exception as e:
                                log.error('outbox send failed to %s: %s', tgt, e)
                except Exception as e:
                    log.error('outbox row %s failed: %s', row.get('id'), e)
                # Помечаем обработанным в любом случае, чтобы не зацикливаться.
                sent_ids.append(row['id'])
            if sent_ids:
                await asyncio.to_thread(db.mark_outbox_sent, sent_ids)
        except Exception as e:
            log.error('outbox loop error: %s', e)
        await asyncio.sleep(6)


async def auto_export_loop(bot: Bot):
    if not config.EXPORT_CHAT_ID or config.EXPORT_INTERVAL_MIN <= 0:
        return
    await asyncio.sleep(10)
    while True:
        try:
            path = await asyncio.to_thread(exporter.build_workbook)
            await bot.send_document(config.EXPORT_CHAT_ID, FSInputFile(path), caption='📑 Авто-выгрузка БД')
        except Exception as e:
            log.error('auto export failed: %s', e)
        await asyncio.sleep(config.EXPORT_INTERVAL_MIN * 60)


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit('Укажите TG_BOT_TOKEN в bot/.env')

    log.info('Bot starting (proxy: %s)', config.TG_PROXY or 'нет')

    while True:
        bot = build_bot()
        export_task = None
        try:
            me = await bot.get_me()
            log.info('Подключился как @%s', me.username)
            await setup_commands(bot)
            export_task = asyncio.create_task(auto_export_loop(bot))
            outbox_task = asyncio.create_task(outbox_loop(bot))
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        except TelegramNetworkError as e:
            log.error('Сеть недоступна (%s). Повтор через 15 сек…', e)
            await asyncio.sleep(15)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            log.exception('Неожиданная ошибка: %s. Повтор через 15 сек…', e)
            await asyncio.sleep(15)
        finally:
            if export_task:
                export_task.cancel()
            if 'outbox_task' in dir() or 'outbox_task' in locals():
                try:
                    outbox_task.cancel()
                except Exception:
                    pass
            await bot.session.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
