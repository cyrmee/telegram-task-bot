"""
AI-powered natural language parsing for task creation using Google Gemini.
"""

import os
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from google import genai
from pytz import timezone

logger = logging.getLogger(__name__)


class TaskParser:
    """
    Parses natural language task descriptions using Google Gemini AI.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the TaskParser with Gemini API.

        Args:
            api_key (str, optional): Gemini API key. If not provided, uses GEMINI_API_KEY env var.
        """
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")

        self.client = genai.Client(api_key=self.api_key)

    def parse_task_description(self, text: str, available_users: List[Dict]) -> Dict:
        """
        Parse natural language task description into structured data.

        Args:
            text (str): Natural language task description
            available_users (list): List of available users with their info

        Returns:
            dict: Parsed task data with keys: task_name, usernames, due_date, confidence
        """
        # Create a prompt for Gemini to parse the task
        user_list_text = "\n".join(
            [
                f"- @{user['username']} (ID: {user['id']})"
                for user in available_users
                if user["username"]
            ]
        )

        prompt = f"""
You are a task parsing assistant for a Telegram bot. Parse the following natural language task description into structured JSON format.

Available users in the group:
{user_list_text}

Task description: "{text}"

Please extract:
1. task_name: The name/description of the task
2. usernames: Array of usernames mentioned (without @ symbol) - match against available users
3. due_date_relative: Relative date description (e.g., "tomorrow", "next week", "in 3 days", "today", "next monday")
4. due_time: Time in HH:MM format (24-hour format, default to "09:00" if not specified)
5. reminder_minutes_list: Array of minutes before due date to send reminders (default [30] if not specified)
6. confidence: Your confidence level (0.0-1.0) in the parsing accuracy

Rules:
- If no specific date is mentioned, use "tomorrow"
- If no specific time is mentioned, use "09:00"
- If no users are mentioned, return empty usernames array
- Only include usernames that exist in the available users list
- For reminders: parse multiple reminder times like "remind me 1 hour before, 30 minutes before, and 15 minutes before"
- Convert reminder times to minutes: 1 hour = 60 minutes, 30 minutes = 30 minutes, etc.
- If "no reminders" or "don't remind" is mentioned, set reminder_minutes_list to empty array []
- Default reminder_minutes_list is [30] (30 minutes before)
- Return valid JSON only

Example output:
{{
    "task_name": "Prepare presentation",
    "usernames": ["john", "jane"],
    "due_date_relative": "tomorrow",
    "due_time": "14:30",
    "reminder_minutes_list": [60, 30, 15],
    "confidence": 0.95
}}
"""

        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,  # Low temperature for consistent parsing
                ),
            )

            # Parse the JSON response
            if hasattr(response, "text") and response.text:
                result = json.loads(response.text.strip())
            else:
                raise ValueError("No response text received from AI")

            # Validate the response structure
            required_keys = [
                "task_name",
                "usernames",
                "due_date_relative",
                "due_time",
                "reminder_minutes_list",
                "confidence",
            ]
            if not all(key in result for key in required_keys):
                raise ValueError("Missing required keys in AI response")

            # Convert relative date to actual date
            due_date = self._parse_relative_date(
                result["due_date_relative"], result["due_time"]
            )

            # Check if date is in the future
            if due_date <= datetime.now(timezone.utc):
                raise ValueError(f"Due date must be in the future. Parsed: {due_date}")

            # Replace the relative date with actual date in result
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
        """Calculate days ahead to reach target weekday."""
        days_ahead = (target_weekday - current_weekday) % 7
        return days_ahead if days_ahead > 0 else 7

    def _parse_relative_date(self, relative_date: str, time_str: str) -> datetime:
        """
        Convert relative date description to actual datetime using system date.

        Args:
            relative_date (str): Relative date like "tomorrow", "next week", etc.
            time_str (str): Time in HH:MM format

        Returns:
            datetime: Actual datetime object
        """
        now = datetime.now(timezone.utc)
        base_date = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Parse the time
        try:
            hour, minute = map(int, time_str.split(":"))
        except ValueError:
            hour, minute = 9, 0

        relative_lower = relative_date.lower().strip()

        # Simple day mappings
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
            # Try to parse "in X days"
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
