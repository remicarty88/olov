import asyncio
import logging
import sys
import io
import uuid
from datetime import datetime

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import cm

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

# Попытка найти системный шрифт с поддержкой кириллицы
registered_font = None

def register_fonts():
    global registered_font
    # Пути к шрифтам на macOS
    font_paths = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Cache/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf" # Linux
    ]
    
    for path in font_paths:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('CombinedFont', path))
                registered_font = 'CombinedFont'
                return
            except:
                continue

register_fonts()

import firebase_admin
from firebase_admin import credentials, db
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    BufferedInputFile,
    ReplyKeyboardRemove
)

# --- CONFIGURATION ---
API_TOKEN = "8752266798:AAFlBiA2F9_xlmymr6yFK0ENzZ9fHiwZutA"
ADMIN_ID = 6201234513
FIREBASE_DATABASE_URL = "https://neonapp-a05b0-default-rtdb.firebaseio.com/"
# Path to your Firebase service account JSON file
FIREBASE_SERVICE_ACCOUNT_JSON = "neonapp-a05b0-firebase-adminsdk-evh7r-45fcbe3067.json"

# --- FIREBASE INIT ---
try:
    cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_JSON)
    firebase_admin.initialize_app(cred, {
        'databaseURL': FIREBASE_DATABASE_URL
    })
except Exception as e:
    print(f"Error initializing Firebase: {e}")
    # For local development without the file, we continue but DB calls will fail.
    # In production, ensure firebase-sdk.json is present.

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# --- FSM STATES ---
class Form(StatesGroup):
    waiting_for_access_code = State()
    waiting_for_clear_code = State()
    # Add Supplier
    waiting_for_supplier_name = State()
    # Add Debt
    waiting_for_debt_supplier = State()
    waiting_for_debt_amount = State()
    waiting_for_debt_date = State()
    # Payment
    waiting_for_payment_debt = State()
    waiting_for_payment_amount = State()

