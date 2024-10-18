import imaplib
import email
from email.header import decode_header
import re


def decode_if_bytes(value, encoding="utf-8"):
    if isinstance(value, bytes):
        try:
            return value.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            try:
                return value.decode("latin-1")
            except:
                return value.decode(errors="ignore")
    return value


def get_email_body(email_message):
    if email_message.is_multipart():
        for part in email_message.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if "attachment" in content_disposition:
                continue
            if content_type == "text/plain" or content_type == "text/html":
                try:
                    body = part.get_payload(decode=True)
                    return decode_if_bytes(body)
                except Exception as e:
                    print(f"Failed to decode part: {e}")
                    return None
    else:
        try:
            body = email_message.get_payload(decode=True)
            return decode_if_bytes(body)
        except Exception as e:
            print(f"Failed to decode body: {e}")
            return None


def fetch_emails_from_all_folders(imap, target_sender):
    status, folders = imap.list()
    if status != "OK":
        print("Failed to retrieve folders")
        return None

    for folder in folders:
        folder_name = folder.decode().split(' "/" ')[-1]
        imap.select(folder_name)
        print(f"\nSearching for emails from {target_sender} in {folder_name}...")
        _, all_messages = imap.search(None, "ALL")
        all_messages = all_messages[0].split()
        for num in all_messages:
            _, msg_data = imap.fetch(num, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    email_body = response_part[1]
                    email_message = email.message_from_bytes(email_body)
                    sender = decode_header(email_message.get("From", ""))[0][0]
                    sender = decode_if_bytes(sender)
                    if target_sender in sender:
                        subject = decode_header(email_message.get("Subject", ""))[0][0]
                        subject = decode_if_bytes(subject)
                        date = email_message.get("Date", "")
                        body = get_email_body(email_message)
                        return body
    return None


def get_specific_email_senders(username, password, target_sender, mail_type):
    mail_type = username.split("@")[1]
    
    if mail_type == "rambler.ru":
        imap_server = "imap.rambler.ru"
    elif mail_type == "hotmail.com" or mail_type == "outlook.com":
        imap_server = "outlook.office365.com"
    else:
        imap_server = "imap.firstmail.ltd"

    imap = imaplib.IMAP4_SSL(imap_server)
    try:
        imap.login(username, password)
        body = fetch_emails_from_all_folders(imap, target_sender)
    except imaplib.IMAP4.error as e:
        print(f"An IMAP error occurred: {str(e)}")
    except Exception as e:
        print(f"An unexpected error occurred: {str(e)}")
    finally:
        try:
            imap.close()
            imap.logout()
            return body
        except:
            pass


def extract_link_from_body(body):
    pattern = r'class="maillink">(https?://[^\s]+)</a></p>'
    match = re.search(pattern, body)
    if match:
        return match.group(1)
    return None


def get_verification_link(username, password, target_sender="hello@dawninternet.com", mail_type="outlook"):
    body = get_specific_email_senders(username, password, target_sender, mail_type)
    link = extract_link_from_body(body)
    print(f"Extracted link: {link}")
    return link


#test
if __name__ == "__main__":
    username = "yghdkcii@fumesmail.com"
    password = "nkirqlomX!7839"
    target_sender = "hello@dawninternet.com"
    mail_type = username.split("@")[1]
    get_verification_link(username, password, target_sender)