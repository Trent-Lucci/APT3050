import os
import mysql.connector
from mysql.connector import Error


def get_db_connection():
  return mysql.connector.connect(
      host=os.getenv("DB_HOST", "localhost"),
      user=os.getenv("DB_USER", "root"),
      password=os.getenv("DB_PASSWORD", ""),
      database=os.getenv("DB_NAME", "usiu_lab_guard"),
  )


if __name__ == "__main__":
  try:
    with get_db_connection() as conn:
      if conn.is_connected():
        print("Connected to XAMPP MySQL successfully!")
  except Error as e:
    print(f"Database connection failed: {e}")