# --- KEYBOARDS ---
def get_main_menu(user_id=None):
    buttons = [
        [KeyboardButton(text="📦 Поставщики"), KeyboardButton(text="➕ Добавить поставщика")],
        [KeyboardButton(text="💰 Добавить долг"), KeyboardButton(text="📊 Долги")],
        [KeyboardButton(text="💸 Оплатить"), KeyboardButton(text="🧾 История")],
        [KeyboardButton(text="📥 Экспорт Excel"), KeyboardButton(text="📄 Экспорт PDF")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton(text="🗑 Очистить базу")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

# --- BOT INIT ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- HELPERS ---
def get_user_ref(user_id=None):
    try:
        # Теперь все пользователи используют одну ветку 'shared_data'
        return db.reference("shared_data")
    except ValueError:
        return None

def format_uzs(amount):
    return f"{amount:,.0f}".replace(",", " ") + " UZS"

def format_date(date_str=None):
    if not date_str:
        return datetime.now().strftime("%d.%m.%Y")
    return date_str

# --- HANDLERS ---

@dp.message(Command("start"))
@dp.message(F.text == "❌ Отмена")
async def cmd_start(message: types.Message, state: FSMContext):
    logging.info(f"User {message.from_user.id} triggered /start or Cancel")
    await state.clear()
    await state.set_state(Form.waiting_for_access_code)
    await message.answer(
        "� Пожалуйста, введите код доступа для входа в бота:",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(Form.waiting_for_access_code)
async def check_access_code(message: types.Message, state: FSMContext):
    if message.text == "1188":
        await state.clear()
        await message.answer(
            "👋 Добро пожаловать в OLOV! Выберите действие в меню ниже:",
            reply_markup=get_main_menu(message.from_user.id)
        )
    else:
        await message.answer("❌ Неверный код. Попробуйте еще раз:")

# --- 🗑 АДМИН: ОЧИСТКА ---
@dp.message(F.text == "🗑 Очистить базу")
async def admin_clear_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(Form.waiting_for_clear_code)
    await message.answer("⚠️ Введите код подтверждения для полной очистки базы данных (777):", reply_markup=get_cancel_keyboard())

@dp.message(Form.waiting_for_clear_code)
async def admin_clear_finish(message: types.Message, state: FSMContext):
    if message.text == "777":
        user_ref = get_user_ref()
        if user_ref:
            user_ref.delete()
            await state.clear()
            await message.answer("🔥 База данных полностью очищена!", reply_markup=get_main_menu(message.from_user.id))
        else:
            await message.answer("⚠️ Ошибка: База данных не инициализирована.")
    else:
        await message.answer("❌ Неверный код подтверждения. Очистка отменена.", reply_markup=get_main_menu(message.from_user.id))
        await state.clear()

# --- 📦 ПОСТАВЩИКИ ---
@dp.message(F.text == "📦 Поставщики")
async def list_suppliers(message: types.Message):
    user_id = message.from_user.id
    user_ref = get_user_ref(user_id)
    if not user_ref:
        await message.answer("⚠️ Ошибка: База данных не инициализирована (отсутствует firebase-sdk.json).")
        return
        
    suppliers = user_ref.child("suppliers").get()
    
    if not suppliers:
        await message.answer("ℹ️ У вас пока нет поставщиков. Добавьте первого!")
        return
    
    text = "📋 **Список поставщиков:**\n\n"
    for s_id, data in suppliers.items():
        text += f"• {data.get('name')} (Создан: {data.get('createdAt')})\n"
    
    await message.answer(text, parse_mode="Markdown")

# --- ➕ ДОБАВИТЬ ПОСТАВЩИКА ---
@dp.message(F.text == "➕ Добавить поставщика")
async def add_supplier_start(message: types.Message, state: FSMContext):
    await state.set_state(Form.waiting_for_supplier_name)
    await message.answer("📝 Введите название поставщика:", reply_markup=get_cancel_keyboard())

@dp.message(Form.waiting_for_supplier_name)
async def add_supplier_finish(message: types.Message, state: FSMContext):
    name = message.text.strip()
    user_id = message.from_user.id
    
    user_ref = get_user_ref(user_id)
    if not user_ref:
        await message.answer("⚠️ Ошибка: База данных не инициализирована.")
        await state.clear()
        return

    supplier_id = str(uuid.uuid4())[:8]
    data = {
        "name": name,
        "createdAt": format_date()
    }
    
    user_ref.child("suppliers").child(supplier_id).set(data)
    
    await state.clear()
    await message.answer(f"✅ Поставщик '{name}' успешно добавлен!", reply_markup=get_main_menu())

@dp.message(F.text == "💰 Добавить долг")
async def add_debt_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_ref = get_user_ref(user_id)
    if not user_ref:
        await message.answer("⚠️ Ошибка: База данных не инициализирована.")
        return

    suppliers = user_ref.child("suppliers").get()
    
    if not suppliers:
        await message.answer("❌ Сначала добавьте хотя бы одного поставщика!")
        return
    
    await state.set_state(Form.waiting_for_debt_supplier)
    
    # Create buttons for each supplier
    supplier_buttons = [[KeyboardButton(text=s['name'])] for s in suppliers.values()]
    supplier_buttons.append([KeyboardButton(text="❌ Отмена")])
    
    kb = ReplyKeyboardMarkup(keyboard=supplier_buttons, resize_keyboard=True)
    await message.answer("🏢 Выберите поставщика из списка:", reply_markup=kb)

@dp.message(Form.waiting_for_debt_supplier)
async def add_debt_supplier(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_ref = get_user_ref(user_id)
    if not user_ref:
        await message.answer("⚠️ Ошибка: База данных не инициализирована.")
        await state.clear()
        return

    suppliers = user_ref.child("suppliers").get()
    
    # Find supplier ID by name
    supplier_id = None
    supplier_name = message.text
    for s_id, data in suppliers.items():
        if data['name'] == supplier_name:
            supplier_id = s_id
            break
            
    if not supplier_id:
        await message.answer("⚠️ Пожалуйста, выберите поставщика из предложенных кнопок.")
        return
        
    await state.update_data(supplier_id=supplier_id, supplier_name=supplier_name)
    await state.set_state(Form.waiting_for_debt_amount)
    await message.answer(f"💰 Введите сумму долга для {supplier_name}:", reply_markup=get_cancel_keyboard())

def parse_amount(text):
    # Убираем пробелы, точки и заменяем запятую на точку для float
    clean_text = text.replace(" ", "").replace("\xa0", "").replace(";", "").replace(".", "").replace(",", ".")
    return float(clean_text)

@dp.message(Form.waiting_for_debt_amount)
async def add_debt_amount(message: types.Message, state: FSMContext):
    try:
        amount = parse_amount(message.text)
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer("⚠️ Пожалуйста, введите корректное положительное число (можно с пробелами, например: 5 000 000).")
        return
        
    await state.update_data(amount=amount)
    await state.set_state(Form.waiting_for_debt_date)
    await message.answer(f"💰 Сумма: {format_uzs(amount)}\n📅 Введите дату (например, 01.01.2024) или нажмите кнопку ниже, чтобы использовать текущую:", 
                         reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Сегодня")], [KeyboardButton(text="❌ Отмена")]], resize_keyboard=True))

@dp.message(Form.waiting_for_debt_date)
async def add_debt_finish(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    date_val = message.text
    
    if date_val == "Сегодня":
        date_val = format_date()
        
    user_id = message.from_user.id
    debt_id = str(uuid.uuid4())[:8]
    
    user_ref = get_user_ref(user_id)
    if not user_ref:
        await message.answer("⚠️ Ошибка: База данных не инициализирована.")
        await state.clear()
        return

    debt_data = {
        "supplierId": user_data['supplier_id'],
        "totalAmount": user_data['amount'],
        "paidAmount": 0,
        "createdAt": date_val,
        "status": "active"
    }
    
    user_ref.child("debts").child(debt_id).set(debt_data)
    
    await state.clear()
    await message.answer(f"✅ Долг на сумму {format_uzs(user_data['amount'])} для {user_data['supplier_name']} добавлен!", reply_markup=get_main_menu())

# --- 📊 ДОЛГИ ---
@dp.message(F.text == "📊 Долги")
async def show_debts(message: types.Message):
    user_id = message.from_user.id
    user_ref = get_user_ref(user_id)
    if not user_ref:
        await message.answer("⚠️ Ошибка: База данных не инициализирована.")
        return

    suppliers = user_ref.child("suppliers").get() or {}
    debts = user_ref.child("debts").get() or {}
    
    if not debts:
        await message.answer("ℹ️ У вас пока нет активных долгов.")
        return
        
    text = "📊 **Ваши долги:**\n\n"
    for d_id, d_data in debts.items():
        s_name = suppliers.get(d_data['supplierId'], {}).get('name', 'Неизвестен')
        total = d_data['totalAmount']
        paid = d_data['paidAmount']
        remains = total - paid
        status_emoji = "⏳" if d_data['status'] == "active" else "✅"
        
        text += f"📦 **Поставщик:** {s_name}\n"
        text += f"💰 **Долг:** {format_uzs(total)}\n"
        text += f"✅ **Оплачено:** {format_uzs(paid)}\n"
        text += f"📉 **Остаток:** {format_uzs(remains)}\n"
        text += f"📅 **Дата:** {d_data['createdAt']}\n"
        text += f"📌 **Статус:** {d_data['status']} {status_emoji}\n"
        text += "------------------------\n"
        
    await message.answer(text, parse_mode="Markdown")

# --- 💸 ОПЛАТИТЬ ---
@dp.message(F.text == "💸 Оплатить")
async def pay_debt_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_ref = get_user_ref(user_id)
    
    debts = user_ref.child("debts").get() or {}
    suppliers = user_ref.child("suppliers").get() or {}
    
    # Only active debts
    active_debts = {k: v for k, v in debts.items() if v['status'] == 'active'}
    
    if not active_debts:
        await message.answer("ℹ️ У вас нет активных долгов для оплаты.")
        return
        
    await state.set_state(Form.waiting_for_payment_debt)
    
    debt_buttons = []
    for d_id, d_data in active_debts.items():
        s_name = suppliers.get(d_data['supplierId'], {}).get('name', '???')
        remains = d_data['totalAmount'] - d_data['paidAmount']
        btn_text = f"{s_name} | {format_uzs(remains)} | ID:{d_id}"
        debt_buttons.append([KeyboardButton(text=btn_text)])
        
    debt_buttons.append([KeyboardButton(text="❌ Отмена")])
    kb = ReplyKeyboardMarkup(keyboard=debt_buttons, resize_keyboard=True)
    await message.answer("💸 Выберите долг для оплаты:", reply_markup=kb)

@dp.message(Form.waiting_for_payment_debt)
async def pay_debt_select(message: types.Message, state: FSMContext):
    text = message.text
    if "ID:" not in text:
        await message.answer("⚠️ Пожалуйста, выберите долг с помощью кнопок.")
        return
        
    debt_id = text.split("ID:")[-1].strip()
    user_id = message.from_user.id
    user_ref = get_user_ref(user_id)
    if not user_ref:
        await message.answer("⚠️ Ошибка: База данных не инициализирована.")
        await state.clear()
        return

    debt_data = user_ref.child("debts").child(debt_id).get()
    
    if not debt_data:
        await message.answer("⚠️ Ошибка: долг не найден.")
        return
        
    await state.update_data(debt_id=debt_id)
    await state.set_state(Form.waiting_for_payment_amount)
    
    remains = debt_data['totalAmount'] - debt_data['paidAmount']
    await message.answer(f"💰 Введите сумму оплаты (Остаток: {format_uzs(remains)}):", reply_markup=get_cancel_keyboard())

@dp.message(Form.waiting_for_payment_amount)
async def pay_debt_finish(message: types.Message, state: FSMContext):
    try:
        pay_amount = parse_amount(message.text)
        if pay_amount <= 0: raise ValueError
    except ValueError:
        await message.answer("⚠️ Пожалуйста, введите корректное положительное число (можно с пробелами, например: 1 000 000).")
        return
        
    user_data = await state.get_data()
    debt_id = user_data['debt_id']
    user_id = message.from_user.id
    
    user_ref = get_user_ref(user_id)
    debt_ref = user_ref.child("debts").child(debt_id)
    debt_data = debt_ref.get()
    
    new_paid = debt_data['paidAmount'] + pay_amount
    new_status = "closed" if new_paid >= debt_data['totalAmount'] else "active"
    
    # Update Debt
    debt_ref.update({
        "paidAmount": new_paid,
        "status": new_status
    })
    
    # Record Payment
    payment_id = str(uuid.uuid4())[:8]
    payment_data = {
        "debtId": debt_id,
        "amount": pay_amount,
        "createdAt": format_date()
    }
    user_ref.child("payments").child(payment_id).set(payment_data)
    
    await state.clear()
    status_msg = "✅ Долг полностью закрыт!" if new_status == "closed" else f"✅ Оплата принята. Новый остаток: {format_uzs(debt_data['totalAmount'] - new_paid)}"
    await message.answer(status_msg, reply_markup=get_main_menu())

# --- ЭКСПОРТ EXCEL ---
@dp.message(F.text == "📥 Экспорт Excel")
async def export_excel(message: types.Message):
    user_id = message.from_user.id
    user_ref = get_user_ref(user_id)
    if not user_ref:
        await message.answer("⚠️ Ошибка: База данных не инициализирована.")
        return

    debts = user_ref.child("debts").get() or {}
    suppliers = user_ref.child("suppliers").get() or {}
    
    if not debts:
        await message.answer("ℹ️ Нет данных для экспорта.")
        return

    data = []
    for d_id, d in debts.items():
        s_name = suppliers.get(d['supplierId'], {}).get('name', 'Неизвестен')
        data.append({
            "Поставщик": s_name,
            "Сумма долга": d['totalAmount'],
            "Оплачено": d['paidAmount'],
            "Остаток": d['totalAmount'] - d['paidAmount'],
            "Дата создания": d['createdAt'],
            "Статус": "Активен" if d['status'] == "active" else "Закрыт"
        })

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Долги')
    
    file_content = output.getvalue()
    await message.answer_document(
        BufferedInputFile(file_content, filename=f"debts_{datetime.now().strftime('%d_%m_%Y')}.xlsx"),
        caption="📊 Ваша выгрузка долгов в Excel"
    )

# --- ЭКСПОРТ PDF ---
@dp.message(F.text == "📄 Экспорт PDF")
async def export_pdf(message: types.Message):
    user_id = message.from_user.id
    user_ref = get_user_ref(user_id)
    if not user_ref:
        await message.answer("⚠️ Ошибка: База данных не инициализирована.")
        return

    debts = user_ref.child("debts").get() or {}
    suppliers = user_ref.child("suppliers").get() or {}
    
    if not debts:
        await message.answer("ℹ️ Нет данных для экспорта.")
        return

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    elements = []
    styles = getSampleStyleSheet()

    # Стили
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontName=PDF_FONT,
        fontSize=24,
        textColor=colors.HexColor("#FF4500"), # Оранжево-красный (OLOV)
        alignment=1, # Center
        spaceAfter=10
    )
    subtitle_style = ParagraphStyle(
        'SubtitleStyle',
        parent=styles['Normal'],
        fontName=PDF_FONT,
        fontSize=10,
        textColor=colors.grey,
        alignment=1,
        spaceAfter=30
    )

    # Заголовок OLOV
    elements.append(Paragraph("OLOV", title_style))
    elements.append(Paragraph(f"ОТЧЕТ ПО ЗАДОЛЖЕННОСТЯМ ОТ {datetime.now().strftime('%d.%m.%Y')}", subtitle_style))

    # Данные для таблицы
    table_data = [["Поставщик", "Сумма долга", "Оплачено", "Остаток", "Статус"]]
    
    total_debt = 0
    total_remains = 0

    for d_id, d in debts.items():
        s_name = suppliers.get(d['supplierId'], {}).get('name', 'Неизвестен')
        remains = d['totalAmount'] - d['paidAmount']
        status_text = "Активен" if d['status'] == "active" else "Закрыт"
        
        table_data.append([
            str(s_name),
            format_uzs(d['totalAmount']),
            format_uzs(d['paidAmount']),
            format_uzs(remains),
            status_text
        ])
        
        total_debt += d['totalAmount']
        total_remains += remains

    # Таблица
    t = Table(table_data, colWidths=[5*cm, 3.5*cm, 3.5*cm, 3.5*cm, 2*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#FF4500")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), PDF_FONT),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 20))

    # Итого
    total_style = ParagraphStyle('TotalStyle', parent=styles['Normal'], fontName=PDF_FONT, fontSize=12, alignment=2) # Right
    elements.append(Paragraph(f"<b>ИТОГО ДОЛГОВ: {format_uzs(total_debt)}</b>", total_style))
    elements.append(Paragraph(f"<b>ИТОГО К ОПЛАТЕ: {format_uzs(total_remains)}</b>", total_style))

    # Генерация
    doc.build(elements)
    
    pdf_content = buffer.getvalue()
    await message.answer_document(
        BufferedInputFile(pdf_content, filename=f"OLOV_Report_{datetime.now().strftime('%d_%m_%Y')}.pdf"),
        caption="📄 Отчет OLOV"
    )
@dp.message(F.text == "🧾 История")
async def show_history(message: types.Message):
    user_id = message.from_user.id
    user_ref = get_user_ref(user_id)
    
    payments = user_ref.child("payments").get() or {}
    debts = user_ref.child("debts").get() or {}
    suppliers = user_ref.child("suppliers").get() or {}
    
    if not payments:
        await message.answer("ℹ️ История платежей пуста.")
        return
        
    text = "🧾 **История платежей:**\n\n"
    # Sort by date (naive string sort, but better than nothing for this example)
    sorted_payments = sorted(payments.values(), key=lambda x: x['createdAt'], reverse=True)
    
    for p in sorted_payments:
        debt_data = debts.get(p['debtId'], {})
        s_id = debt_data.get('supplierId')
        s_name = suppliers.get(s_id, {}).get('name', 'Неизвестен')
        
        text += f"📅 {p['createdAt']} | 💰 {format_uzs(p['amount'])} | 📦 {s_name}\n"
        
    await message.answer(text, parse_mode="Markdown")

# --- MAIN ---
async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот выключен.")

'''
ИНСТРУКЦИЯ ПО ЗАПУСКУ:

1. Установите библиотеки:
   pip install aiogram firebase-admin

2. Получите TOKEN у @BotFather в Telegram и вставьте его в переменную API_TOKEN.

3. Настройте Firebase:
   - Создайте проект на https://console.firebase.google.com/
   - Включите Realtime Database.
   - Перейдите в "Project Settings" -> "Service Accounts".
   - Нажмите "Generate new private key" и скачайте JSON-файл.
   - Переименуйте его в 'firebase-sdk.json' и положите в папку с ботом (или укажите путь в FIREBASE_SERVICE_ACCOUNT_JSON).

4. Запустите бота:
   python bot.py
'''
