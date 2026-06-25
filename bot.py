import os
import sys
import json
import random
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError

from supabase import create_client, Client

# 1. LOGGING SETUP
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# 2. CONFIGURATION & ENVIRONMENT VARIABLES
BOT_TOKEN: Optional[str] = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW: str = os.getenv("ADMIN_ID", "1973341892")
SUPABASE_URL: Optional[str] = os.getenv("SUPABASE_URL")
SUPABASE_KEY: Optional[str] = os.getenv("SUPABASE_KEY")

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    logger.critical("Muhit o'zgaruvchilari (Environment Variables) to'liq emas! Bot to'xtatiladi.")
    sys.exit("Error: Missing required environment variables.")

# Supabase URL formatini to'g'rilash (Agar noto'g'ri kiritilgan bo'lsa)
if "/rest/v1" in SUPABASE_URL:
    SUPABASE_URL = SUPABASE_URL.split("/rest/v1")[0]

try:
    ADMIN_ID: int = int(ADMIN_ID_RAW.strip())
    logger.info(f"Admin muvaffaqiyatli yuklandi ID: {ADMIN_ID}")
except ValueError:
    ADMIN_ID = 1973341892
    logger.warning(f"ADMIN_ID xato formatda, standart qiymat yuklandi: {ADMIN_ID}")

# 3. INITIALIZE CLIENTS
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
JSON_FILE_PATH = "murojaatlar.json"

# Global Ma'lumotlar Ro'yxati
MFY_LIST: List[str] = [
    "Boyovut", "Terakzor", "Oltin Vodiy", "Furqat", "Sharq Haqiqati", "Sohilobod",
    "Ibrat", "Dostlik", "Ahillik", "A.Yassaviy", "Beshbuloq", "Inoqlik",
    "Chortoq", "A.Navoiy", "Birlashgan", "Zarbdor", "Ishonch", "Baxmal",
    "Yulduz", "Mevazor", "Soyibobod", "Mustaqillik", "Tajribakor", "H.Olimjon"
]

CATEGORIES: List[str] = [
    "Moddiy yordam", "Nogironlik va TIEK", "Reabilitatsiya",
    "Sanatoriy", "Vasiylik", "Sayyor xizmatlar"
]

# 4. KEYBOARDS & NAVIGATION (NameError bo'lmasligi uchun tepaga ko'chirildi)
def get_mfy_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    items_per_page = 6
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    sliced_mfy = MFY_LIST[start_idx:end_idx]
    
    buttons = []
    for mfy in sliced_mfy:
        buttons.append([InlineKeyboardButton(text=mfy, callback_data=f"mfy_sel:{mfy}")])
        
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"mfy_page:{page-1}"))
    if end_idx < len(MFY_LIST):
        nav_row.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"mfy_page:{page+1}"))
        
    if nav_row:
        buttons.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_category_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=cat, callback_data=f"cat_sel:{cat}")] for cat in CATEGORIES]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_action_keyboard(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Qabul qilish", callback_data=f"adm_accept:{app_id}")],
        [InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm_reject:{app_id}")],
        [InlineKeyboardButton(text="🏁 Tugatish (Hal etildi)", callback_data=f"adm_resolve:{app_id}")]
    ])

# 5. UTILITY FUNCTIONS (Markdown Escape & DB Operations)
def escape_markdown(text: Any) -> str:
    if not text:
        return ""
    text_str = str(text)
    for char in ['*', '_', '`']:
        text_str = text_str.replace(char, f"\\{char}")
    return text_str

