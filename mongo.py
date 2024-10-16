from motor.motor_asyncio import AsyncIOMotorClient
import logging
from typing import List, Dict, Tuple
import asyncio
from pprint import pprint
from bson import ObjectId
from faker import Faker
import random
from fake_useragent import UserAgent
from datetime import datetime
from config import SETTINGS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


client = AsyncIOMotorClient('mongodb://localhost:27017/')
db = client['dawn_profiles']
collection = db['user_entries']
users_api_keys = db['users_api_keys']

fake = Faker()

#main workflow
async def validate_data(data: Dict) -> bool:
    mail = data.get('mail', '')
    mail_pass = data.get('mail_pass', '')
    proxy = data.get('proxy', '')

    return all([
        '@' in mail,
        len(mail_pass) >= 8,
        proxy.startswith('http:') or proxy.startswith('https:')
    ])

def generate_random_user_agent():
    ua = UserAgent()
    return ua.chrome

async def process_excel_file(file_path: str, owner_id: str) -> str:
    try:
        dtype = {
            'mail:mail_pass': str,
            'proxy': str,
            'registered': bool,
            'verified': bool,
            'referralCode': str
        }
        df = pd.read_excel(file_path,header=0, dtype=dtype)
        logging.info(f"Initial DataFrame shape: {df.shape}")
        logging.info(f"Initial DataFrame columns: {df.columns.tolist()}")
        logging.info(f"Initial DataFrame head:\n{df.head()}")
        
        
    except UnicodeDecodeError as e:
        logging.error(f'Error reading file: {e}')
        return 'Ошибка чтения файла'
    
    valid_data = []
    invalid_entries = []

    for index, row in df.iterrows():
        row = row.to_dict()
        print(row)
        print('*'*69)
        mail = row['mail:mail_pass'].strip().split(':')[0]
        mail_pass = row['mail:mail_pass'].strip().split(':')[1]
        proxy = row['proxy'].strip()
        registered = row['registered']
        verified = row['verified']
        referralCode = row['referralCode'].strip() if isinstance(row['referralCode'], str) else ''

        data = {
            'name': fake.first_name() + fake.last_name() + str(fake.random_int(100, 9999)),
            'mail': mail,
            'mail_pass': mail_pass,
            'password': fake.password(length=random.randint(8, 13)),
            'proxy': proxy,
            'registered': registered,
            'verified': verified,
            'referralCode': referralCode,
            'user_agent': generate_random_user_agent()
        }
        if await validate_data(data):
            existing_account = await collection.find_one({'mail': data['mail']})
            if existing_account:
                # Update the existing account
                update_result = await collection.update_one(
                    {'_id': existing_account['_id']},
                    {'$set': {
                        'name': existing_account['name'],
                        'mail_pass': existing_account['mail_pass'],
                        'proxy': data['proxy'],
                        'referralCode': existing_account['referralCode'],
                        'user_agent': data['user_agent'],
                        'registration_attempts': 0,
                        'verification_attempts': 0,
                        'registration_failed': False,
                        'owner': existing_account['owner'],
                        'account_state': 'active',
                        'registered': data['registered'],
                        'verified': data['verified'],
                        'token': existing_account['token'],
                        'points': existing_account['points']
                    }}
                )
                if update_result.modified_count > 0:
                    logging.info(f"Updated existing account: {data['mail']}")
                else:
                    logging.warning(f"Failed to update existing account: {data['mail']}")
            else:
                # Insert new account
                data.update({
                    'owner': owner_id,
                    'account_state': 'active',
                    'registered': data['registered'],
                    'verified': data['verified'],
                    'token': None,
                    'registration_attempts': 0,
                    'verification_attempts': 0,
                    'registration_failed': False,
                    'points': 0
                })
                valid_data.append(data)
                logging.info(f"Valid entry: {data}")
        else:
            logging.warning(f"Invalid row data: {row}")
            invalid_entries.append(data)
    
    if valid_data:
        if len(valid_data) > 1:
            await collection.insert_many(valid_data)
        else:
            await collection.insert_one(valid_data[0])

    if invalid_entries:
        invalid_file_path = f'invalid_entries_{owner_id}.xlsx'
        columns = ['name', 'mail', 'mail_pass', 'proxy', 'registered', 'verified', 'referralCode']
        invalid_df = pd.DataFrame(invalid_entries, columns=columns)
        invalid_df.to_excel(invalid_file_path, index=False, header=True)
        logging.info(f'Файл с недействительными записями сохранен: {invalid_file_path}')
        return invalid_file_path
    
    return 'Аккаунты успешно добавлены и начинают регистрацию!'

async def toggle_account_state(owner_id: str) -> Tuple[int, str, str]:
    count = await collection.count_documents({'owner': owner_id})
    if count == 0:
        logging.warning(f"Не знайдено записів для owner_id: {owner_id}")
        return [1, 'err', '']

    cursor = collection.find({'owner': owner_id})
    
    # Get the current state from the first document
    first_entry = await cursor.to_list(length=1)
    if not first_entry:
        logging.warning(f"No entries found for owner_id: {owner_id}")
        return [1, 'err', '']
    
    current_state = first_entry[0].get('account_state')
    new_state = 'active' if current_state == 'sleep' else 'sleep'
    
    # Update all documents to the new state
    result = await collection.update_many(
        {'owner': owner_id},
        {'$set': {'account_state': new_state}}
    )

    logging.info(f"Оновлено {result.modified_count} записів до стану {new_state}")

    return [0, current_state, new_state]

