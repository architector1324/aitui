import curses
import api
import textwrap
import threading
import queue
import md_renderer


class Keys:
    """Constants for keyboard input handling."""
    # Special keys
    ESCAPE = '\x1b'
    TAB = '\t'
    ENTER = frozenset({'\n', '\r', 10, 13})
    BACKSPACE = frozenset({curses.KEY_BACKSPACE, '\x7f', '\x08', 127, 8})
    DELETE = curses.KEY_DC
    
    # Control keys
    CTRL_B = '\x02'  # Toggle sidebar
    CTRL_H = '\x08'  # Help (also Backspace)
    CTRL_G = '\x07'  # Switch model
    CTRL_N = '\x0e'  # New chat
    CTRL_D = '\x04'  # Delete chat
    
    # Navigation
    UP = curses.KEY_UP
    DOWN = curses.KEY_DOWN
    LEFT = curses.KEY_LEFT
    RIGHT = curses.KEY_RIGHT
    HOME = curses.KEY_HOME
    END = curses.KEY_END
    PAGE_UP = curses.KEY_PPAGE
    PAGE_DOWN = curses.KEY_NPAGE
    RESIZE = curses.KEY_RESIZE
    
    # Shift combinations (terminal-dependent codes)
    SHIFT_UP = frozenset({339, 566})
    SHIFT_DOWN = frozenset({338, 525})


HELP_COMMANDS = (
    ("Tab", "Switch Focus"),
    ("Enter", "Send / Select"),
    ("Ctrl+B", "Toggle Sidebar"),
    ("Ctrl+G", "Switch Model"),
    ("Ctrl+N", "New Chat"),
    ("Ctrl+D", "Delete Chat"),
    ("Ctrl+H", "Show Help"),
    ("Esc", "Exit")
)


