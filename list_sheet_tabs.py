#!/usr/bin/env python3
"""Pomocniczy skrypt: wypisuje dokładne nazwy zakładek w arkuszu Google."""
import os
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

creds = service_account.Credentials.from_service_account_file(
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
    scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
)
service = build("sheets", "v4", credentials=creds)

meta = service.spreadsheets().get(
    spreadsheetId=os.environ["GOOGLE_SHEET_ID"],
    fields="sheets.properties.title",
).execute()

print("Zakładki w tym arkuszu:")
for sheet in meta.get("sheets", []):
    title = sheet["properties"]["title"]
    print(f"  -> '{title}'  (użyj: GOOGLE_SHEET_RANGE={title}!A:F)")
