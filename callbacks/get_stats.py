from src.mongo import get_accounts_stats_by_owner_id  # Relative import
import asyncio

async def main():
    owner_id = "1"
    try:
        result = await get_accounts_stats_by_owner_id(owner_id)
        print('res',result)  # Print the result to stdout
    except Exception as e:
        print(f"Error processing file: {str(e)}")  # Print any errors to stdout

asyncio.run(main())
