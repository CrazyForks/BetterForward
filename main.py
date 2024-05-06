import argparse
import gettext
import importlib
import logging
import os
import signal
import sqlite3

import telebot
from telebot.apihelper import create_forum_topic, close_forum_topic, ApiTelegramException, delete_forum_topic
from telebot.types import Message

parser = argparse.ArgumentParser(description="")
parser.add_argument("-token", type=str, required=True, help="Telegram bot token")
parser.add_argument("-group_id", type=str, required=True, help="Group ID")
parser.add_argument("-language", type=str, default="en_US", help="Language", choices=["en_US", "zh_CN", "ja_JP"])
args = parser.parse_args()

logger = logging.getLogger()
logger.setLevel("INFO")
BASIC_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
formatter = logging.Formatter(BASIC_FORMAT, DATE_FORMAT)
chlr = logging.StreamHandler()
chlr.setFormatter(formatter)
logger.addHandler(chlr)

project_root = os.path.dirname(os.path.abspath(__file__))
locale_dir = os.path.join(project_root, "locale")
gettext.bindtextdomain("BetterForward", locale_dir)
gettext.textdomain("BetterForward")
try:
    _ = gettext.translation("BetterForward", locale_dir, languages=[args.language]).gettext
except FileNotFoundError:
    _ = gettext.gettext


def handle_sigterm(*args):
    raise KeyboardInterrupt()


signal.signal(signal.SIGTERM, handle_sigterm)


