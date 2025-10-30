"""Command handling module."""

import json
import sqlite3
from datetime import datetime

from telebot import types
from telebot.apihelper import ApiTelegramException, delete_forum_topic, close_forum_topic, reopen_forum_topic
from telebot.types import Message

from src.config import logger, _


class CommandHandler:
    """Handles bot commands."""

    def __init__(self, bot, group_id: int, db_path: str, cache, time_zone, captcha_manager):
        self.bot = bot
        self.group_id = group_id
        self.db_path = db_path
        self.cache = cache
        self.time_zone = time_zone
        self.captcha_manager = captcha_manager

    def check_valid_chat(self, message: Message) -> bool:
        """Check if message is in valid chat context."""
        return message.chat.id == self.group_id and message.message_thread_id is None

    def help_command(self, message: Message, menu_callback):
        """Handle /help and /start commands."""
        if self.check_valid_chat(message):
            menu_callback(message)
        else:
            default_message = self._get_setting('default_message')
            if default_message is None:
                self.bot.send_message(message.chat.id,
                                      _("I'm a bot that forwards messages, so please just tell me what you want to say.") + "\n" +
                                      "Powered by [BetterForward](https://github.com/SideCloudGroup/BetterForward)",
                                      parse_mode="Markdown",
                                      disable_web_page_preview=True)
            else:
                self.bot.send_message(message.chat.id, default_message)

    def ban_user(self, message: Message):
        """Ban a user from sending messages."""
        if message.chat.id == self.group_id and message.message_thread_id is None:
            self.bot.send_message(self.group_id, _("This command is not available in the main chat."))
            return
        if message.chat.id != self.group_id:
            self.bot.send_message(message.chat.id, _("This command is only available to admin users."))
            return

        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("UPDATE topics SET ban = 1 WHERE thread_id = ?", (message.message_thread_id,))
            # Remove user from verified list
            db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1",
                              (message.message_thread_id,))
            if (user_id := db_cursor.fetchone()) is not None:
                db_cursor.execute("DELETE FROM verified_users WHERE user_id = ?", (user_id[0],))
            db.commit()

        self.bot.send_message(self.group_id, _("User banned"), message_thread_id=message.message_thread_id)
        close_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id,
                          token=self.bot.token)

    def unban_user(self, message: Message, user_id: int = None):
        """Unban a user."""
        if message.chat.id != self.group_id:
            self.bot.send_message(message.chat.id, _("This command is only available to admin users."))
            return

        if user_id is None:
            if self.check_valid_chat(message):
                if len((msg_split := message.text.split(" "))) != 2:
                    self.bot.reply_to(message, "Invalid command\n"
                                               "Correct usage:```\n"
                                               "/unban <user ID>```", parse_mode="Markdown")
                    return
                user_id = int(msg_split[1])

        if user_id is None:
            with sqlite3.connect(self.db_path) as db:
                db_cursor = db.cursor()
                db_cursor.execute("UPDATE topics SET ban = 0 WHERE thread_id = ?",
                                  (message.message_thread_id,))
                db.commit()
            self.bot.send_message(self.group_id, _("User unbanned"),
                                  message_thread_id=message.message_thread_id)
            try:
                reopen_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id,
                                   token=self.bot.token)
            except ApiTelegramException:
                pass
        else:
            with sqlite3.connect(self.db_path) as db:
                db_cursor = db.cursor()
                db_cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (user_id,))
                thread_id = db_cursor.fetchone()
                if thread_id is None:
                    self.bot.send_message(self.group_id, _("User not found"))
                    return
                db_cursor.execute("UPDATE topics SET ban = 0 WHERE user_id = ?", (user_id,))
                db.commit()
            try:
                reopen_forum_topic(chat_id=self.group_id, message_thread_id=thread_id[0],
                                   token=self.bot.token)
            except ApiTelegramException:
                pass
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "menu"})))
            if message.from_user.id == self.bot.get_me().id:
                self.bot.edit_message_text(_("User unbanned"), message.chat.id, message.message_id,
                                           reply_markup=markup)
            else:
                self.bot.send_message(self.group_id, _("User unbanned"), reply_markup=markup)

    def terminate_thread(self, thread_id=None, user_id=None):
        """Terminate and delete a thread."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            if thread_id is not None:
                result = db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1",
                                           (thread_id,))
                if (user_id := result.fetchone()) is not None:
                    user_id = user_id[0]
                    db_cursor.execute("DELETE FROM topics WHERE thread_id = ?", (thread_id,))
                    db.commit()
            elif user_id is not None:
                result = db_cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1",
                                           (user_id,))
                if (thread_id := result.fetchone()) is not None:
                    thread_id = thread_id[0]
                    db_cursor.execute("DELETE FROM topics WHERE user_id = ?", (user_id,))
                    db.commit()

            if user_id and thread_id:
                self.cache.delete(f"chat_{user_id}_threadid")
                self.cache.delete(f"threadid_{thread_id}_userid")
                try:
                    delete_forum_topic(chat_id=self.group_id, message_thread_id=thread_id,
                                       token=self.bot.token)
                except ApiTelegramException:
                    pass
                db_cursor.execute("DELETE FROM messages WHERE topic_id = ?", (thread_id,))
                db.commit()
        logger.info(_("Terminating thread") + str(thread_id))

    def handle_terminate(self, message: Message):
        """Handle /terminate command."""
        if (message.chat.id == self.group_id) and (
                self.bot.get_chat_member(message.chat.id, message.from_user.id).status in ["administrator", "creator"]):
            user_id = None
            thread_id = None
            if message.message_thread_id is None:
                if len((msg_split := message.text.split(" "))) != 2:
                    self.bot.reply_to(message, "Invalid command\n"
                                               "Correct usage:```\n"
                                               "/terminate <user ID>```", parse_mode="Markdown")
                    return
                user_id = int(msg_split[1])
            else:
                thread_id = message.message_thread_id
            if thread_id == 1:
                self.bot.reply_to(message, _("Cannot terminate main thread"))
                return
            markup = types.InlineKeyboardMarkup()
            confirm_button = types.InlineKeyboardButton(
                f"✅{_('Confirm')}",
                callback_data=json.dumps(
                    {"action": "confirm_terminate", "thread_id": thread_id} if thread_id is not None else
                    {"action": "confirm_terminate", "user_id": user_id}
                )
            )
            cancel_button = types.InlineKeyboardButton(
                f"❌{_('Cancel')}",
                callback_data=json.dumps({"action": "cancel_terminate"})
            )
            markup.add(confirm_button, cancel_button)
            self.bot.reply_to(message, _("Are you sure you want to terminate this thread?"),
                              reply_markup=markup)
        else:
            self.bot.send_message(message.chat.id, _("This command is only available to admin users."))

    def delete_message(self, message: Message):
        """Delete a forwarded message."""
        if self.check_valid_chat(message):
            return
        if message.reply_to_message is None:
            self.bot.reply_to(message, _("Please reply to the message you want to delete"))
            return

        msg_id = message.reply_to_message.message_id
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute(
                "SELECT topic_id, forwarded_id FROM messages WHERE received_id = ? AND in_group = ? LIMIT 1",
                (msg_id, message.chat.id == self.group_id))
            if (result := db_cursor.fetchone()) is None:
                return
            topic_id, forwarded_id = result
            if message.chat.id == self.group_id:
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
                if (user_id := db_cursor.fetchone()) is None or user_id[0] is None:
                    return
                self.bot.delete_message(chat_id=user_id[0], message_id=forwarded_id)
            else:
                self.bot.send_message(chat_id=self.group_id,
                                      text=_("[Alert]") + _("Message deleted by user"),
                                      reply_to_message_id=forwarded_id)
            # Delete the message from the database
            db_cursor.execute("DELETE FROM messages WHERE received_id = ? AND in_group = ?",
                              (msg_id, message.chat.id == self.group_id))
            db.commit()

        # Delete the current message
        self.bot.delete_message(chat_id=message.chat.id, message_id=message.reply_to_message.id)
        self.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    def handle_verify(self, message: Message):
        """Handle /verify command to manually set verification status."""
        if message.chat.id != self.group_id or message.message_thread_id is None:
            return

        command_parts = message.text.split()
        if len(command_parts) != 2 or command_parts[1].lower() not in ["true", "false"]:
            self.bot.send_message(message.chat.id,
                                  _("Invalid command format.\nUse /verify <true/false>"),
                                  message_thread_id=message.message_thread_id)
            return

        verified_status = command_parts[1].lower() == "true"
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ?",
                              (message.message_thread_id,))
            user_id = db_cursor.fetchone()
            if user_id is None:
                self.bot.send_message(message.chat.id, _("User not found"),
                                      message_thread_id=message.message_thread_id)
                return
            user_id = user_id[0]
            if verified_status:
                self.captcha_manager.set_user_verified(user_id, db)
                self.bot.send_message(message.chat.id, _("User verified successfully."),
                                      message_thread_id=message.message_thread_id)
            else:
                self.captcha_manager.remove_user_verification(user_id, db)
                self.bot.send_message(message.chat.id, _("User verification removed."),
                                      message_thread_id=message.message_thread_id)

    def handle_edit(self, message: Message):
        """Handle edited messages."""
        if self.check_valid_chat(message):
            return

        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute(
                "SELECT topic_id, forwarded_id FROM messages WHERE received_id = ? AND in_group = ? LIMIT 1",
                (message.message_id, message.chat.id == self.group_id))
            if (result := db_cursor.fetchone()) is None:
                return
            topic_id, forwarded_id = result
            edit_time = datetime.now().astimezone(self.time_zone).strftime("%Y-%m-%d %H:%M:%S")
            edited_text = message.text + f"\n\n({_('Edited at')} {edit_time})"

            if message.chat.id == self.group_id:
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
                if (user_id := db_cursor.fetchone()) is None or user_id[0] is None:
                    return
                if message.content_type == "text":
                    self.bot.edit_message_text(chat_id=user_id[0], message_id=forwarded_id,
                                               text=edited_text)
            else:
                if message.content_type == "text":
                    self.bot.edit_message_text(chat_id=self.group_id, message_id=forwarded_id,
                                               text=edited_text)

    def handle_reaction(self, message):
        """Handle message reactions."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute(
                "SELECT topic_id, received_id FROM messages WHERE forwarded_id = ? LIMIT 1",
                (message.message_id,))
            if (result := db_cursor.fetchone()) is None:
                db_cursor.execute(
                    "SELECT topic_id, forwarded_id FROM messages WHERE received_id = ? LIMIT 1",
                    (message.message_id,))
                if (result := db_cursor.fetchone()) is None:
                    return
            topic_id, forwarded_id = result
            if message.chat.id == self.group_id:
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
                if (chat_id := db_cursor.fetchone()) is None or chat_id[0] is None:
                    return
                chat_id = chat_id[0]
            else:
                chat_id = self.group_id
            self.bot.set_message_reaction(chat_id=chat_id, message_id=forwarded_id,
                                          reaction=[message.new_reaction[-1]] if message.new_reaction else [])

    def _get_setting(self, key: str):
        """Get a setting from the database."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT value FROM settings WHERE key = ? LIMIT 1", (key,))
            result = db_cursor.fetchone()
            return result[0] if result else None
