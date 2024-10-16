import time
import httpx
from mongo import client
from config import SETTINGS
import asyncio

db = client[SETTINGS['DB_NAME']]
users_api_keys = db[SETTINGS['USERS_API_KEYS']]

async def print_db_entries():
    cursor = users_api_keys.find({})
    print("Entries in DB:")
    async for document in cursor:
        print(document)

async def get_api_key_for_account(owner_id):
    try:
        user_api_key = await users_api_keys.find_one({'user_id': str(owner_id)})
        if not user_api_key or 'api_key' not in user_api_key:
            raise ValueError(f"API key not found for user {owner_id}")
        
        return user_api_key['api_key']
    except Exception as e:
        print(f"Error retrieving API key: {str(e)}")
        return None

async def solve_captcha(img_base64, owner_id):
    api_key = await get_api_key_for_account(owner_id)
    if not api_key:
        raise ValueError("Failed to retrieve API key for the account")

    async with httpx.AsyncClient() as client:
        url = 'https://api.capmonster.cloud/createTask'
        data = {
            "clientKey": api_key,
            "task": {
                "type": "ImageToTextTask",
                "body": img_base64,
                "case": True,
                "capMonsterModule":"universal"
            }
        }

        response = await client.post(url, json=data)
        data = response.json()
        
        time.sleep(10)
        task_id = data.get('taskId')
        params = {
        "clientKey": api_key,
        "taskId": task_id
        }

        result = await client.post(url='https://api.capmonster.cloud/getTaskResult', json=params)
        solution = result.json().get('solution', {}).get('text')
        
        return solution


if __name__=="__main__":
    # asyncio.run(print_db_entries())
    asyncio.run(get_api_key_for_account('228946258'))