def save_to_json_local(data: Dict[str, Any]) -> None:
    try:
        all_records = []
        if os.path.exists(JSON_FILE_PATH):
            with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
                try:
                    all_records = json.load(f)
                    if not isinstance(all_records, list):
                        all_records = []
                except json.JSONDecodeError:
                    all_records = []
        
        existing_idx = next((i for i, item in enumerate(all_records) if item.get("application_id") == data.get("application_id")), None)
        if existing_idx is not None:
            all_records[existing_idx] = data
        else:
            all_records.append(data)
            
        with open(JSON_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(all_records, f, ensure_ascii=False, indent=4)
        logger.info(f"Mahalliy zaxira yangilandi: ID {data.get('application_id')}")
    except Exception as e:
        logger.error(f"JSON faylga yozishda xatolik: {e}")

# ASYNC EXECUTORS FOR HYBRID BACKUP
async def sync_supabase_insert(data: Dict[str, Any]) -> Any:
    return supabase_client.table("murojaatlar").insert(data).execute()

async def sync_supabase_update(app_id: int, updates: Dict[str, Any]) -> Any:
    return supabase_client.table("murojaatlar").update(updates).eq("application_id", app_id).execute()

async def sync_supabase_select_by_id(app_id: int) -> Any:
    return supabase_client.table("murojaatlar").select("*").eq("application_id", app_id).execute()

async def sync_supabase_select_by_user(user_id: int) -> Any:
    return supabase_client.table("murojaatlar").select("*").eq("user_id", user_id).execute()

async def save_application_db(data: Dict[str, Any]) -> bool:
    save_to_json_local(data)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, lambda: asyncio.run(sync_supabase_insert(data)))
        logger.info(f"Supabase-ga muvaffaqiyatli saqlandi: ID {data['application_id']}")
        return True
    except Exception as e:
        logger.error(f"Supabase-ga insert qilishda xatolik yuz berdi (Bot ishlashda davom etadi): {e}")
        return False

async def update_application_db(app_id: int, updates: Dict[str, Any]) -> bool:
    loop = asyncio.get_event_loop()
    try:
        if os.path.exists(JSON_FILE_PATH):
            with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
                records = json.load(f)
            for item in records:
                if item.get("application_id") == app_id:
                    item.update(updates)
            with open(JSON_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=4)
        
        await loop.run_in_executor(None, lambda: asyncio.run(sync_supabase_update(app_id, updates)))
        return True
    except Exception as e:
        logger.error(f"Ma'lumotni yangilashda xatolik: {e}")
        return False

async def get_application_from_db(app_id: int) -> Optional[Dict[str, Any]]:
    loop = asyncio.get_event_loop()
    try:
        res = await loop.run_in_executor(None, lambda: asyncio.run(sync_supabase_select_by_id(app_id)))
        if res and hasattr(res, 'data') and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        logger.warning(f"Supabase-dan o'qishda xatolik, JSON-dan qidirilmoqda: {e}")
    
    if os.path.exists(JSON_FILE_PATH):
        try:
            with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
                records = json.load(f)
                for item in records:
                    if item.get("application_id") == app_id:
                        return item
        except Exception as json_err:
            logger.error(f"JSON-dan o'qishda xatolik: {json_err}")
    return None

async def generate_unique_id() -> int:
    loop = asyncio.get_event_loop()
    for _ in range(50):
        candidate = random.randint(1000, 9999)
        try:
            res = await loop.run_in_executor(None, lambda: asyncio.run(sync_supabase_select_by_id(candidate)))
            if not res.data:
                return candidate
        except Exception:
            if os.path.exists(JSON_FILE_PATH):
                with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
                    records = json.load(f)
                if not any(i.get("application_id") == candidate for i in records):
                    return candidate
            else:
                return candidate
    return random.randint(1000, 9999)

# 6. FSM STATES DEFINITION
class MurojaatJarayoni(StatesGroup):
    FISH = State()
    MFY = State()
    Toifa = State()
    Telefon = State()
    QoshimchaAloqa = State()
    Matn = State()

class AdminJarayoni(StatesGroup):
    RadSababi = State()

