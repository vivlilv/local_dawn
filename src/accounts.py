import asyncio
import aiohttp
import logging
from datetime import datetime, timezone
from collections import deque
from typing import List, Dict, Optional
from src.local_dawn.src.captcha import solve_captcha 
from src.local_dawn.src.mail import get_verification_link
from src.local_dawn.src.mongo import client
from src.local_dawn.src.config import SETTINGS
import ssl
import certifi
import random
import re
import json
from aiohttp_socks import ProxyConnector
from urllib.parse import urlparse
import signal
import sys
import atexit

import logging

LOG_COLORS = {
    'INFO': '\033[92m',     # Green
    'WARNING': '\033[93m',  # Yellow
    'ERROR': '\033[91m',    # Red
    'RESET': '\033[0m'      # Reset to default color
}

class CustomFormatter(logging.Formatter):
    def format(self, record):
        log_msg = super().format(record)
        
        log_color = LOG_COLORS.get(record.levelname, LOG_COLORS['RESET'])
        return f"{log_color}{log_msg}{LOG_COLORS['RESET']}"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [Function: %(funcName)s] - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

for handler in logging.getLogger().handlers:
    handler.setFormatter(CustomFormatter('%(asctime)s - [Function: %(funcName)s] - %(levelname)s - %(message)s'))


def parse_user_agent(user_agent):
    browser_match = re.search(r'(Opera|Chrome|Safari|Firefox|MSIE|Trident(?=/))', user_agent)
    browser = browser_match.group(1) if browser_match else "Unknown"
    
    version_match = re.search(r'(?:Version|'+ browser + r')/(\d+(\.\d+)?)', user_agent)
    version = version_match.group(1) if version_match else "0"
    
    platform_match = re.search(r'\((.*?)\)', user_agent)
    platform = platform_match.group(1).split(';')[0] if platform_match else "Unknown"
    
    return browser, version, platform