async def get_accounts_stats_by_owner_id(owner_id: str) -> Dict[str, int]:
    pipeline = [
        {"$match": {"owner": owner_id}},
        {"$group": {
            "_id": None,
            "accounts": {"$sum": 1},
            "fully_registered_and_verified": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$eq": ["$registered", True]},
                            {"$eq": ["$verified", True]}
                        ]},
                        1,
                        0
                    ]
                }
            },
            "registration_failed": {
                "$sum": {
                    "$cond": [{"$eq": ["$registration_failed", True]}, 1, 0]
                }
            },
            "total_points": {"$sum": "$points"}
        }}
    ]

    result = await collection.aggregate(pipeline).to_list(length=1)
    print(result)
    if result:
        stats = result[0]
        stats.pop('_id', None)
        
        return stats
    else:
        return {
            "accounts": 0,
            "fully_registered_and_verified": 0,
            "registration_failed": 0,
            "total_points": 0
        }

async def print_db_entries():
    cursor = collection.find({})
    print("Записи в базі даних:")
    async for document in cursor:
        pprint(document)

async def retrieve_and_categorize_accounts(owner_id: str) -> str:
    cursor = collection.find({'owner': owner_id})
    
    registered_accounts = []
    not_registered_accounts = []
    failed_accounts = []

    accounts = []
    async for account in cursor:
        accounts.append(account)

    for account in accounts:
        registered = account.get('registered') in [True, 'True']
        verified = account.get('verified') in [True, 'True']
        registration_attempts = account.get('registration_attempts')
        verification_attempts = account.get('verification_attempts')
        
        account_info = {
            'mail:mail_pass': f"{account.get('mail', '')}:{account.get('mail_pass', '')}",
            'proxy': account.get('proxy', ''),
            'referralCode': account.get('referralCode', ''),
            'points': account.get('points', 0),
            'registration_attempts': registration_attempts,
            'verification_attempts': verification_attempts
        }

        if registered and verified:
            registered_accounts.append(account_info)
        elif not registered or not verified:
            if registration_attempts >= SETTINGS['max_registration_attempts'] or verification_attempts >= SETTINGS['max_verification_attempts']:
                account_info['error_msg'] = "Ошибка верификации почты" if registered else "Ошибка прохождения капчи"
                failed_accounts.append(account_info)
            else:
                not_registered_accounts.append(account_info)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"accounts_status_{owner_id}_{timestamp}.xlsx"

    with pd.ExcelWriter(filename) as writer:
        if registered_accounts:
            df_registered = pd.DataFrame(registered_accounts)
            df_registered.to_excel(writer, sheet_name='Registered', index=False)
        if not_registered_accounts:
            df_not_registered = pd.DataFrame(not_registered_accounts)
            df_not_registered.to_excel(writer, sheet_name='Not Registered', index=False)
        if failed_accounts:
            df_failed = pd.DataFrame(failed_accounts)
            df_failed.to_excel(writer, sheet_name='Failed', index=False)

    logging.info(f"Generated file {filename} with {len(registered_accounts)} valid, {len(not_registered_accounts)} not registered, and {len(failed_accounts)} failed accounts")
    return filename

async def save_api_key(user_id: str, api_key: str) -> Dict:
    """
    Save or update the API key for a user in the users_keys collection.
    :param user_id: The ID of the user
    :param api_key: The API key to save
    :return: A dictionary containing the result of the operation
    """
    try:
        result = await users_api_keys.update_one(
            {'user_id': user_id},
            {'$set': {'api_key': api_key}},
            upsert=True
        )
        
        if result.matched_count > 0:
            return {"status": "updated", "message": "API ключ успешно обновлён"}
        else:
            return {"status": "inserted", "message": "API ключ успешно добавлен"}
    except Exception as e:
        logging.error(f"Error saving API key for user {user_id}: {str(e)}")
        return {"status": "error", "message": f"Ошибка при попытке добавить АРІ ключ, повторите позже"}

async def mark_account_as_failed(account_id):
    await collection.update_one(
        {'_id': account_id},
        {'$set': {'registration_failed': True}}
    )

#used in tests/debugging
async def delete_entries_with_null_token():
    try:
        result = await collection.delete_many({"token": None})
        deleted_count = result.deleted_count
        logging.info(f"Deleted {deleted_count} entries with null token")
        return deleted_count
    except Exception as e:
        logging.error(f"Error deleting entries with null token: {str(e)}")
        raise

async def update_registration_fields():
    # Read proxies from the file
    with open('proxies.txt', 'r') as file:
        proxies = [line.strip() for line in file if line.strip()]

    # Update registration_failed based on proxy and registration status
    result = await collection.update_many(
        {},
        [
            {
                '$set': {
                    'registration_attempts': 0,
                    'verification_attempts': 0,
                    'proxy': random.choice(proxies)  # Set proxy to a random one from the list
                }
            }
        ]
    )
    print(f"Updated registration fields for {result.modified_count} documents")
    return result.modified_count

async def inspect_field_types():
    documents = collection.find({
        '$or': [{'registered': False}, {'verified': False}],
            'registration_failed': False
    })

    async for doc in documents:
        registered_type = type(doc.get('registered'))
        verified_type = type(doc.get('verified'))
        print(f"Document ID: {doc['mail']}")
        print(f"registered: {doc['registered']} (type: {registered_type})")
        print(f"verified: {doc['verified']} (type: {verified_type})")
        print("-" * 40)

async def renew_key():
    cursor = users_api_keys.find({})
    async for account in cursor:
        
        update_result = await users_api_keys.update_one(
            {'_id': account['_id']},
            {'$set': {'api_key': '4e4805e767d5c7f97b73863b98aeea17'}}
        )
        
        





if __name__ == "__main__":
    # asyncio.run(inspect_field_types())
    # asyncio.run(update_registration_fields())
    # asyncio.run(renew_key())
    asyncio.run(print_db_entries())
