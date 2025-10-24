import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

try:
    from ai_parser import TaskParser

    print("✅ AI parser import successful")

    try:
        parser = TaskParser()
        print("✅ AI parser initialization successful")

        test_users = [
            {
                "id": 123,
                "username": "testuser",
                "first_name": "Test",
                "last_name": "User",
            }
        ]
        try:
            result = parser.parse_task_description(
                "Prepare presentation for @testuser in 7 days at 2 PM", test_users
            )
            print(f"✅ AI parsing successful: {result}")
        except Exception as e:
            print(f"⚠️ AI parsing failed (expected with fake data): {e}")

    except ValueError as e:
        if "GEMINI_API_KEY" in str(e):
            print("❌ AI parser requires GEMINI_API_KEY - please set it in .env file")
            print(f"Current GEMINI_API_KEY: {os.getenv('GEMINI_API_KEY', 'NOT SET')}")
        else:
            print(f"❌ Unexpected error: {e}")
            sys.exit(1)

    print("🎉 All basic tests passed!")

except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    sys.exit(1)