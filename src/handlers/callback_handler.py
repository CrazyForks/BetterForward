"""Callback query handling module."""

import json

from telebot import types

from src.config import logger, _


class CallbackHandler:
    """Handles callback queries from inline keyboards."""

    def __init__(self, bot, group_id: int, admin_handler, command_handler, captcha_manager):
        self.bot = bot
        self.group_id = group_id
        self.admin_handler = admin_handler
        self.command_handler = command_handler
        self.captcha_manager = captcha_manager

    def handle_callback_query(self, call: types.CallbackQuery):
        """Main callback query handler."""
        if call.data == "null":
            logger.error(_("Invalid callback data received"))
            return

        try:
            data = json.loads(call.data)
            action = data["action"]
        except json.JSONDecodeError:
            logger.error(_("Invalid JSON data received"))
            return

        self.bot.answer_callback_query(call.id)

        # User end callbacks
        if action == "verify_button":
            self._handle_verify_button(call, data)
            return

        # Admin end callbacks
        if call.message.chat.id != self.group_id:
            return

        self._handle_admin_callback(call, action, data)

    def _handle_verify_button(self, call: types.CallbackQuery, data: dict):
        """Handle button captcha verification."""
        user_id = data.get("user_id")
        if user_id:
            import sqlite3
            db_path = "./data/storage.db"
            with sqlite3.connect(db_path) as db:
                self.captcha_manager.set_user_verified(user_id, db)
            self.bot.answer_callback_query(call.id)
            self.bot.send_message(user_id, _("Verification successful, you can now send messages"))
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        else:
            self.bot.answer_callback_query(call.id)
            self.bot.send_message(call.message.chat.id, _("Invalid user ID"))

    def _handle_admin_callback(self, call: types.CallbackQuery, action: str, data: dict):
        """Handle admin callbacks."""
        markup = types.InlineKeyboardMarkup()
        back_button = types.InlineKeyboardButton("⬅️" + _("Back"),
                                                 callback_data=json.dumps({"action": "menu"}))

        match action:
            case "menu":
                self.admin_handler.menu(call.message, edit=True)
            case "auto_reply":
                self.admin_handler.auto_reply_menu(call.message)
            case "set_auto_response_time":
                self.admin_handler.handle_auto_response_time_callback(call.message, data)
            case "start_add_auto_reply":
                self.admin_handler.add_auto_response(call.message)
            case "add_auto_reply":
                self.admin_handler.process_add_auto_reply(call.message)
            case "manage_auto_reply":
                self.admin_handler.manage_auto_reply(call.message, page=data.get("page", 1))
            case "select_auto_reply":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.select_auto_reply(call.message, data["id"])
            case "delete_auto_reply":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.delete_auto_reply(call.message, data["id"])
            case "ban_user":
                self.admin_handler.manage_ban_user(call.message)
            case "unban_user":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.command_handler.unban_user(call.message, user_id=data["id"])
            case "select_ban_user":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.select_ban_user(call.message, data["id"])
            case "default_msg":
                self.admin_handler.default_msg_menu(call.message)
            case "edit_default_msg":
                self.admin_handler.edit_default_msg(call.message)
            case "empty_default_msg":
                self.admin_handler.empty_default_msg(call.message)
            case "captcha_settings":
                self.admin_handler.captcha_settings_menu(call.message)
            case "set_captcha":
                self.admin_handler.set_captcha(call.message, data["value"])
            case "broadcast_message":
                self.admin_handler.broadcast_message(call.message)
            case "confirm_broadcast":
                self.bot.delete_message(self.group_id, call.message.message_id)
                self.admin_handler.confirm_broadcast_message(call)
            case "cancel_broadcast":
                self.bot.delete_message(self.group_id, call.message.message_id)
                self.bot.send_message(self.group_id, _("Broadcast cancelled"))
                self.admin_handler.cancel_broadcast()
            case "time_zone_settings":
                self.admin_handler.time_zone_settings_menu(call.message)
            case "confirm_terminate":
                try:
                    self.command_handler.terminate_thread(thread_id=data.get("thread_id"),
                                                          user_id=data.get("user_id"))
                except Exception:
                    logger.error(_("Failed to terminate the thread"))
                    self.bot.send_message(self.group_id, _("Failed to terminate the thread"))
            case "cancel_terminate":
                self.bot.edit_message_text(_("Operation cancelled"),
                                           call.message.chat.id, call.message.message_id)
            case _:
                logger.error(_("Invalid action received") + action)