# 7. USER HANDLERS
@router.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Assalomu alaykum! Murojaat qabul qilish botiga xush kelibsiz.\n\n"
        "Arizangizni boshlash uchun iltimos, **F.I.SH (Familiya, Ism, Sharif)**ingizni kiriting:",
        parse_mode="Markdown"
    )
    await state.set_state(MurojaatJarayoni.FISH)

@router.message(MurojaatJarayoni.FISH)
async def process_fish(message: Message, state: FSMContext):
    if len(message.text.strip()) < 3:
        await message.answer("❌ F.I.SH juda qisqa. Iltimos, kamida 3 ta harfdan iborat to'liq ismingizni yozing:")
        return
    await state.update_data(fish=message.text.strip())
    await message.answer("Yashash MFY (Mahalla fuqarolar yig'ini)ni tanlang:", reply_markup=get_mfy_keyboard(0))
    await state.set_state(MurojaatJarayoni.MFY)

@router.callback_query(F.data.startswith("mfy_page:"), MurojaatJarayoni.MFY)
async def process_mfy_pagination(callback: CallbackQuery):
    page = int(callback.data.split(":")[1])
    await callback.message.edit_reply_markup(reply_markup=get_mfy_keyboard(page))
    await callback.answer()

@router.callback_query(F.data.startswith("mfy_sel:"), MurojaatJarayoni.MFY)
async def process_mfy_selection(callback: CallbackQuery, state: FSMContext):
    mfy_name = callback.data.split(":")[1]
    await state.update_data(mfy=mfy_name)
    await callback.message.edit_text(f"Tanlangan MFY: **{mfy_name}**\n\nEndi murojaatingiz toifasini tanlang:", reply_markup=get_category_keyboard(), parse_mode="Markdown")
    await state.set_state(MurojaatJarayoni.Toifa)
    await callback.answer()