class TGBot:
    def __init__(self, bot_token: str, group_id: str, db_path: str = "./data/storage.db"):
        logger.info(_("Starting BetterForward..."))
        self.group_id = int(group_id)
        self.bot = telebot.TeleBot(bot_token)
        self.bot.message_handler(commands=["auto_response"])(self.manage_auto_response)
        self.bot.message_handler(commands=["terminate"])(self.handle_terminate)
        self.bot.message_handler(func=lambda m: True, content_types=["photo", "text", "sticker", "video", "document"])(
            self.handle_messages)
        self.db_path = db_path
        self.upgrade_db()
        self.bot.set_my_commands([
            telebot.types.BotCommand("terminate", _("Terminate a thread")),
        ])
        self.check_permission()
        self.bot.infinity_polling(skip_pending=True, timeout=30)

    def get_auto_response_help(self):
        return _("Invalid command\n"
                 "Correct usage:\n"
                 "`/auto_response set <key> <value> <topic_action(0/1)>`\n"
                 "`/auto_response delete <key>`\n"
                 "`/auto_response list`")

    def manage_auto_response(self, message: Message):
        if message.chat.id == self.group_id:
            if len((msg_split := message.text.split(" "))) < 2:
                self.bot.reply_to(message, self.get_auto_response_help(), parse_mode="Markdown")
                return
            with sqlite3.connect(self.db_path) as db:
                db_cursor = db.cursor()
                match msg_split[1]:
                    case "list":
                        result = db_cursor.execute("SELECT key, value, topic_action FROM auto_response")
                        content = _("Auto response list")
                        content += "\n" + "-" * 20 + "\n"
                        for row in result.fetchall():
                            content += _("Trigger: {}\nResponse: {}\nForward message: {}\n").format(row[0], row[1],
                                                                                                    _("Enable") if row[
                                                                                                        2]
                                                                                                    else _("Disable"))
                            content += "-" * 20 + "\n"
                        self.bot.reply_to(message, content)
                        return
                    case "set":
                        if len(msg_split) not in [4, 5]:
                            self.bot.reply_to(message, self.get_auto_response_help(), parse_mode="Markdown")
                            return
                        key = msg_split[2]
                        value = msg_split[3]
                        topic_action = int(msg_split[4]) if len(msg_split) == 5 else 0
                        # Check if key exists
                        db_cursor.execute("SELECT key FROM auto_response WHERE key = ?", (key,))
                        if db_cursor.fetchone() is not None:
                            db_cursor.execute("UPDATE auto_response SET value = ?, topic_action = ? WHERE key = ?",
                                              (value, topic_action, key))
                        else:
                            db_cursor.execute("INSERT INTO auto_response (key, value, topic_action) VALUES (?, ?, ?)",
                                              (key, value, topic_action))
                        self.bot.reply_to(message, _("Auto response set"))
                    case "delete":
                        if len(msg_split) != 3:
                            self.bot.reply_to(message, self.get_auto_response_help(), parse_mode="Markdown")
                            return
                        key = msg_split[2]
                        db_cursor.execute("DELETE FROM auto_response WHERE key = ?", (key,))
                        self.bot.reply_to(message, _("Auto response deleted"))
                    case _:
                        self.bot.reply_to(message, _("Invalid operation"))
                db.commit()

    def upgrade_db(self):
        try:
            with sqlite3.connect(self.db_path) as db:
                db_cursor = db.cursor()
                db_cursor.execute("SELECT value FROM settings WHERE key = 'db_version'")
                current_version = int(db_cursor.fetchone()[0])
        except sqlite3.OperationalError:
            current_version = 0
        db_migrate_dir = "./db_migrate"
        files = [f for f in os.listdir(db_migrate_dir) if f.endswith('.py')]
        files.sort(key=lambda x: int(x.split('_')[0]))
        for file in files:
            version = int(file.split('_')[0])
            if version > current_version:
                logger.info(_("Upgrading database to version {}").format(version))
                module = importlib.import_module(f"db_migrate.{file[:-3]}")
                module.upgrade(self.db_path)
                with sqlite3.connect(self.db_path) as db:
                    db_cursor = db.cursor()
                    db_cursor.execute("UPDATE settings SET value = ? WHERE key = 'db_version'", (str(version),))
                    db.commit()

    # Get thread_id to terminate when needed
    def terminate_thread(self, thread_id=None, user_id=None):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            if thread_id is not None:
                db_cursor.execute("DELETE FROM topics WHERE thread_id = ?", (thread_id,))
                db.commit()
            elif user_id is not None:
                result = db_cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (user_id,))
                if (thread_id := result.fetchone()[0]) is not None:
                    db_cursor.execute("DELETE FROM topics WHERE user_id = ?", (user_id,))
                    db.commit()
            try:
                delete_forum_topic(chat_id=self.group_id, message_thread_id=thread_id, token=self.bot.token)
            except ApiTelegramException:
                pass
        logger.info(_("Terminating thread") + str(thread_id))

    # To terminate and totally delete the topic
    def handle_terminate(self, message: Message):
        if message.chat.id == self.group_id:
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
            try:
                self.terminate_thread(thread_id=thread_id, user_id=user_id)
                if message.message_thread_id is None:
                    self.bot.reply_to(message, _("Thread terminated"))
            except Exception:
                logger.error(_("Failed to terminate the thread") + str(thread_id))
                self.bot.reply_to(message, _("Failed to terminate the thread"))

    # To forward your words
    def handle_messages(self, message: Message):
        # Not responding in General topic
        if message.message_thread_id is None and message.chat.id == self.group_id:
            return
        with sqlite3.connect(self.db_path) as db:
            curser = db.cursor()
            if message.chat.id != self.group_id:
                logger.info(
                    _("Received message from {}, content: {}, type: {}").format(message.from_user.id, message.text,
                                                                                message.content_type))
                # Auto response
                result = curser.execute("SELECT value, topic_action FROM auto_response WHERE key = ? LIMIT 1",
                                        (message.text,))
                if (result := result.fetchone()) is None:
                    auto_response, topic_action = None, None
                else:
                    auto_response, topic_action = result
                if auto_response is not None:
                    self.bot.send_message(message.chat.id, auto_response)
                    if not topic_action:
                        return
                # Forward message to group
                userid = message.from_user.id
                result = curser.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (userid,))
                thread_id = result.fetchone()
                if thread_id is None:
                    # Create a new thread
                    logger.info(_("Creating a new thread for user {}").format(userid))
                    try:
                        topic = create_forum_topic(chat_id=self.group_id, name=message.from_user.first_name,
                                                   token=self.bot.token)
                    except Exception as e:
                        logger.error(e)
                        return
                    curser.execute("INSERT INTO topics (user_id, thread_id) VALUES (?, ?)",
                                   (userid, topic["message_thread_id"]))
                    db.commit()
                    thread_id = topic["message_thread_id"]
                    username = _("Not set") if message.from_user.username is None else f"@{message.from_user.username}"
                    last_name = "" if message.from_user.last_name is None else f" {message.from_user.last_name}"
                    pin_message = self.bot.send_message(self.group_id,
                                                        f"User ID: {userid}\n"
                                                        f"Full Name: {message.from_user.first_name}{last_name}\n"
                                                        f"Username: {username}\n",
                                                        message_thread_id=thread_id)
                    self.bot.pin_chat_message(self.group_id, pin_message.message_id)
                try:
                    self.bot.forward_message(self.group_id, message.chat.id, message_thread_id=thread_id,
                                             message_id=message.message_id)
                except ApiTelegramException as e:
                    logger.error(_("Failed to forward message from user {}".format(message.from_user.id)) + f" {e}")
                    self.bot.send_message(self.group_id,
                                          _("Failed to forward message from user {}".format(message.from_user.id)),
                                          message_thread_id=None)
                    self.bot.forward_message(self.group_id, message.chat.id, message_id=message.message_id)
                if topic_action:
                    self.bot.send_message(self.group_id, _("[Auto Response]") + auto_response,
                                          message_thread_id=thread_id)
            else:
                # Forward message to user
                result = curser.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1",
                                        (message.message_thread_id,))
                user_id = result.fetchone()
                if user_id is not None:
                    match message.content_type:
                        case "photo":
                            self.bot.send_photo(chat_id=user_id[0], photo=message.photo[-1].file_id,
                                                caption=message.caption)
                        case "text":
                            self.bot.send_message(chat_id=user_id[0], text=message.text)
                        case "sticker":
                            self.bot.send_sticker(chat_id=user_id[0], sticker=message.sticker.file_id)
                        case "video":
                            self.bot.send_video(chat_id=user_id[0], video=message.video.file_id,
                                                caption=message.caption)
                        case "document":
                            self.bot.send_document(chat_id=user_id[0], document=message.document.file_id,
                                                   caption=message.caption)
                        case _:
                            logger.error(_("Unsupported message type") + message.content_type)
                else:
                    self.bot.send_message(self.group_id, _("Chat not found, please remove this topic manually"),
                                          message_thread_id=message.message_thread_id)
                    close_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id,
                                      token=self.bot.token)

    def check_permission(self):
        chat_member = self.bot.get_chat_member(self.group_id, self.bot.get_me().id)
        permissions = {
            _("Manage Topics"): chat_member.can_manage_topics,
            _("Delete Messages"): chat_member.can_delete_messages
        }
        for key, value in permissions.items():
            if value is False:
                logger.error(_("Bot doesn't have {} permission").format(key))
                self.bot.send_message(self.group_id, _("Bot doesn't have {} permission").format(key))
        self.bot.send_message(self.group_id, _("Bot started successfully"))


if __name__ == "__main__":
    if not args.token or not args.group_id:
        logger.error(_("Token or group ID is empty"))
        exit(1)
    try:
        bot = TGBot(args.token, args.group_id)
    except KeyboardInterrupt:
        logger.info(_("Exiting..."))
        exit(0)
