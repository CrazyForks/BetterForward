"""Captcha functionality for BetterForward."""

import json
import random

import httpx
from diskcache import Cache
from telebot import types

from src.config import _, logger


class CaptchaManager:
    """Manages captcha generation and verification."""

    def __init__(self, bot, cache: Cache, group_id: int = None):
        self.bot = bot
        self.cache = cache
        self.group_id = group_id

    def generate_captcha(self, user_id: int, captcha_type: str = "math"):
        """Generate a captcha for the user."""
        match captcha_type:
            case "math":
                num1 = random.randint(1, 10)
                num2 = random.randint(1, 10)
                answer = num1 + num2
                self.cache.set(f"captcha_{user_id}", answer, 300)
                return f"{num1} + {num2} = ?"
            case "button":
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(
                    "Click to verify",
                    callback_data=json.dumps({"action": "verify_button", "user_id": user_id})
                ))
                self.bot.send_message(user_id, _("Please click the button to verify."),
                                      reply_markup=markup)
                return None
            case "tguard":
                return self._generate_tguard_captcha(user_id)
            case _:
                raise ValueError(_("Invalid captcha setting"))
    
    def _generate_tguard_captcha(self, user_id: int):
        """Generate TGuard verification request."""
        api_url = self.cache.get("setting_tguard_api_url")
        api_key = self.cache.get("setting_tguard_api_key")
        
        if not api_url or not api_key:
            error_msg = _("TGuard API URL or Key not configured")
            logger.error(error_msg)
            # Notify in group if group_id is available
            if self.group_id:
                try:
                    self.bot.send_message(
                        self.group_id,
                        f"⚠️ {error_msg}",
                        message_thread_id=None
                    )
                except Exception:
                    pass  # Ignore if sending to group fails
            raise ValueError(_("TGuard API not configured"))
        
        try:
            # Create verification request
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    f"{api_url.rstrip('/')}/api/verification/create",
                    json={"user_id": user_id},
                    headers={"X-API-Key": api_key}
                )
                response.raise_for_status()
                data = response.json()
                
                token = data.get("token")
                verification_url = data.get("verification_url")
                
                if not token or not verification_url:
                    error_msg = _("Invalid response from TGuard API")
                    logger.error(error_msg)
                    # Notify in group if group_id is available
                    if self.group_id:
                        try:
                            self.bot.send_message(
                                self.group_id,
                                f"⚠️ TGuard验证错误：{error_msg}",
                                message_thread_id=None
                            )
                        except Exception:
                            pass
                    raise ValueError(error_msg)
                
                # Store token for verification status check (when user sends next message)
                self.cache.set(f"tguard_token_{user_id}", token, 600)  # 10 minutes
                
                # Send verification button with Mini Web App
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(
                    _("🔐 Complete Verification"),
                    web_app=types.WebAppInfo(url=verification_url)
                ))
                
                self.bot.send_message(
                    user_id,
                    _("Please complete the verification by clicking the button below.\n"
                      "After completing verification, send a message to check your verification status."),
                    reply_markup=markup
                )
                
                # No background polling - verification status will be checked when user sends next message
                
                return None
        except httpx.HTTPStatusError as e:
            error_msg = _("TGuard API error: {}").format(e)
            logger.error(error_msg)
            # Notify in group if group_id is available
            if self.group_id:
                try:
                    self.bot.send_message(
                        self.group_id,
                        f"⚠️ TGuard API错误：{str(e)}\n用户ID：{user_id}",
                        message_thread_id=None
                    )
                except Exception:
                    pass
            raise ValueError(_("Failed to create verification request"))
        except Exception as e:
            error_msg = _("TGuard verification error: {}").format(e)
            logger.error(error_msg)
            # Notify in group if group_id is available
            if self.group_id:
                try:
                    self.bot.send_message(
                        self.group_id,
                        f"⚠️ TGuard验证系统错误：{str(e)}\n用户ID：{user_id}",
                        message_thread_id=None
                    )
                except Exception:
                    pass
            raise ValueError(_("Failed to create verification request"))
    
    def check_tguard_verification_status(self, user_id: int) -> bool:
        """
        Check TGuard verification status immediately.
        Called when user sends a message to check if verification is completed.
        Returns True if verification is completed, False otherwise.
        """
        token = self.cache.get(f"tguard_token_{user_id}")
        if not token:
            return False
        
        api_url = self.cache.get("setting_tguard_api_url")
        if not api_url:
            return False
        
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(
                    f"{api_url.rstrip('/')}/api/v1/verification-status/{token}"
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get("completed"):
                        # Verification completed, mark user as verified
                        import sqlite3
                        db_path = "./data/storage.db"
                        with sqlite3.connect(db_path) as db:
                            self.set_user_verified(user_id, db)
                        # Send success message to user
                        try:
                            self.bot.send_message(user_id, _("✅ Verification successful! You can now send messages."))
                        except Exception:
                            pass  # User might have blocked the bot, ignore
                        self.cache.delete(f"tguard_token_{user_id}")
                        logger.info(_("User {} completed TGuard verification").format(user_id))
                        return True
                elif response.status_code == 404:
                    # Token not found or expired
                    logger.warning(_("TGuard verification token expired for user {}").format(user_id))
                    self.cache.delete(f"tguard_token_{user_id}")
                    return False
        except Exception as e:
            logger.error(_("Error checking TGuard verification status: {}").format(e))
        
        return False


    def verify_captcha(self, user_id: int, answer: str) -> bool:
        """Verify a captcha answer."""
        captcha = self.cache.get(f"captcha_{user_id}")
        if captcha is None:
            return False
        return str(answer) == str(captcha)

    def is_user_verified(self, user_id: int, db) -> bool:
        """Check if a user is verified."""
        verified = self.cache.get(f"verified_{user_id}")
        if verified is None:
            cursor = db.cursor()
            result = cursor.execute("SELECT 1 FROM verified_users WHERE user_id = ? LIMIT 1",
                                    (user_id,))
            verified = result.fetchone() is not None
            self.cache.set(f"verified_{user_id}", verified, 1800)
        return verified

    def set_user_verified(self, user_id: int, db):
        """Mark a user as verified."""
        cursor = db.cursor()
        cursor.execute("INSERT OR REPLACE INTO verified_users (user_id) VALUES (?)", (user_id,))
        db.commit()
        self.cache.set(f"verified_{user_id}", True, 1800)

    def remove_user_verification(self, user_id: int, db):
        """Remove user verification status."""
        cursor = db.cursor()
        cursor.execute("DELETE FROM verified_users WHERE user_id = ?", (user_id,))
        db.commit()
        self.cache.delete(f"verified_{user_id}")
