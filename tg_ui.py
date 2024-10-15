import os
import asyncio
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from mongo import process_excel_file, toggle_account_state, get_accounts_stats_by_owner_id, retrieve_and_categorize_accounts, save_api_key
import logging
from collections import defaultdict
import openpyxl

API_TOKEN = os.environ.get("API_TOKEN", "7214897743:AAHamDqE6ZFvemyLNQU-qF3CaU2ul3OEeC8")
bot = AsyncTeleBot(API_TOKEN)

waiting_for_api_key = defaultdict(bool)
waiting_for_accounts_file = defaultdict(bool)

def create_main_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("Установить API ключ", callback_data="set_api_key"))
    keyboard.row(InlineKeyboardButton("Добавить аккаунты", callback_data="add_accounts"))
    keyboard.row(InlineKeyboardButton("Старт/Стоп фарминга", callback_data="start_stop_farming"))
    keyboard.row(InlineKeyboardButton("Статистика", callback_data="stats"))
    keyboard.row(InlineKeyboardButton("Информация об аккаунтах", callback_data="info_accounts"))
    keyboard.row(InlineKeyboardButton("Помощь", callback_data="help"))
    return keyboard

@bot.message_handler(commands=['start', 'help'])
async def send_welcome(message):
    user_name = message.chat.username or message.chat.first_name or "пользователь"
    welcome_text = (
        f"👋 Добро пожаловать, @{user_name}, в бот для управления аккаунтами!\n\n"
        "🔑 Для начала работы, пожалуйста, установите свой API ключ от Capmonster, используя кнопку 'Установить API ключ'.\n\n"
        "📁 Затем вы можете добавить аккаунты, нажав на кнопку 'Добавить аккаунты' и следуя инструкциям.\n\n"
        "🔄 Используйте 'Старт/Стоп фарминга' для управления работой ваших аккаунтов.\n\n"
        "📊 Вы можете просмотреть статистику и информацию о ваших аккаунтах с помощью соответствующих кнопок.\n\n"
        "Выберите действие из меню ниже:"
    )
    await bot.reply_to(message, welcome_text, reply_markup=create_main_keyboard())

@bot.callback_query_handler(func=lambda call: True)
async def callback_query(call):
    if call.data == "add_accounts":
        await add_accounts_command(call.message)
    elif call.data == "start_stop_farming":
        await farm_command(call.message)
    elif call.data == "stats":
        await stats_command(call.message)
    elif call.data == "info_accounts":
        await info_accounts_command(call.message)
    elif call.data == "set_api_key":
        await set_api_key_command(call.message)
    elif call.data == "help":
        await send_help_message(call.message)

async def send_help_message(message):
    help_text = (
        "🔑 Установить API ключ: Добавьте ваш API ключ от Capmonster для решения капч.\n\n"
        "📁 Добавить аккаунты: Загрузите файл с данными аккаунтов для управления.\n\n"
        "🔄 Старт/Стоп фарминга: Управляйте работой ваших аккаунтов.\n\n"
        "📊 Статистика: Просмотрите общую статистику по вашим аккаунтам.\n\n"
        "ℹ️ Информация об аккаунтах: Получите детальную информацию о каждом аккаунте.\n\n"
        "Если у вас возникли вопросы, пожалуйста, свяжитесь с администратором."
    )
    await bot.send_message(message.chat.id, help_text, reply_markup=create_main_keyboard())

async def add_accounts_command(message):
    user_id = str(message.chat.id)
    waiting_for_accounts_file[user_id] = True
    
    example_file_path = 'accounts_example.xlsx'
    user_name = message.chat.first_name or message.chat.username or "пользователь"
    
    instructions = (
        f'Привет, {user_name}! Для добавления аккаунтов, пожалуйста, следуйте этим шагам:\n\n'
        '1. Подготовьте ексель файл (.xlsx) с данными аккаунтов\n'
        '2. Отправьте этот файл в чат\n'
        'Прокси только в формате http/https(как в примере файла)!!!\n'
        'Если аккаунт не регистрирован в DAWN то поля registered verified-(FALSE)\n\n'
        'Ниже приведен пример правильного формата файла:'
    )
    
    await bot.send_message(message.chat.id, instructions)
    
    with open(example_file_path, 'rb') as file:
        await bot.send_document(message.chat.id, file, caption='Пример файла')
    
    await bot.send_message(message.chat.id, 'Теперь отправьте ваш файл с аккаунтами.')

async def farm_command(message):
    user_id = message.chat.id

    await bot.send_message(message.chat.id, 'Меняю состояние аккаунтов (работа/сон)')
    result = await toggle_account_state(owner_id=str(user_id))
    if result[0] == 1:
        await bot.send_message(message.chat.id, 'Не найдено аккаунтов, добавьте их с помощью кнопки "Добавить аккаунты".')
    else:
        await bot.send_message(message.chat.id, f'Успешно изменено состояние аккаунтов с {result[1]} на {result[2]}')
    await bot.send_message(message.chat.id, "Выберите следующее действие:", reply_markup=create_main_keyboard())

