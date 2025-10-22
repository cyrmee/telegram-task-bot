import os
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from google import genai

from constants import (
    AI_MODEL,
    AI_TEMPERATURE,
    AI_MIME_TYPE,
    AI_REQUIRED_KEYS,
    AI_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)


class TaskParser:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")

        self.client = genai.Client(api_key=self.api_key)

    def parse_task_description(self, text: str, available_users: List[Dict]) -> Dict:
        user_list_text = "\n".join(
            [
                (
                    f"- @{user['username']} (ID: {user['id']})"
                    if user["username"]
                    else f"- {user['first_name']} {user['last_name'] or ''} (ID: {user['id']})"
                )
                for user in available_users
            ]
        )

        prompt = AI_PROMPT_TEMPLATE.format(user_list_text=user_list_text, text=text)

        try:
            response = self.client.models.generate_content(
                model=AI_MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type=AI_MIME_TYPE,
                    temperature=AI_TEMPERATURE,
                ),
            )

            # Parse the JSON response
            if hasattr(response, "text") and response.text:
                result = json.loads(response.text.strip())
            else:
                raise ValueError("No response text received from AI")

            if not all(key in result for key in AI_REQUIRED_KEYS):
                raise ValueError("Missing required keys in AI response")

            due_date = self._parse_relative_date(
                result["due_date_relative"], result["due_time"]
            )

            if due_date <= datetime.now(timezone.utc):
                raise ValueError(f"Due date must be in the future. Parsed: {due_date}")

            result["due_date"] = due_date.strftime("%Y-%m-%d %H:%M")

            logger.info(
                f"Successfully parsed task: {result['task_name']} for users {result['usernames']}"
            )
            return result

        except Exception as e:
            logger.error(f"Error parsing task with AI: {e}")
            raise ValueError(f"Failed to parse task description: {str(e)}")

    def _calculate_weekday_offset(
        self, target_weekday: int, current_weekday: int
    ) -> int:
        days_ahead = (target_weekday - current_weekday) % 7
        return days_ahead if days_ahead > 0 else 7

    def _parse_relative_date(self, relative_date: str, time_str: str) -> datetime:
        now = datetime.now(timezone.utc)
        base_date = now.replace(hour=0, minute=0, second=0, microsecond=0)

        try:
            hour, minute = map(int, time_str.split(":"))
        except ValueError:
            hour, minute = 9, 0

        relative_lower = relative_date.lower().strip()

        day_mappings = {
            "today": 0,
            "tomorrow": 1,
            "day after tomorrow": 2,
            "in 2 days": 2,
            "in 3 days": 3,
            "in 4 days": 4,
            "in 5 days": 5,
            "in 6 days": 6,
            "next week": 7,
            "in 2 weeks": 14,
            "in 3 weeks": 21,
        }

        if relative_lower in day_mappings:
            days_ahead = day_mappings[relative_lower]
        elif relative_lower.startswith("next "):
            weekday_map = {
                "next monday": 0,
                "next tuesday": 1,
                "next wednesday": 2,
                "next thursday": 3,
                "next friday": 4,
                "next saturday": 5,
                "next sunday": 6,
            }
            target_weekday = weekday_map.get(relative_lower)
            if target_weekday is not None:
                days_ahead = self._calculate_weekday_offset(
                    target_weekday, now.weekday()
                )
            else:
                days_ahead = 1
        else:
            try:
                if relative_lower.startswith("in ") and relative_lower.endswith(
                    " days"
                ):
                    days_ahead = int(relative_lower.split()[1])
                else:
                    days_ahead = 1
            except ValueError:
                days_ahead = 1

        target_date = base_date + timedelta(days=days_ahead)
        return target_date.replace(hour=hour, minute=minute)