@router.callback_query(F.data.startswith("cat_sel:"), MurojaatJarayoni.Toifa)
async def process_category_selection(callback: CallbackQuery, state: FSMContext):
    category_name = callback.data.split(":")[1]
    await state.update_data(category=category_name)
    
    phone_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Kontakt yuborish", request_contact=True)],
            [KeyboardButton(text="⏭ O'tkazish")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await callback.message.delete()
    await callback.message.answer(
        f"Tanlangan toifa: **{category_name}**\n\nIltimos, telefon raqamingizni pastdagi tugma orqali yuboring yoki o'tkazib yuboring:",
        reply_markup=phone_keyboard,
        parse_mode="Markdown"
    )
    await state.set_state(MurojaatJarayoni.Telefon)
    await callback.answer()

@router.message(MurojaatJarayoni.Telefon)
async def process_phone(message: Message, state: FSMContext):
    if message.contact:
        phone = message.contact.phone_number
    elif message.text == "⏭ O'tkazish":
        phone = "Kiritilmadi"
    else:
        phone = message.text.strip()
        
    await state.update_data(phone=phone)
    await message.answer(
        "Qo'shimcha telefon raqami yoki bog'lanish uchun boshqa ma'lumotlarni kiriting (Agar kerak bo'lmasa 'Yo'q' deb yozing):",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(MurojaatJarayoni.QoshimchaAloqa)

@router.message(MurojaatJarayoni.QoshimchaAloqa)
async def process_additional_contact(message: Message, state: FSMContext):
    await state.update_data(additional_contact=message.text.strip())
    await message.answer("Batafsil murojaat matnini kiriting (Kamida 5 ta belgi bo'lishi shart):")
    await state.set_state(MurojaatJarayoni.Matn)

@router.message(MurojaatJarayoni.Matn)
async def process_final_text_and_save(message: Message, state: FSMContext):
    text_content = message.text.strip()
    if len(text_content) < 5:
        await message.answer("❌ Murojaat matni juda qisqa! Iltimos, batafsilroq yozing (kamida 5 ta belgi):")
        return
        
    user_data = await state.get_data()
    await state.clear()
    
    app_id = await generate_unique_id()
    current_time = datetime.now().isoformat()
    
    payload = {
        "application_id": app_id,
        "user_id": message.from_user.id,
        "user_handle": message.from_user.username or "Kiritilmadi",
        "fish": user_data["fish"],
        "mfy": user_data["mfy"],
        "category": user_data["category"],
        "phone": user_data["phone"],
        "additional_contact": user_data["additional_contact"],
        "text_content": text_content,
        "status": "Yuborildi",
        "rejection_reason": "",
        "created_at": current_time
    }
    
    # Save (Supabase va JSON parallel ishlaydi, xatolikda o'chmaydi)
    await save_application_db(payload)
    
    success_text = (
        f"✅ Murojaatingiz muvaffaqiyatli qabul qilindi!\n\n"
        f"🆔 Murojaat ID raqami: **{app_id}**\n"
        f"📊 Holati: **Yuborildi**\n\n"
        f"Murojaat holatini istalgan vaqtda /holat buyrug'i orqali tekshirishingiz mumkin."
    )
    await message.answer(success_text, parse_mode="Markdown")
    
    admin_alert = (
        f"📩 **YANGI MUROJAAT KELDI (ID: {app_id})**\n\n"
        f"👤 Kimdan: {escape_markdown(payload['fish'])}\n"
        f"📍 MFY: {escape_markdown(payload['mfy'])}\n"
        f"🗂 Toifa: {escape_markdown(payload['category'])}\n"
        f"📞 Tel: {escape_markdown(payload['phone'])}\n"
        f"🔗 Qo'shimcha: {escape_markdown(payload['additional_contact'])}\n\n"
        f"📝 Matn: {escape_markdown(payload['text_content'])}"
    )
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_alert,
            reply_markup=get_admin_action_keyboard(app_id),
            parse_mode="Markdown"
        )
        logger.info(f"Yangi ariza adminga uzatildi. ID: {app_id}")
    except Exception as e:
        logger.error(f"Adminga bildirishnoma ketmadi: {e}")

# 8. STATUS CHECKING FOR USERS
@router.message(Command("holat"))
async def check_status(message: Message):
    loop = asyncio.get_event_loop()
    user_id = message.from_user.id
    
    try:
        res = await loop.run_in_executor(None, lambda: asyncio.run(sync_supabase_select_by_user(user_id)))
        records = res.data if res and hasattr(res, 'data') else []
    except Exception:
        records = []
        if os.path.exists(JSON_FILE_PATH):
            with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
                all_rec = json.load(f)
                records = [r for r in all_rec if r.get("user_id") == user_id]
                
    if not records:
        await message.answer("Sizda hech qanday arizalar mavjud emas.")
        return
        
    response = "📊 **Sizning arizalaringiz ro'yxati:**\n\n"
    for r in records:
        reason_str = f"\n❌ Sabab: _{escape_markdown(r.get('rejection_reason'))}_" if r.get('rejection_reason') else ""
        response += (
            f"🆔 ID: `{r.get('application_id')}` | Toifa: {escape_markdown(r.get('category'))}\n"
            f"🔹 Holati: **{escape_markdown(r.get('status'))}**{reason_str}\n"
            f"--- \n"
        )
    await message.answer(response, parse_mode="Markdown")

# 9. ADMIN PANEL ACTIONS & MANAGEMENT
@router.message(F.text.regexp(r'^\d{4}$'))
async def admin_lookup_by_id(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    app_id = int(message.text.strip())
    app = await get_application_from_db(app_id)
    
    if not app:
        await message.answer(f"❌ ID {app_id} bilan hech qanday ariza topilmadi.")
        return
        
    reason_str = f"\n❌ Rad etish sababi: {escape_markdown(app.get('rejection_reason'))}" if app.get('rejection_reason') else ""
    info = (
        f"🔎 **Murojaat Ma'lumotlari (ID: {app['application_id']})**\n\n"
        f"👤 Arizachi: {escape_markdown(app['fish'])}\n"
        f"📍 MFY: {escape_markdown(app['mfy'])}\n"
        f"🗂 Toifa: {escape_markdown(app['category'])}\n"
        f"📞 Tel: {escape_markdown(app['phone'])}\n"
        f"🔗 Aloqa: {escape_markdown(app['additional_contact'])}\n"
        f"📊 Holati: **{escape_markdown(app['status'])}**{reason_str}\n\n"
        f"📝 Matn: {escape_markdown(app['text_content'])}"
    )
    await message.answer(info, reply_markup=get_admin_action_keyboard(app_id), parse_mode="Markdown")

@router.callback_query(F.data.startswith("adm_accept:"))
async def admin_accept_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Siz admin emassiz!", show_alert=True)
        return

    app_id = int(callback.data.split(":")[1])
    app = await get_application_from_db(app_id)
    if app:
        await update_application_db(app_id, {"status": "Qabul qilindi"})
        await callback.message.edit_text(callback.message.text + "\n\n🟢 **Holat: Qabul qilindi deb o'zgartirildi**", parse_mode="Markdown")
        
        try:
            await bot.send_message(
                chat_id=app["user_id"],
                text=f"✅ Sizning `ID: {app_id}` dagi murojaatingiz mutaxassislar tomonidan **Qabul qilindi**.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Foydalanuvchiga xabar yuborib bo'lmadi: {e}")
    await callback.answer()

@router.callback_query(F.data.startswith("adm_resolve:"))
async def admin_resolve_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Siz admin emassiz!", show_alert=True)
        return

    app_id = int(callback.data.split(":")[1])
    app = await get_application_from_db(app_id)
    if app:
        await update_application_db(app_id, {"status": "Hal etildi"})
        await callback.message.edit_text(callback.message.text + "\n\n🏁 **Holat: Hal etildi deb belgilandi**", parse_mode="Markdown")
        
        try:
            await bot.send_message(
                chat_id=app["user_id"],
                text=f"🏁 Sizning `ID: {app_id}` dagi murojaatingiz to'liq **Hal etildi**. Bizni tanlaganingiz uchun rahmat!",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Foydalanuvchiga xabar yuborib bo'lmadi: {e}")
    await callback.answer()

@router.callback_query(F.data.startswith("adm_reject:"))
async def admin_reject_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Siz admin emassiz!", show_alert=True)
        return

    app_id = int(callback.data.split(":")[1])
    await state.update_data(reject_app_id=app_id)
    await bot.send_message(chat_id=ADMIN_ID, text=f"Iltimos, ID {app_id} arizasini rad etish sababini batafsil yozing:")
    await state.set_state(AdminJarayoni.RadSababi)
    await callback.answer()

@router.message(AdminJarayoni.RadSababi)
async def process_rejection_reason_text(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    reason = message.text.strip()
    adm_data = await state.get_data()
    app_id = adm_data["reject_app_id"]
    await state.clear()
    
    app = await get_application_from_db(app_id)
    if app:
        await update_application_db(app_id, {"status": "Rad etildi", "rejection_reason": reason})
        await message.answer(f"❌ ID {app_id} bo'yicha ariza rad etildi va sababi foydalanuvchiga yuborildi.")
        
        try:
            await bot.send_message(
                chat_id=app["user_id"],
                text=f"❌ Sizning `ID: {app_id}` dagi murojaatingiz rad etildi.\n\n**Sababi:** {reason}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Foydalanuvchiga rad xabari ketmadi: {e}")
    else:
        await message.answer("Xatolik: Arizani bazadan topib bo'lmadi.")

# 10. MAIN POLLING FUNCTION
async def main():
    logger.info("Bot qayta tartiblangan xavfsiz mantiq bilan ishga tushmoqda...")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    except Exception as general_error:
        logger.critical(f"Botning asosiy siklida kutilmagan xatolik: {general_error}")

if __name__ == "__main__":
    asyncio.run(main())