async def stats_command(message):
    user_id = message.chat.id
    result = await get_accounts_stats_by_owner_id(owner_id=str(user_id))
    if result['accounts'] == 0:
        await bot.send_message(message.chat.id, 'Не найдено аккаунтов, добавьте их с помощью кнопки "Добавить аккаунты".')
    else:
        await bot.send_message(message.chat.id, f'У вас: {result["accounts"]} аккаунтов\nУже зарегистрировано: {result["fully_registered_and_verified"]}\nВ процессе регистрации: {result['accounts']-result["fully_registered_and_verified"]-result["registration_failed"]}\nНЕ прошедших: {result["registration_failed"]}\n{result["total_points"]} поинтов в сумме')
    await bot.send_message(message.chat.id, "Выберите следующее действие:", reply_markup=create_main_keyboard())
    
async def info_accounts_command(message):
    user_id = message.chat.id
    
    await bot.send_message(message.chat.id, 'Подготавливаю информацию о ваших аккаунтах...')
    try:
        filename = await retrieve_and_categorize_accounts(str(user_id))
        with open(filename, 'rb') as file:
            await bot.send_document(message.chat.id, file, caption='Информация о ваших аккаунтах')
        os.remove(filename)
    except Exception as e:
        logging.error(f"Error in info_accounts_command: {str(e)}")
        await bot.send_message(message.chat.id, 'Произошла ошибка при подготовке информации. Пожалуйста, попробуйте позже.')
    await bot.send_message(message.chat.id, "Выберите следующее действие:", reply_markup=create_main_keyboard())

async def set_api_key_command(message):
    user_id = str(message.chat.id)
    waiting_for_api_key[user_id] = True
    logging.debug(f"Waiting for API key from user {user_id}")
    instructions = (
        "Для получения API ключа:\n\n"
        "1. Зарегистрируйтесь на [CapMonster Cloud](https://capmonster.cloud/)\n"
        "2. Пополните баланс\n"
        "3. Скопируйте ваш API-ключ\n\n"
        "Пожалуйста, отправьте ваш API ключ от CapMonster в следующем сообщении."
    )
    await bot.send_message(message.chat.id, instructions, parse_mode='Markdown', disable_web_page_preview=True)
    await bot.send_message(message.chat.id, "Выберите следующее действие:", reply_markup=create_main_keyboard())

@bot.message_handler(content_types=['document'])
async def handle_docs(message):
    user_id = str(message.chat.id)
    
    if not waiting_for_accounts_file.get(user_id, False):
        await bot.reply_to(message, 'Пожалуйста, сначала используйте команду "Добавить аккаунты".')
        return
    
    if message.document.mime_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        file_info = await bot.get_file(message.document.file_id)
        file_path = f'temp_accounts_{user_id}.xlsx'  # Save the file with the user_id in the name
        downloaded_file = await bot.download_file(file_info.file_path)
        
        with open(file_path, 'wb') as new_file:
            new_file.write(downloaded_file)
    
        try:
            result = await process_excel_file(file_path, user_id)  # Process the .xlsx file
            os.remove(file_path)  # Remove the file after processing

            
            if isinstance(result, str):
                if result.startswith('invalid_entries_'):
                    with open(result, 'rb') as file:
                        await bot.send_document(message.chat.id, file, caption='Недействительные записи')
                    os.remove(result)
                    await bot.reply_to(message, 'Обнаружены недействительные записи. Файл с ними отправлен отдельно.')
                else:
                    await bot.reply_to(message, result)
            else:
                await bot.reply_to(message, 'Все аккаунты прошли проверку и начинают регистрацию.')
        
        except Exception as e:
            logging.error(f"Error processing file: {str(e)}")
            await bot.reply_to(message, 'Произошла ошибка при обработке файла. Пожалуйста, попробуйте еще раз.')
    else:
        await bot.reply_to(message, 'Пожалуйста, отправьте файл Excel (.xlsx)')
    
    waiting_for_accounts_file[user_id] = False
    await bot.send_message(message.chat.id, "Выберите следующее действие:", reply_markup=create_main_keyboard())




@bot.message_handler(func=lambda message: waiting_for_api_key.get(str(message.chat.id), False))
async def receive_api_key(message):
    user_id = str(message.chat.id)
    api_key = message.text.strip()
    logging.debug(f"Received potential API key from user {user_id}: {api_key}")
    
    try:
        result = await save_api_key(user_id, api_key)
        
        if result['status'] in ['updated', 'inserted']:
            success_message = f"{result['message']}\n\nВаш новый API ключ: `{api_key}`"
            await bot.reply_to(message, success_message, parse_mode='Markdown')
        else:
            await bot.reply_to(message, "Ошибка при попытке добавить API ключ, попробуйте позже.")
    except Exception as e:
        logging.error(f"Error saving API key: {str(e)}")
        await bot.reply_to(message, "Произошла ошибка при сохранении API ключа. Пожалуйста, попробуйте еще раз позже.")
    
    waiting_for_api_key[user_id] = False
    await bot.send_message(message.chat.id, "Выберите следующее действие:", reply_markup=create_main_keyboard())

@bot.message_handler(func=lambda message: True)
async def handle_all_messages(message):
    logging.debug(f"Received message: {message.text}")
    if waiting_for_api_key.get(str(message.chat.id), False):
        await receive_api_key(message)
    else:
        await bot.reply_to(message, "Извините, я не понимаю эту команду. Пожалуйста, используйте кнопки меню.")
    await bot.send_message(message.chat.id, "Выберите следующее действие:", reply_markup=create_main_keyboard())

async def main():
    while True:
        try:
            await bot.polling(non_stop=True)
        except Exception as e:
            print(f"An error occurred: {e}")
            await asyncio.sleep(5)

if __name__ == '__main__':
    asyncio.run(main())