class ChatTUI:
    def __init__(self, stdscr, provider, model, config, db):
        self.stdscr = stdscr
        self.provider = provider
        self.model = model
        self.config = config
        self.db = db
        
        self.sidebar_width = 25
        self.show_sidebar = False
        self.current_chat_id = None
        self.chats = []
        self.messages = []
        self.focus = "input"  # "input" or "sidebar"
        self.sidebar_idx = 0
        self.input_text = ""
        self.cursor_pos = 0
        self.scroll_offset = 0
        self.auto_scroll = True
        
        self.is_thinking = False
        self.stop_event = threading.Event()
        self.stream_queue = queue.Queue()
        
        self.all_models = []
        threading.Thread(target=self._preload_models, daemon=True).start()
        
        # Initialize stream handlers with bound methods
        self._stream_handlers = {
            "content": self._handle_content_chunk,
            "reasoning": self._handle_reasoning_chunk,
            "done": self._handle_stream_done,
            "title_updated": self._handle_title_updated,
            "cancelled": self._handle_stream_cancelled,
            "error": self._handle_stream_error,
        }
        
        curses.curs_set(1)
        self.stdscr.keypad(True)
        self.stdscr.nodelay(True)  # Non-blocking input
        self._init_colors()
        self._setup_windows()
        self._refresh_chats()
        self._draw_all()

    # ==================== Initialization ====================

    def _preload_models(self):
        """Background task to preload available models from all providers."""
        try:
            self.all_models = []
            for p in ["openrouter", "ollama", "openai"]:
                p_config = self.config.get(p)
                if p_config:
                    try:
                        models = api.get_models(p, p_config)
                        self.all_models.extend(models)
                    except Exception:
                        continue
        except Exception:
            pass

    def _init_colors(self):
        """Initialize curses color pairs."""
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)      # User messages
        curses.init_pair(2, curses.COLOR_GREEN, -1)     # Assistant messages
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Selection
        curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLACK)  # Code blocks
        curses.init_pair(5, curses.COLOR_YELLOW, -1)    # Headers
        curses.init_pair(6, curses.COLOR_BLUE, -1)      # Tables/Borders
        curses.init_pair(7, 244, -1)                    # Grey/Dim text

    def _setup_windows(self):
        """Create or recreate all curses windows."""
        h, w = self.stdscr.getmaxyx()
        sidebar_w = self.sidebar_width if self.show_sidebar else 0
        
        # Clear existing windows to avoid memory leaks
        for win_attr in ['sidebar_win', 'chat_win', 'input_rect', 'input_win']:
            win = getattr(self, win_attr, None)
            if win:
                try:
                    win.erase()
                    setattr(self, win_attr, None)
                except curses.error:
                    pass

        if self.show_sidebar:
            self.sidebar_win = curses.newwin(h - 1, sidebar_w, 0, 0)
        else:
            self.sidebar_win = None
            
        chat_w = w - sidebar_w
        self.chat_win = curses.newwin(h - 4, chat_w, 0, sidebar_w)
        self.chat_win.scrollok(True)
        self.input_rect = curses.newwin(3, chat_w, h - 4, sidebar_w)
        self.input_win = curses.newwin(1, chat_w - 2, h - 3, sidebar_w + 1)
        self.input_win.keypad(True)
        self.input_win.nodelay(True)

    # ==================== Data Management ====================

    def _refresh_chats(self):
        """Reload chats from database and update current selection."""
        self.chats = self.db.get_chats()
        if not self.chats:
            self.current_chat_id = self.db.create_chat(self.provider, self.model)
            self.chats = self.db.get_chats()
            self.sidebar_idx = 0
            self.messages = []
            return

        if not self.current_chat_id:
            self._select_chat(0)
        else:
            # Find the current chat in the updated list
            found = False
            for i, chat in enumerate(self.chats):
                if chat['id'] == self.current_chat_id:
                    self._select_chat(i)
                    found = True
                    break
            if not found:
                self._select_chat(0)
        
        self._load_messages()

    def _select_chat(self, index):
        """Select a chat by index and update model/provider."""
        if 0 <= index < len(self.chats):
            self.sidebar_idx = index
            self.current_chat_id = self.chats[index]['id']
            self.model = self.chats[index]['model']
            self.provider = self.chats[index]['provider']

    def _load_messages(self):
        """Load messages for the current chat from database."""
        self.messages = []
        for m in self.db.get_messages(self.current_chat_id):
            msg_dict = dict(m)
            if msg_dict.get('reasoning') is None:
                msg_dict['reasoning'] = ''
            self.messages.append(msg_dict)

    # ==================== Drawing ====================

    def _draw_sidebar(self):
        """Render the sidebar with chat list."""
        if not self.show_sidebar or not self.sidebar_win:
            return
        
        self.sidebar_win.erase()
        self.sidebar_win.box()
        
        title_attr = curses.A_BOLD | (curses.color_pair(3) if self.focus == "sidebar" else curses.A_NORMAL)
        self.sidebar_win.addstr(1, 2, "CHATS", title_attr)
        self.sidebar_win.hline(2, 1, curses.ACS_HLINE, self.sidebar_width - 2)
        
        for i, chat in enumerate(self.chats):
            if 3 + i >= self.sidebar_win.getmaxyx()[0] - 1:
                break
            
            is_current = chat['id'] == self.current_chat_id
            is_focused = (self.focus == "sidebar" and i == self.sidebar_idx)
            attr = curses.color_pair(3) if is_focused else curses.A_NORMAL
            prefix = "• " if is_current else "  "
            
            title = chat['title']
            if len(title) > self.sidebar_width - 6:
                title = title[:self.sidebar_width - 9] + "..."
            
            self.sidebar_win.addstr(3 + i, 1, f"{prefix}{title}".ljust(self.sidebar_width - 2), attr)
        
        self.sidebar_win.noutrefresh()

    def _draw_messages(self):
        """Render the message area with all messages."""
        self.chat_win.erase()
        self.chat_win.box()
        h, w = self.chat_win.getmaxyx()
        
        # Roomy Split: 50% width centered area
        margin = int(w * 0.25)
        max_msg_w = int(w * 0.50)
        all_lines = []
        
        for msg in self.messages:
            self._render_message(msg, margin, max_msg_w, all_lines)
        
        # Handle scrolling
        max_visible = h - 2
        max_scroll = max(0, len(all_lines) - max_visible)
        
        if self.auto_scroll:
            self.scroll_offset = max_scroll
        else:
            self.scroll_offset = min(self.scroll_offset, max_scroll)
        
        visible_lines = all_lines[self.scroll_offset:self.scroll_offset + max_visible]
        
        for i, (segments, x) in enumerate(visible_lines):
            y = i + 1
            if y >= h - 1:
                break
            
            curr_x = x
            for text, attr in segments:
                if not text:
                    continue
                try:
                    self.chat_win.addstr(y, curr_x, text, attr)
                    curr_x += len(text)
                except curses.error:
                    pass
        
        if self.is_thinking:
            self.chat_win.addstr(h - 1, 2, " Thinking... ", curses.A_REVERSE)
        
        self.chat_win.noutrefresh()

    def _render_message(self, msg, margin, max_msg_w, all_lines):
        """Render a single message into the all_lines list."""
        is_user = msg['role'] == 'user'
        color = curses.color_pair(1) if is_user else curses.color_pair(2)
        
        # Header
        if is_user:
            header = "User"
            header_x = margin + max_msg_w - len(header)
        else:
            msg_model = msg.get('model') or self.model
            header = f"{msg_model}"
            header_x = margin
        
        all_lines.append(([(header, color | curses.A_BOLD)], header_x))
        
        # Reasoning (Thinking)
        reasoning = msg.get('reasoning', '')
        if reasoning:
            is_last = (msg == self.messages[-1])
            if is_last and self.is_thinking:
                md_lines = md_renderer.render_markdown(reasoning, max_msg_w, curses.color_pair(7))
                for line_segments in md_lines:
                    all_lines.append((line_segments, margin))
            else:
                all_lines.append(([("think", curses.color_pair(7) | curses.A_ITALIC)], margin))
        
        # Content with Markdown support
        content = msg['content']
        if content:
            md_lines = md_renderer.render_markdown(content, max_msg_w, curses.A_NORMAL)
            for line_segments in md_lines:
                line_w = sum(len(text) for text, attr in line_segments)
                x = margin + max_msg_w - line_w if is_user else margin
                all_lines.append((line_segments, x))
        
        all_lines.append(([("", 0)], 0))  # Spacer

    def _draw_input(self):
        """Render the input area."""
        input_attr = curses.color_pair(3) if self.focus == "input" else curses.A_NORMAL
        
        self.input_rect.erase()
        self.input_rect.box()
        if self.focus == "input":
            self.input_rect.addstr(0, 2, " prompt ", input_attr)
        self.input_rect.noutrefresh()
        
        self.input_win.erase()
        win_w = self.input_win.getmaxyx()[1]
        
        # Handle input text longer than window
        display_text = self.input_text
        if len(display_text) >= win_w:
            display_text = display_text[-(win_w - 1):]
        
        self.input_win.addstr(0, 0, display_text)
        
        if self.focus == "input":
            curses.curs_set(1)
            self.input_win.move(0, min(self.cursor_pos, win_w - 1))
        else:
            curses.curs_set(0)
        
        self.input_win.noutrefresh()

    def _draw_all(self):
        """Redraw the entire screen."""
        h, w = self.stdscr.getmaxyx()
        self.stdscr.erase()
        self.stdscr.addstr(h - 1, 1, f"Model: {self.model} | Ctrl+H: Help | Esc: Exit", curses.A_DIM)
        self.stdscr.noutrefresh()
        
        self._draw_sidebar()
        self._draw_messages()
        self._draw_input()
        curses.doupdate()

    # ==================== Dialogs ====================

    def _show_help(self):
        """Display help dialog with keyboard shortcuts."""
        h, w = self.stdscr.getmaxyx()
        
        win_h, win_w = len(HELP_COMMANDS) + 7, 34
        win_y, win_x = (h - win_h) // 2, (w - win_w) // 2
        win = curses.newwin(win_h, win_w, win_y, win_x)
        win.box()
        curses.curs_set(0)
        
        win.addstr(1, (win_w - 8) // 2, "COMMANDS", curses.A_BOLD)
        
        max_key_len = max(len(k) for k, _ in HELP_COMMANDS)
        formatted_lines = [f"{key.rjust(max_key_len)} : {desc}" for key, desc in HELP_COMMANDS]
        max_line_len = max(len(line) for line in formatted_lines)
        start_x = (win_w - max_line_len) // 2
        
        for i, line in enumerate(formatted_lines):
            win.addstr(i + 3, start_x, line)
        
        win.addstr(win_h - 2, (win_w - 22) // 2, "Press any key to close")
        win.refresh()
        
        self.stdscr.nodelay(False)
        win.getch()
        self.stdscr.nodelay(True)
        curses.curs_set(1)

    def _show_confirm(self, message):
        """Show a confirmation dialog. Returns True if user confirms."""
        h, w = self.stdscr.getmaxyx()
        win_h, win_w = 7, max(len(message) + 10, 30)
        win_y, win_x = (h - win_h) // 2, (w - win_w) // 2
        win = curses.newwin(win_h, win_w, win_y, win_x)
        win.keypad(True)
        selected = 0
        curses.curs_set(0)
        self.stdscr.nodelay(False)
        
        while True:
            win.erase()
            win.box()
            win.addstr(2, (win_w - len(message)) // 2, message)
            yes_attr = curses.color_pair(3) if selected == 0 else curses.A_NORMAL
            no_attr = curses.color_pair(3) if selected == 1 else curses.A_NORMAL
            win.addstr(4, (win_w // 2) - 8, "  YES  ", yes_attr)
            win.addstr(4, (win_w // 2) + 2, "  NO   ", no_attr)
            win.refresh()
            
            ch = win.getch()
            if ch in [9, curses.KEY_RIGHT, curses.KEY_LEFT]:
                selected = 1 - selected
            elif ch in [10, 13]:
                self.stdscr.nodelay(True)
                curses.curs_set(1)
                return selected == 0
            elif ch == 27:
                self.stdscr.nodelay(True)
                curses.curs_set(1)
                return False

    def _show_model_selector(self):
        """Display model selection dialog."""
        h, w = self.stdscr.getmaxyx()
        win_h, win_w = 20, 60
        win_y, win_x = (h - win_h) // 2, (w - win_w) // 2
        win = curses.newwin(win_h, win_w, win_y, win_x)
        win.keypad(True)
        win.box()
        
        search_text = ""
        selected_idx = 0
        scroll_offset = 0
        
        if not self.all_models:
            win.addstr(win_h // 2, (win_w - 10) // 2, "Loading...", curses.A_BOLD)
            win.refresh()
            try:
                self.all_models = api.get_models(self.provider, self.config)
            except Exception as e:
                self._show_confirm(f"Error fetching models: {str(e)}")
                return
        
        self.stdscr.nodelay(False)
        curses.curs_set(1)
        
        while True:
            win.erase()
            win.box()
            win.addstr(1, 2, " SELECT MODEL ", curses.A_BOLD)
            win.addstr(2, 2, f"Search: {search_text}")
            win.hline(3, 1, curses.ACS_HLINE, win_w - 2)
            
            filtered_models = [m for m in self.all_models if search_text.lower() in m.lower()]
            
            list_h = win_h - 5
            if selected_idx >= len(filtered_models):
                selected_idx = max(0, len(filtered_models) - 1)
            
            if selected_idx < scroll_offset:
                scroll_offset = selected_idx
            elif selected_idx >= scroll_offset + list_h:
                scroll_offset = selected_idx - list_h + 1
            
            for i in range(list_h):
                idx = i + scroll_offset
                if idx >= len(filtered_models):
                    break
                
                model_name = filtered_models[idx]
                attr = curses.color_pair(3) if idx == selected_idx else curses.A_NORMAL
                
                display_name = model_name
                if len(display_name) > win_w - 4:
                    display_name = display_name[:win_w - 7] + "..."
                
                win.addstr(4 + i, 2, display_name.ljust(win_w - 4), attr)
            
            win.move(2, 10 + len(search_text))
            win.refresh()
            
            try:
                ch = win.get_wch()
            except curses.error:
                continue
            
            if ch == Keys.ESCAPE:
                break
            elif ch in Keys.ENTER:
                if filtered_models:
                    selected_model = filtered_models[selected_idx]
                    if ":" in selected_model:
                        new_provider, new_model = selected_model.split(":", 1)
                        self.provider = new_provider
                        self.model = new_model
                    else:
                        self.model = selected_model
                    
                    if self.current_chat_id:
                        self.db.update_chat_model(self.current_chat_id, self.model, self.provider)
                        self._refresh_chats()
                    break
            elif ch == curses.KEY_UP:
                selected_idx = max(0, selected_idx - 1)
            elif ch == curses.KEY_DOWN:
                selected_idx = min(len(filtered_models) - 1, selected_idx + 1)
            elif ch in Keys.BACKSPACE:
                search_text = search_text[:-1]
                selected_idx = 0
            elif isinstance(ch, str) and ch.isprintable():
                search_text += ch
                selected_idx = 0
        
        self.stdscr.nodelay(True)
        curses.curs_set(1)
        self._draw_all()

    # ==================== API Workers ====================

    def _api_worker(self, messages):
        """Background worker to call the LLM API."""
        try:
            p_config = self.config.get(self.provider, {})
            p_config['model'] = self.model
            for chunk_type, chunk_content in api.call_llm(self.provider, p_config, messages):
                if self.stop_event.is_set():
                    self.stream_queue.put(("cancelled", None))
                    return
                self.stream_queue.put((chunk_type, chunk_content))
            self.stream_queue.put(("done", None))
        except Exception as e:
            self.stream_queue.put(("error", str(e)))

    def _title_worker(self, chat_id, first_message):
        """Background worker to generate a chat title."""
        try:
            prompt = f"Create a very short title (max 5 words) for a conversation that starts with: '{first_message}'. Respond ONLY with the title text, no quotes or punctuation."
            messages = [{"role": "user", "content": prompt}]
            title = ""
            p_config = self.config.get(self.provider, {})
            p_config['model'] = self.model
            for chunk_type, chunk_content in api.call_llm(self.provider, p_config, messages):
                if chunk_type == "content":
                    title += chunk_content
            title = title.strip().strip('"').strip("'").strip()
            if title:
                self.db.update_chat_title(chat_id, title)
                self.stream_queue.put(("title_updated", None))
        except Exception:
            pass

    # ==================== Stream Queue Processing ====================

    def _process_stream_queue(self):
        """Process all pending messages from the API worker queue."""
        try:
            while True:
                msg_type, data = self.stream_queue.get_nowait()
                handler = self._stream_handlers.get(msg_type)
                if handler:
                    handler(data)
        except queue.Empty:
            pass

    def _handle_content_chunk(self, data):
        """Handle incoming content chunk from stream."""
        self._ensure_assistant_message()
        self.messages[-1]['content'] += data

    def _handle_reasoning_chunk(self, data):
        """Handle incoming reasoning chunk from stream."""
        self._ensure_assistant_message()
        self.messages[-1]['reasoning'] += data

    def _handle_stream_done(self, data):
        """Handle stream completion."""
        self.is_thinking = False
        last_msg = self.messages[-1]
        self.db.add_message(
            self.current_chat_id, 'assistant', last_msg['content'],
            self.provider, self.model, reasoning=last_msg.get('reasoning')
        )
        if len(self.messages) == 2:  # First exchange
            threading.Thread(
                target=self._title_worker,
                args=(self.current_chat_id, self.messages[0]['content']),
                daemon=True
            ).start()

    def _handle_title_updated(self, data):
        """Handle title update notification."""
        self._refresh_chats()

    def _handle_stream_cancelled(self, data):
        """Handle stream cancellation."""
        self.is_thinking = False
        if self.messages and self.messages[-1]['role'] == 'assistant':
            self.messages[-1]['content'] += " [Cancelled]"
            last_msg = self.messages[-1]
            self.db.add_message(
                self.current_chat_id, 'assistant', last_msg['content'],
                self.provider, self.model, reasoning=last_msg.get('reasoning')
            )

    def _handle_stream_error(self, data):
        """Handle stream error."""
        self.is_thinking = False
        error_msg = f"Error: {data}"
        self.messages.append({
            'role': 'assistant', 'content': error_msg,
            'reasoning': '', 'provider': self.provider, 'model': self.model
        })
        self.db.add_message(self.current_chat_id, 'assistant', error_msg, self.provider, self.model)

    def _ensure_assistant_message(self):
        """Ensure there's an assistant message to append content to."""
        if not self.messages or self.messages[-1]['role'] != 'assistant':
            self.messages.append({
                'role': 'assistant', 'content': '', 'reasoning': '',
                'provider': self.provider, 'model': self.model
            })

    # ==================== Input Editing ====================

    def _insert_char(self, char):
        """Insert a character at cursor position."""
        self.input_text = self.input_text[:self.cursor_pos] + char + self.input_text[self.cursor_pos:]
        self.cursor_pos += 1

    def _delete_backward(self):
        """Delete character before cursor."""
        if self.cursor_pos > 0:
            self.input_text = self.input_text[:self.cursor_pos - 1] + self.input_text[self.cursor_pos:]
            self.cursor_pos -= 1

    def _delete_forward(self):
        """Delete character at cursor position."""
        if self.cursor_pos < len(self.input_text):
            self.input_text = self.input_text[:self.cursor_pos] + self.input_text[self.cursor_pos + 1:]

    def _move_cursor(self, delta):
        """Move cursor by delta positions."""
        self.cursor_pos = max(0, min(len(self.input_text), self.cursor_pos + delta))

    def _send_message(self):
        """Send the current input message to the LLM."""
        if not self.input_text.strip() or self.is_thinking:
            return
        
        user_text = self.input_text.strip()
        self.db.add_message(self.current_chat_id, 'user', user_text, self.provider, self.model)
        self.messages.append({
            'role': 'user', 'content': user_text,
            'provider': self.provider, 'model': self.model
        })
        self.input_text = ""
        self.cursor_pos = 0
        self.is_thinking = True
        self.stop_event.clear()
        
        threading.Thread(target=self._api_worker, args=(self.messages,), daemon=True).start()

    # ==================== Keyboard Handling ====================

    def _handle_global_keys(self, ch):
        """Handle keys that work regardless of focus. Returns True if handled."""
        if ch == Keys.ESCAPE:
            return self._handle_exit()
        
        if ch == Keys.TAB:
            self._handle_toggle_focus()
            return True
        
        if ch == Keys.CTRL_B:
            self._handle_toggle_sidebar()
            return True
        
        if ch == Keys.CTRL_H:
            self._show_help()
            return True
        
        if ch == Keys.CTRL_G:
            self._show_model_selector()
            return True
        
        if ch == Keys.CTRL_N:
            self._handle_new_chat()
            return True
        
        if ch == Keys.CTRL_D:
            self._handle_delete_chat()
            return True
        
        if ch == Keys.PAGE_UP:
            self._handle_page_scroll(-1)
            return True
        
        if ch == Keys.PAGE_DOWN:
            self._handle_page_scroll(1)
            return True
        
        if ch == Keys.RESIZE:
            self._handle_resize()
            return True
        
        if ch in Keys.SHIFT_UP:
            self._handle_line_scroll(-1)
            return True
        
        if ch in Keys.SHIFT_DOWN:
            self._handle_line_scroll(1)
            return True
        
        # Arrow keys in input mode also scroll
        if self.focus == "input":
            if ch == Keys.UP and self.scroll_offset > 0:
                self._handle_line_scroll(-1)
                return True
            if ch == Keys.DOWN:
                self._handle_line_scroll(1)
                return True
        
        return False

    def _handle_input_keys(self, ch):
        """Handle key input when focus is on input field."""
        if ch in Keys.ENTER:
            self._send_message()
        elif ch in Keys.BACKSPACE:
            self._delete_backward()
        elif ch == Keys.DELETE:
            self._delete_forward()
        elif ch == Keys.LEFT:
            self._move_cursor(-1)
        elif ch == Keys.RIGHT:
            self._move_cursor(1)
        elif ch == Keys.HOME:
            self.cursor_pos = 0
        elif ch == Keys.END:
            self.cursor_pos = len(self.input_text)
        elif isinstance(ch, str) and ch.isprintable():
            self._insert_char(ch)

    def _handle_sidebar_keys(self, ch):
        """Handle key input when focus is on sidebar."""
        if ch == Keys.UP:
            self.sidebar_idx = max(0, self.sidebar_idx - 1)
        elif ch == Keys.DOWN:
            self.sidebar_idx = min(len(self.chats) - 1, self.sidebar_idx + 1)
        elif ch in Keys.ENTER and self.chats:
            self.current_chat_id = self.chats[self.sidebar_idx]['id']
            self._refresh_chats()
            self.focus = "input"

    # ==================== Global Key Actions ====================

    def _handle_exit(self):
        """Handle exit request. Returns True to exit the main loop."""
        if self._show_confirm("Exit application?"):
            return True
        return False

    def _handle_toggle_focus(self):
        """Toggle focus between input and sidebar."""
        if self.show_sidebar:
            self.focus = "sidebar" if self.focus == "input" else "input"
        else:
            self.focus = "input"

    def _handle_toggle_sidebar(self):
        """Toggle sidebar visibility."""
        self.show_sidebar = not self.show_sidebar
        if not self.show_sidebar:
            self.focus = "input"
        self._setup_windows()

    def _handle_new_chat(self):
        """Create a new chat."""
        self.current_chat_id = self.db.create_chat(self.provider, self.model)
        self.input_text = ""
        self.cursor_pos = 0
        self._refresh_chats()

    def _handle_delete_chat(self):
        """Delete the current or selected chat."""
        if not self.chats:
            return
        target_id = self.chats[self.sidebar_idx]['id'] if self.focus == "sidebar" else self.current_chat_id
        if target_id and self._show_confirm("Delete this chat?"):
            self.db.delete_chat(target_id)
            self.current_chat_id = None
            self._refresh_chats()

    def _handle_page_scroll(self, direction):
        """Scroll by half a page."""
        self.auto_scroll = False
        half_page = self.chat_win.getmaxyx()[0] // 2
        if direction < 0:
            self.scroll_offset = max(0, self.scroll_offset - half_page)
        else:
            self.scroll_offset += half_page

    def _handle_line_scroll(self, direction):
        """Scroll by one line."""
        self.auto_scroll = False
        if direction < 0:
            self.scroll_offset = max(0, self.scroll_offset - 1)
        else:
            self.scroll_offset += 1

    def _handle_resize(self):
        """Handle terminal resize event."""
        curses.update_lines_cols()
        self._setup_windows()
        self._draw_all()

    def _handle_keyboard_interrupt(self):
        """Handle Ctrl+C interrupt."""
        if self.is_thinking:
            self.stop_event.set()
        elif self.input_text:
            self.input_text = ""
            self.cursor_pos = 0
        else:
            if self._show_confirm("Exit application?"):
                return True
        return False

    # ==================== Main Loop ====================

    def run(self):
        """Main application loop."""
        while True:
            try:
                # Process any pending stream data
                self._process_stream_queue()
                
                # Redraw UI
                self._draw_all()
                
                # Get input
                try:
                    ch = self.input_win.get_wch()
                except curses.error:
                    curses.napms(10)
                    continue
                
                # Try global handlers first
                if self._handle_global_keys(ch):
                    if ch == Keys.ESCAPE:
                        break  # Exit requested
                    continue
                
                # Route to focus-specific handler
                if self.focus == "input":
                    self._handle_input_keys(ch)
                elif self.focus == "sidebar":
                    self._handle_sidebar_keys(ch)
                    
            except KeyboardInterrupt:
                if self._handle_keyboard_interrupt():
                    break
