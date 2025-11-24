import os
import asyncio
import logging
from collections import deque
from pyrogram import filters
from pyrogram.types import Message
from pyrogram.enums import ChatAction, UserStatus, ChatType
from .base_module import BaseModule


class SmartAutoReplyModule(BaseModule):
    def __init__(self, client, socketio):
        super().__init__(client, socketio)
        self.pending_replies = {}
        self.conversation_mode = {}

        if os.getenv('GEMINI_API_KEY', ''):
            self.auto_reply_message = "𝑰 𝒎𝒂𝒚𝒃𝒆 𝒃𝒖𝒔𝒚 𝒏𝒐𝒘. 💝\n\n💬 আপনি চাইলে আমাকে কিছু জিজ্ঞাসা করতে পারেন, আমি AI দিয়ে উত্তর দেওয়ার চেষ্টা করব। \n 💝 𝑻𝒉𝒂𝒏𝒌 𝑼 💝"
        else:
            self.auto_reply_message = "𝑰 𝒎𝒂𝒚𝒃𝒆 𝒃𝒖𝒔𝒚 𝒏𝒐𝒘. 💝\n\n⚠️ Note: AI features are currently disabled (GEMINI_API_KEY not configured).\n\n 💝 𝑻𝒉𝒂𝒏𝒌 𝑼 💝"

        self.programmatic_message_count = 0
        self._programmatic_lock = asyncio.Lock()

        self.reply_timeout = 120  
        self.group_reply_timeout = 120  
        self.conversation_history = {}  
        self.max_history_length = 50
        self.pending_group_replies = {}

        self.api_key = os.getenv('GEMINI_API_KEY' , '')
        if not self.api_key:
            logging.error("❌ GEMINI_API_KEY environment variable not set! AI features will not work.")
            self.api_url = None
        else:
            self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.api_key}"

        if self.api_key:
            logging.info("✅ Using Gemini API key from environment variables")

    def setup(self):
        @self.client.on_message(filters.private & filters.command("clear") & filters.incoming)
        async def handle_clear_command(client, message: Message):
            chat_id = message.chat.id
            if chat_id in self.conversation_history:
                self.conversation_history[chat_id].clear()
                await message.reply_text("✅ **Conversation history cleared!**\n\nনতুন কথোপকথন শুরু হবে এখন থেকে। 🔄")
                logging.info(f"🗑️ Conversation history cleared for {message.from_user.first_name}")
                self.emit_terminal(f'🗑️ History cleared for {message.from_user.first_name}')
            else:
                await message.reply_text("ℹ️ কোন conversation history নেই এই chat এ।")

        @self.client.on_message(filters.private & filters.command("stop") & filters.outgoing)
        async def handle_stop_command(client, message: Message):
            """Stop all conversation modes and pending replies."""
            self.conversation_mode.clear()
            self.pending_replies.clear()
            logging.info("🛑 All conversation modes stopped")
            self.emit_terminal("🛑 Conversation modes stopped")
            await message.edit_text("🛑 **Auto-reply Stopped**\n\nসব conversation mode বন্ধ করা হয়েছে।")

        @self.client.on_message(filters.group & filters.text & filters.incoming & filters.mentioned)
        async def handle_group_mention(client, message: Message):
            try:
                user = message.from_user
                chat_id = message.chat.id
                msg_id = message.id
                group_name = message.chat.title or "Group"

                logging.info(f"👥 Mentioned in group '{group_name}' by {user.first_name}")
                self.emit_terminal(f'👥 Mentioned in {group_name} by {user.first_name}')

                group_key = f"{chat_id}_{msg_id}"

                if group_key in self.pending_group_replies:
                    return

                logging.info(f"📨 New group mention from {user.first_name} - Waiting {self.group_reply_timeout}s for reply")
                self.emit_terminal(f'⏰ Group mention: Waiting {self.group_reply_timeout}s...')

                async def send_delayed_group_reply():
                    try:
                        logging.info(f"⏰ Waiting {self.group_reply_timeout} seconds before group auto-reply...")
                        await asyncio.sleep(self.group_reply_timeout)

                        if group_key not in self.pending_group_replies:
                            logging.info("❌ Group reply was cancelled")
                            return

                        logging.info(f"📤 Sending auto-reply to group '{group_name}'...")
                        self.emit_terminal(f'📤 Auto-replying in {group_name}')

                        busy_message = "𝑰 𝒎𝒂𝒚𝒃𝒆 𝒃𝒖𝒔𝒚 𝒏𝒐𝒘. 💝\n\n 💬 কোন দরকার হলে ℑ𝔫𝔟𝔬𝔵 𝔪𝔢. 💝 𝑻𝒉𝒂𝒏𝒌 𝑼 💝"

                        async with self._programmatic_lock:
                            self.programmatic_message_count += 1

                        try:
                            await message.reply_text(busy_message)
                            logging.info(f"✅ Sent busy message to group '{group_name}'")
                            self.emit_terminal(f'✅ Replied in group: {group_name}')
                        finally:
                            async with self._programmatic_lock:
                                self.programmatic_message_count -= 1

                    except asyncio.CancelledError:
                        logging.info("❌ Group auto-reply cancelled by user response")
                        self.emit_terminal(f'❌ Group auto-reply cancelled')
                    except Exception as e:
                        logging.error(f"Error sending group auto-reply: {e}", exc_info=True)
                    finally:
                        if group_key in self.pending_group_replies:
                            del self.pending_group_replies[group_key]

                task = asyncio.create_task(send_delayed_group_reply())
                self.pending_group_replies[group_key] = task

            except Exception as e:
                logging.error(f"Error handling group mention: {e}", exc_info=True)

        @self.client.on_message(filters.group & filters.outgoing)
        async def handle_group_outgoing(client, message: Message):
            """Cancel pending group auto-replies when user manually replies in group."""
            try:
                async with self._programmatic_lock:
                    if self.programmatic_message_count > 0:
                        return

                chat_id = message.chat.id
                cancelled_count = 0
                keys_to_remove = []

                for key, task in self.pending_group_replies.items():
                    if key.startswith(f"{chat_id}_"):
                        task.cancel()
                        keys_to_remove.append(key)
                        cancelled_count += 1

                for key in keys_to_remove:
                    del self.pending_group_replies[key]

                if cancelled_count > 0:
                    group_name = message.chat.title or "Group"
                    logging.info(f"✅ Cancelled {cancelled_count} pending group auto-reply(s) in '{group_name}'")
                    self.emit_terminal(f'✅ Cancelled group auto-reply in {group_name}')

            except Exception as e:
                logging.error(f"Error handling group outgoing: {e}", exc_info=True)

        @self.client.on_message(filters.private & filters.text & filters.incoming)
        async def handle_incoming_message(client, message: Message):
            chat_id = message.chat.id
            msg_id = message.id
            user = message.from_user

            if message.text.startswith('/gem'):
                return

            if message.text.startswith('/'):
                logging.info(f"⏭️ Skipping auto-reply for command: {message.text}")
                self.emit_terminal(f'⚙️ Command from {user.first_name}: "{message.text}"')
                return

            logging.info(f'📨 Message from {user.first_name}: "{message.text[:50]}..."')
            self.emit_terminal(f'📨 Message from {user.first_name}: "{message.text[:50]}..."')

            if chat_id in self.conversation_mode:
                if not self.api_key:
                    logging.warning(f"⚠️ Conversation mode active but GEMINI_API_KEY not set - deactivating")
                    self.emit_terminal(f'⚠️ AI unavailable for {user.first_name}')
                    del self.conversation_mode[chat_id]

                    async with self._programmatic_lock:
                        self.programmatic_message_count += 1
                    try:
                        await message.reply_text("⚠️ AI features are currently unavailable. GEMINI_API_KEY environment variable is not configured.\n\nPlease set the API key to enable AI responses.")
                    finally:
                        async with self._programmatic_lock:
                            self.programmatic_message_count -= 1
                    return

                logging.info(f"💬 Conversation mode active for {user.first_name} - Instant AI response")
                self.emit_terminal(f'💬 AI responding to {user.first_name}')

                await client.send_chat_action(message.chat.id, ChatAction.TYPING)

                try:
                    response = await self._call_gemini_api(message.text, chat_id)

                    async with self._programmatic_lock:
                        self.programmatic_message_count += 1

                    try:
                        await message.reply_text(response)
                        logging.info(f"✅ AI responded to {user.first_name} in conversation mode")
                        self.emit_terminal(f'✅ AI replied to {user.first_name}')
                    finally:
                        async with self._programmatic_lock:
                            self.programmatic_message_count -= 1

                except Exception as e:
                    logging.error(f"AI response error: {e}")

                    async with self._programmatic_lock:
                        self.programmatic_message_count += 1

                    try:
                        await message.reply_text("❌ দুঃখিত, AI উত্তর দিতে পারেনি। `/gem` command ব্যবহার করে চেষ্টা করুন।")
                    finally:
                        async with self._programmatic_lock:
                            self.programmatic_message_count -= 1
                return

            logging.info(f"📨 New message from {user.first_name} - Waiting {self.reply_timeout}s for reply")
            self.emit_terminal(f'⏰ Waiting 120 sec for reply to {user.first_name}')

            self.pending_replies[chat_id] = {
                'message_id': msg_id,
                'timestamp': asyncio.get_event_loop().time()
            }

            asyncio.create_task(self._schedule_auto_reply(message, chat_id, msg_id))

        @self.client.on_message(filters.private & filters.text & filters.outgoing)
        async def handle_outgoing_message(client, message: Message):
            chat_id = message.chat.id

            if self.programmatic_message_count > 0:
                logging.info(f"🤖 Programmatic message sent - Idle timer NOT reset")
                return

            logging.info(f"👤 You replied manually to chat {chat_id}")
            self.emit_terminal(f'👤 Manual reply sent')

            if chat_id in self.pending_replies:
                logging.info(f"✅ Cancelling auto-reply (manual reply sent)")
                self.emit_terminal(f'✅ Auto-reply cancelled')
                del self.pending_replies[chat_id]

            if chat_id in self.conversation_mode:
                logging.info(f"🔴 Manual reply - Conversation mode deactivated")
                self.emit_terminal(f'🔴 Conversation mode OFF')
                del self.conversation_mode[chat_id]

    async def _schedule_auto_reply(self, message: Message, chat_id: int, msg_id: int):
        try:
            logging.info(f"⏰ Waiting {self.reply_timeout} seconds before auto-reply...")
            await asyncio.sleep(self.reply_timeout)

            if chat_id in self.pending_replies and self.pending_replies[chat_id]['message_id'] == msg_id:
                try:
                    logging.info(f"📤 Sending auto-reply to {message.from_user.first_name}...")

                    async with self._programmatic_lock:
                        self.programmatic_message_count += 1

                    try:
                        await self.client.send_message(chat_id, self.auto_reply_message)

                        if self.api_key:
                            self.conversation_mode[chat_id] = True
                            logging.info(f'✅ Auto-reply sent + Conversation mode ACTIVATED for {message.from_user.first_name}')
                            self.emit_terminal(f'🤖 Auto-replied + 💬 Conversation mode ON for {message.from_user.first_name}')
                        else:
                            logging.info(f'✅ Auto-reply sent (AI disabled - no GEMINI_API_KEY) for {message.from_user.first_name}')
                            self.emit_terminal(f'🤖 Auto-replied to {message.from_user.first_name} (AI disabled)')

                        self.away_message_used = True

                        del self.pending_replies[chat_id]
                    finally:
                        async with self._programmatic_lock:
                            self.programmatic_message_count -= 1

                except Exception as e:
                    logging.error(f"❌ Failed to send auto-reply: {e}", exc_info=True)
                    self.emit_terminal(f'❌ Auto-reply failed: {str(e)}')

        except Exception as e:
            logging.error(f"Error in auto-reply scheduling: {e}", exc_info=True)

    async def _call_gemini_api(self, query: str, chat_id: int) -> str:
        import requests

        if not self.api_url:
            logging.error("❌ Cannot call Gemini API: GEMINI_API_KEY not configured")
            raise Exception("Gemini API key not configured. Please set GEMINI_API_KEY environment variable.")

        if chat_id not in self.conversation_history:
            self.conversation_history[chat_id] = deque(maxlen=self.max_history_length)

            system_prompt = {
                "role": "user",
                "parts": [{
                    "text": "You are a helpful AI assistant of Mahit Labib. Language guidelines:\n"
                            "- If the user writes in Bengali (বাংলা) or uses English letters to write Bengali (Banglish/Roman Bengali), respond in Bengali (বাংলা script)\n"
                            "- If the user writes in English, respond in English\n"
                            "- If the user writes in any other language, respond in English\n"
                            "- Be natural, friendly, and helpful in your responses"
                }]
            }
            model_ack = {
                "role": "model",
                "parts": [{"text": "আমি বুঝেছি! আমি বাংলা বা ইংরেজিতে সাহায্য করতে পারি। কিভাবে সাহায্য করতে পারি?"}]
            }
            self.conversation_history[chat_id].append(system_prompt)
            self.conversation_history[chat_id].append(model_ack)

        user_message = {
            "role": "user",
            "parts": [{"text": query}]
        }
        self.conversation_history[chat_id].append(user_message)

        payload = {
            "contents": list(self.conversation_history[chat_id]),
            "generationConfig": {
                "temperature": 0.7,
                "topK": 40,
                "topP": 0.95,
                "maxOutputTokens": 2048,
            }
        }

        headers = {
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=30
            )

            response.raise_for_status()
            data = response.json()

            if 'candidates' in data and len(data['candidates']) > 0:
                candidate = data['candidates'][0]
                if 'content' in candidate and 'parts' in candidate['content']:
                    parts = candidate['content']['parts']
                    if len(parts) > 0 and 'text' in parts[0]:
                        ai_response = parts[0]['text']

                        model_message = {
                            "role": "model",
                            "parts": [{"text": ai_response}]
                        }
                        self.conversation_history[chat_id].append(model_message)

                        return ai_response

            return "❌ দুঃখিত, AI থেকে সঠিক উত্তর পাওয়া যায়নি।"

        except Exception as e:
            logging.error(f"Gemini API error: {e}", exc_info=True)
            raise

    def cleanup(self):
        self.pending_replies.clear()
        self.conversation_mode.clear()
        self.conversation_history.clear()
        logging.info("Smart Auto-Reply module cleaned up")