class AccountsManager:
    def __init__(self):
        self.client = client
        self.db = self.client[SETTINGS['DB_NAME']]
        self.collection = self.db[SETTINGS['ACCOUNTS_COLLECTION']]
        self.active_accounts: Dict[str, Account] = {}
        self.add_new_accounts: Dict[str, Account] = {}
        self.registration_queue = deque()
        self.max_simultaneous_registrations = SETTINGS['REGISTRATION_THREADS']
        self.currently_registering = set()
        self.clear_registration_queue()
        self.shutdown_flag = False
        self.shutdown_event = asyncio.Event()
        atexit.register(self.cleanup)

    def setup_signal_handlers(self):
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self.signal_handler)
        loop.add_signal_handler(signal.SIGTERM, self.signal_handler)

    def signal_handler(self):
        if not self.shutdown_event.is_set():
            logging.info("Received shutdown signal.")
            self.shutdown_event.set()
            asyncio.create_task(self.shutdown())
        else:
            logging.info("Shutdown signal already received, ignoring.")

    async def shutdown(self):
        logging.info("Shutting down, closing sessions.")
        await self.close_all_sessions()
        sys.exit(0)

    async def close_all_sessions(self):
        for account in self.active_accounts.values():
            await account.close_session()

    def cleanup(self):
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.run_until_complete(self.close_all_sessions())
        else:
            asyncio.run(self.close_all_sessions())

    def clear_registration_queue(self):
        self.registration_queue.clear()
        logging.info("Cleared registration queue on launch.")

    async def fetch_active_registered_verified_accounts(self) -> List[Dict]:
        return await self.collection.find({'account_state': 'active', 'registered': True, 'verified': True}).to_list(length=None)

    async def fetch_unregistered_or_unverified_accounts(self) -> List[Dict]:
        return await self.collection.find({
            '$or': [{'registered': False}, {'verified': False}],
            'registration_failed': False
        }).to_list(length=None)

    async def run_active_registered_accounts(self):
        while True:
            try:
                logging.info(f"Working on new accounts: {self.add_new_accounts}")
                for account_id, account_instance in self.add_new_accounts.items():
                    if account_id not in self.active_accounts:
                        self.active_accounts[account_id] = account_instance
                        logging.info(f"Starting task for account {account_id}: {account_instance.account_details['mail']}\n")
                        asyncio.create_task(self.active_accounts[account_id].start_task())
                self.add_new_accounts.clear()
            finally:
                await asyncio.sleep(180)

    async def check_db_for_changes(self):
        current_active_accounts = await self.fetch_active_registered_verified_accounts()
        unregistered_or_unverified = await self.fetch_unregistered_or_unverified_accounts()

        for a in unregistered_or_unverified:
            print('Mail:',a['mail'])

        current_ids = {acc['_id'] for acc in current_active_accounts}
        current_ids.update({acc['_id'] for acc in unregistered_or_unverified})
        
        new_accounts = [acc for acc in current_active_accounts if acc['_id'] not in self.active_accounts]
        new_accounts.extend([acc for acc in unregistered_or_unverified if acc['_id'] not in self.active_accounts])
        
        for acc in new_accounts:
            account = Account(account_details=acc, collection=self.collection)
            print(acc['mail'])
            if not acc.get('registered') or not acc.get('verified'):#add to registration queue
                self.registration_queue.append(account)
            else:#if account was paused, now resuming to work
                self.add_new_accounts[acc['_id']] = account
        
        #if state changed to 'sleep':
        for account_id in list(self.active_accounts):
            if account_id not in current_ids:
                await self.active_accounts[account_id].stop_task()
                del self.active_accounts[account_id]
            else:
                updated_acc = next(acc for acc in current_active_accounts if acc['_id'] == account_id)
                self.active_accounts[account_id].points = updated_acc.get('points', 0)
    
    async def process_registration_queue(self):
        while self.registration_queue:
            tasks = []
            logging.info(f"Starting to process registration queue. Queue size: {len(self.registration_queue)}")
            while self.registration_queue and len(self.currently_registering) < self.max_simultaneous_registrations:
                account = self.registration_queue.popleft()
                if not account.account_details['registration_failed']:
                    self.currently_registering.add(account.account_details['_id'])
                    tasks.append(asyncio.create_task(self.register_and_handle(account)))
                    logging.info(f"Added account {account.account_details['name']} to registration tasks. Currently registering: {len(self.currently_registering)}")
                else:
                    logging.warning(f"Skipping registration for failed account: {account['name']}")
            
            if tasks:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                logging.info(f"Completed {len(done)} tasks, {len(pending)} tasks still pending.")
            else:
                logging.info("No tasks created, sleeping for 5 seconds before checking the queue again.")
                await asyncio.sleep(5)  # Wait before checking again if no tasks were created

    async def register_and_handle(self, account):
        try:
            delay = random.uniform(1, SETTINGS['REGISTRATION_THREADS']*3)
            logging.info(f"Sleeping for {delay} seconds before next registration cycle for {account.account_details['mail']}")
            await asyncio.sleep(delay)
            await account.full_registration()
        finally:
            self.currently_registering.remove(account.account_details['_id'])
            if account.account_details['registered'] and account.account_details['verified']:
                await account.start_task()

    async def update_registration_attempt(self, account, status):
        await self.collection.update_one(
            {'_id': account.account_details['_id']},
            {'$set': {'registration_attempt': status}}
        )

    async def renew_all_tokens(self):
        logging.info("Starting token renewal for all registered accounts")
        current_active_accounts = await self.fetch_active_registered_verified_accounts()
        
        # Create a semaphore to limit concurrent token renewals
        semaphore = asyncio.Semaphore(SETTINGS['REGISTRATION_THREADS'])

        async def renew_token(acc):
            async with semaphore:
                account_id = acc.account_details['_id']
                if account_id in self.active_accounts:
                    account = self.active_accounts[account_id]
                else:
                    account = Account(account_details=acc, collection=self.collection)
                    self.active_accounts[account_id] = account

                logging.info(f"Renewing token for account: {acc['name']}")
                await account.login_with_retry()

        # Create tasks for all accounts
        tasks = [asyncio.create_task(renew_token(acc)) for acc in current_active_accounts]

        # Wait for all tasks to complete
        await asyncio.gather(*tasks)

        # Remove accounts that are no longer active, registered, or verified
        current_ids = {acc['_id'] for acc in current_active_accounts}
        for account_id in list(self.active_accounts.keys()):
            if account_id not in current_ids:
                await self.active_accounts[account_id].stop_task()
                del self.active_accounts[account_id]

        logging.info("Token renewal process completed")

    async def run(self):
        self.setup_signal_handlers()
        e = None  # Initialize e to None
        try:
            logging.info("Checking DB for changes")
            await self.check_db_for_changes()
            # logging.info("Running active registered accounts")
            logging.info("Processing registration queue")
            asyncio.create_task(self.run_active_registered_accounts())
            asyncio.create_task(self.process_registration_queue())
            
            while True:
                if self.shutdown_event.is_set():
                    await self.shutdown()
                try:
                    logging.info("Checking DB for changes")
                    await self.check_db_for_changes()
                except Exception as e:
                    logging.error(f"Error in main loop: {e}", exc_info=True)
                logging.info(f"Sleeping for {SETTINGS['CHECK_INTERVAL']} seconds")
                await asyncio.sleep(SETTINGS['CHECK_INTERVAL'])
        except Exception as e:
            logging.error(f"Error in main loop: {e}", exc_info=True)
        except asyncio.CancelledError:
            logging.info("Main loop cancelled")
        finally:
            if e:
                logging.error(f"Unhandled exception: {e}", exc_info=True)
            await self.shutdown()

