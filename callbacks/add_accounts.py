import sys
from src.local_dawn.src.mongo import process_excel_file  # Relative import
import asyncio

async def main():
    file_path = sys.argv[1]  # Get the file path from command line arguments
    owner_id = "1"
    print('initial file:',file_path)
    try:
        result = await process_excel_file(file_path, owner_id)
        print('res',result)  # Print the result to stdout
    except Exception as e:
        print(f"Error processing file: {str(e)}")  # Print any errors to stdout

asyncio.run(main())