class Account:
    def __init__(self, account_details: Dict, collection):
        self.account_details = account_details
        self.collection = collection
        self.should_stop = False
        self.points = self.account_details.get('points', 0)
        self.task = None
        self.session: Optional[aiohttp.ClientSession] = None

    

    async def create_session(self):
        if self.session is None or self.session.closed:
            logging.warning(self.account_details['proxy'])
            proxy = self.account_details['proxy']
            connector = None

            if proxy:
                try:
                    connector = ProxyConnector.from_url(proxy, ssl=False)
                    logging.info(f"Using proxy: {proxy}")
                except Exception as e:
                    logging.error(f"Failed to connect using proxy {proxy}: {e}")
                    return
            
            if connector:
                self.session = aiohttp.ClientSession(connector=connector)
                self.session.headers.update({
                    "user-agent": self.account_details['user_agent'],
                    "authorization": f"Berear {self.account_details['token']}"
                })
                logging.info("Session created successfully.")
        else:
            logging.info("Reusing existing session.")

    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def set_headers(self):
        self.session.headers.update(SETTINGS['DEFAULT_HEADERS'])
        self.session.headers.update({"user-agent": self.account_details['user_agent']})

    async def get_puzzle(self) -> str:
        url = f"{SETTINGS['BASE_URL']}/puzzle/get-puzzle?appid=undefined"
        
        browser, version, platform = parse_user_agent(self.account_details['user_agent'])
        
        headers = {
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "origin": "chrome-extension://fpdkjdnhkakefebpekbdhillbhonfjjp",
            "priority": "u=1, i",
            "sec-ch-ua": f'"{browser}";v="{version}", "Not)A;Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{platform}"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": self.account_details['user_agent']
        }
        logging.info(f"Sending request to: {url}")
        
        try:
            self.session.headers.update(headers)
            async with self.session.get(url) as response:
                logging.info(f"Response status: {response.status}")
                response.raise_for_status()
                data = await response.json()
                return data['puzzle_id']
            
        except Exception as e:
            logging.error(f"Error in get_puzzle: {str(e)}")
            # logging.error(f"Account details: {json.dumps(self.account_details, indent=2, default=str)}")
            raise

    async def get_puzzle_base_64(self, puzzle_id: str) -> str:
        url = f"{SETTINGS['BASE_URL']}/puzzle/get-puzzle-image?puzzle_id={puzzle_id}&appid=undefined"
        async with self.session.get(url) as response:
            response.raise_for_status()
            data = await response.json()
            return data['imgBase64']

    async def register_user(self, puzzle_id: str, solution: str) -> int:
        url = f"{SETTINGS['BASE_URL']}/puzzle/validate-register"
        registration_data = {
            "ans": solution,
            "country": "+91",
            "firstname": self.account_details['name'],
            "lastname": self.account_details['name'],
            "email": self.account_details['mail'],
            "password": self.account_details['mail_pass'],
            "puzzle_id": puzzle_id,
            "mobile": "",
            "referralCode": self.account_details.get('referralCode', '')
        }
        async with self.session.post(url, json=registration_data) as response:
            data = await response.json()
            logging.info(f"Registration response for {self.account_details['name']}: {data}")
            return response.status

    async def verify_mail(self) -> Optional[str]:
        for _ in range(5):
            try:
                link = get_verification_link(username=self.account_details['mail'], password=self.account_details['mail_pass'])
                if link.startswith('https'):
                    self.session.headers.update(SETTINGS['VERIFICATION_HEADERS'])
                    await self.session.get(link)
                    return link
            except Exception as e:
                logging.error(f"Error while getting email: {e}")
            await asyncio.sleep(10)
        return None

    async def login(self, puzzle_id: str, solution: str) -> Optional[str]:

        now = datetime.now(timezone.utc)
        formatted_time = now.strftime('%Y-%m-%dT%H:%M:%S.') + f"{now.microsecond // 1000:03d}Z"

        url = f"{SETTINGS['BASE_URL']}/user/login/v2?appid=undefined"
        login_data = {
            "username": self.account_details['mail'],
            "password": self.account_details['mail_pass'],
            "logindata": {
                "_v": SETTINGS['VERSION'],
                "datetime": formatted_time
            },
            "puzzle_id": puzzle_id,
            "ans": solution
        }

        browser, version, platform = parse_user_agent(self.account_details['user_agent'])
        headers = {
            
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "origin": "chrome-extension://fpdkjdnhkakefebpekbdhillbhonfjjp",
            "priority": "u=1, i",
            "sec-ch-ua": f'"{browser}";v="{version}", "Not)A;Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{platform}"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": self.account_details['user_agent']
        }
        try:
            async with self.session.post(url,headers=headers, json=login_data) as response:
                logging.info(await response.text())
                if 'application/json' in response.headers['Content-Type']:
                    data = await response.json()
                    if data.get('message') == 'Successfully logged in!':
                        return data.get('data', {}).get('token')
                    else:
                        return None
        except Exception as e:
            logging.error(f"Error during login request: {str(e)}", exc_info=True)
            return None

    async def get_user_referral_points(self) -> Dict:
        await self.create_session()
        url = SETTINGS['GET_POINT_URL']

        #token which is taken from DB dynamically
        account = await self.collection.find_one({'_id': self.account_details['_id']})

        browser, version, platform = parse_user_agent(self.account_details['user_agent'])
        headers = {
             "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "if-none-match": 'W/"336-Qpy+2RS1on9WRMSlwnrjPMYeucg"',
            "origin": "chrome-extension://fpdkjdnhkakefebpekbdhillbhonfjjp",
            "priority": "u=1, i",
            "authorization": f"Berear {account['token']}",
            "sec-ch-ua": f'"{browser}";v="{version}", "Not)A;Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{platform}"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": self.account_details['user_agent']
        }

        try:
            async with self.session.get(url,headers=headers) as response:
                print('*'*100)
                logging.info(f"Response status: {response.status}")
                if response.status<400:
                    data = await response.json()
                    if data['data']['rewardPoint']['points']:
                        new_points = data['data']['rewardPoint']['points']
                        if new_points != self.points:
                            self.points = new_points
                            await self.update_points_in_db(new_points)
                
                    logging.info(f"Current points: {self.points}")
                    return data
                else:
                    logging.error(f'Failed with status: {response.status}. Trying different proxy...')
                    with open('proxies.txt', 'r') as f:
                        lines = f.readlines()
                        for i in range(len(lines)):
                            proxy = lines[random.randrange(0, len(lines)-1)].strip()  # Get a random proxy
                            result = await self.check_ip()
                            if result != 1:  # if returns IP
                                existing_account = await self.collection.find_one({'proxy': proxy})
                                if existing_account is None:  # Proxy is not in use
                                    self.account_details['proxy'] = proxy
                                    await self.update_proxy_in_db(proxy)
                                    
                                    # Remove the used proxy from the file
                                    lines.remove(f"{proxy}\n")  # Remove the selected proxy
                                    with open('proxies.txt', 'w') as f_write:
                                        f_write.writelines(lines)  # Write remaining proxies back to the file
                                    break
                                else:
                                    logging.warning(f"Proxy {proxy} is already in use by another account.")
                    
                    await self.create_session()

        except Exception as e:
            logging.error(f"Error in get_user_referral_points: {str(e)}")
            raise
    
    async def update_proxy_in_db(self, proxy: str):
        await self.collection.update_one(
            {'_id': self.account_details['_id']},
            {'$set': {'proxy': proxy}}
        )
        logging.info(f"Updated proxy in database for {self.account_details['mail']}: {proxy}")


    async def update_points_in_db(self, new_points: int):
        await self.collection.update_one(
            {'_id': self.account_details['_id']},
            {'$set': {'points': new_points}}
        )
        logging.info(f"Updated points in database for {self.account_details['name']}: {new_points}")

    async def keep_alive(self) -> int:
        account = await self.collection.find_one({'_id': self.account_details['_id']})
        await self.update_token_in_db(account['token'])
        temp_token = self.account_details['token']

        url = f"{SETTINGS['BASE_URL']}/userreward/keepalive"
        headers = {
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "authorization": f"Berear {self.account_details['token']}",
            "content-type": "application/json",
            "origin": "chrome-extension://fpdkjdnhkakefebpekbdhillbhonfjjp",
            "priority": "u=1, i",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": self.account_details['user_agent']
        }
        body = {
            "username": self.account_details['mail'],
            "extensionid": SETTINGS['EXTENSION_ID'],
            "numberoftabs": 0,
            "Referer": "",
            "_v": SETTINGS['VERSION'],
        }
        async with self.session.post(url,headers=headers, json=body) as response:
            try:
                if response.status>=400:
                    logging.warning(f"{response.status}: {'Server error' if response.status>500 else 'Proxy issue'}")
                    if 'application/json' in response.headers['Content-Type']:
                        json_data = await response.json()
                        if json_data['message'] == 'Your app session expired, Please login again.':
                            logging.warning(f"Session expired for {self.account_details['mail']}")
                            await self.login_with_retry()
                            if temp_token==self.account_details['token']:
                                await asyncio.sleep(3600)#if token hasn't been updated wait for an hour
                    
                else:
                    logging.info(f'Successful keepalive for {self.account_details["mail"]}')
            except Exception as e:
                logging.error(f"Failed to parse JSON: {str(e)}")
            return response.status

    async def full_registration(self) -> Dict[str, bool]:
        logging.info(self.account_details)
        if self.account_details.get('registration_failed'):
            logging.warning(f"Skipping full_registration for failed account: {self.account_details['name']}")
            return {"registered": False, "verified": False}
        
        logging.info(f"Starting registration for {self.account_details['name']}")
        await self.create_session()
        result = {"registered": self.account_details['registered'], "verified": self.account_details['verified']}
        max_registration_attempts = SETTINGS['max_registration_attempts']
        max_verification_attempts = SETTINGS['max_verification_attempts']

        account = await self.collection.find_one({'_id': self.account_details['_id']})

        for _ in range(max_registration_attempts):
            if not self.account_details.get('registered'):
                registration_attempts = account.get('registration_attempts')
                if registration_attempts < max_registration_attempts:
                    try:
                        puzzle_id = await self.get_puzzle()
                        img_base_64 = await self.get_puzzle_base_64(puzzle_id)
                        solution = await solve_captcha(img_base_64, owner_id=self.account_details['owner'])
                        await asyncio.sleep(35)
                        response = await self.register_user(puzzle_id, solution)
                        if response == 200:
                            await self.update_registration_status(True)
                            logging.info(f"Account {self.account_details['name']} successfully registered")
                            result["registered"] = True
                            break
                        else:
                            await self.increment_registration_attempts()
                            logging.warning(f"Registration failed for {self.account_details['name']}. Attempt {registration_attempts + 1}/{max_registration_attempts}")
                    except Exception as e:
                        await self.increment_registration_attempts()
                        logging.error(f"Error during registration for {self.account_details['name']}: {str(e)}")
                else:
                    logging.error(f"Max registration attempts reached for {self.account_details['name']}")
                    break

        for _ in range(max_verification_attempts):
            if result["registered"] and not self.account_details.get('verified'):
                verification_attempts = account.get('verification_attempts')
                if verification_attempts < max_verification_attempts:
                    if await self.verify_mail():
                        await self.update_verification_status(True)
                        logging.info(f"Account {self.account_details['name']} successfully verified")
                        result["verified"] = True
                        break
                    else:
                        await self.increment_verification_attempts()
                        logging.warning(f"Verification failed for {self.account_details['name']}. Attempt {verification_attempts + 1}/{max_verification_attempts}")
                else:
                    logging.error(f"Max verification attempts reached for {self.account_details['name']}")
                    break

        if not self.account_details.get('registered') or not self.account_details.get('verified'):
            await self.collection.update_one(
                {'_id': self.account_details['_id']},
                {'$set': {'registration_failed': True}}
            )
            logging.error(f"Account {self.account_details['name']} marked as failed after exhausting all registration attempts")

        return result

    async def increment_registration_attempts(self):
        await self.collection.update_one(
            {'_id': self.account_details['_id']},
            {'$inc': {'registration_attempts': 1}}
        )
        
    async def increment_verification_attempts(self):
        await self.collection.update_one(
            {'_id': self.account_details['_id']},
            {'$inc': {'verification_attempts': 1}}
        )

    async def update_registration_status(self, status: bool):
        await self.collection.update_one(
            {'_id': self.account_details['_id']},
            {'$set': {'registered': status}}
        )
        self.account_details['registered'] = status

    async def update_verification_status(self, status: bool):
        await self.collection.update_one(
            {'_id': self.account_details['_id']},
            {'$set': {'verified': status}}
        )
        self.account_details['verified'] = status

    async def farm(self):
        logging.info(f"Starting farming for {self.account_details['mail']}")
        await self.create_session()
        await self.set_headers()
        try:
            if not self.account_details.get('token') or self.account_details['token']=='':
                await self.login_with_retry()
            
            while self.account_details['account_state'] == 'active' and not self.should_stop:
                try:
                    await asyncio.gather(
                        self.get_user_referral_points(),#THE PROBLEM OF PASSING INITIAL TOKEN
                        self.keep_alive_with_retry(),
                    )
                except Exception:
                    pass

                delay = random.uniform(100, 200)
                logging.info(f"Sleeping for {delay} seconds before next farming cycle for {self.account_details['mail']}")
                await asyncio.sleep(delay)
        finally:
            await self.close_session()

    async def login_with_retry(self, max_retries: int = 10):
        for attempt in range(max_retries):
            try:
                await self.create_session()
                puzzle_id = await self.get_puzzle()
                img_base_64 = await self.get_puzzle_base_64(puzzle_id)
                await asyncio.sleep(20)
                solution = await solve_captcha(img_base_64,owner_id=self.account_details['owner'])
                token = await self.login(puzzle_id=puzzle_id, solution=solution)
                if token:
                    with open('renewed_accounts.txt','a') as f:
                        f.write(f"{self.account_details['name']}---{self.account_details['mail']}\n")
                    await self.update_token_in_db(token)
                    self.account_details['token'] = token  # Update the token in memory
                    logging.info(f"Successfully renewed token for account: {self.account_details['name']}")
                    return
                else:
                    logging.warning(f"Login attempt {attempt + 1} failed: No token received")
            except Exception as e:
                logging.error(f"Login error for {self.account_details['name']} (attempt {attempt + 1}): {str(e)}", exc_info=True)
            finally:
                await self.close_session()
            await asyncio.sleep(10)
        logging.error(f"Failed to renew token for {self.account_details['name']} after {max_retries} attempts")

    async def keep_alive_with_retry(self, max_retries: int = 5):
        for attempt in range(max_retries):
            try:
                status = await self.keep_alive()
                if status == 200:
                    return
            except Exception as e:
                logging.error(f"Keep_alive error (attempt {attempt + 1}): {e}")
            await asyncio.sleep(15)
        logging.error("Failed to perform keep_alive after all attempts")

    async def update_token_in_db(self, token: str):
        await self.collection.update_one(
            {'_id': self.account_details['_id']},
            {'$set': {'token': token}}
        )

    async def start_task(self):
        if self.task is None:
            self.should_stop = False
            if self.account_details.get('registered')==True and self.account_details.get('verified')==True:
                self.task = asyncio.create_task(self.farm())

    async def stop_task(self):
        if self.task is not None:
            self.should_stop = True
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        await self.close_session()

    async def check_ip(self):
        url = "https://api.ipify.org"
        try:
            async with self.session.get(url,ssl=False) as response:
                ip = await response.text()
                logging.info(f"Request made from IP: {ip}")
                return ip
        except Exception as e:
            logging.error(f"Error checking IP: {str(e)}")
            return 1


if __name__ == "__main__":
    manager = AccountsManager()
    asyncio.run(manager.run())