import curses
import api
import textwrap
import threading
import queue

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
        self.focus = "input" # "input" or "sidebar"
        self.sidebar_idx = 0
        self.input_text = ""
        self.cursor_pos = 0
        self.scroll_offset = 0
        self.auto_scroll = True
        
        self.is_thinking = False
        self.stop_event = threading.Event()
        self.stream_queue = queue.Queue()
        
        self.all_models = []
        threading.Thread(target=self.preload_models, daemon=True).start()
        
        curses.curs_set(1)
        self.stdscr.keypad(True)
        self.stdscr.nodelay(True) # Non-blocking input
        self.init_colors()
        self.setup_windows()
        self.refresh_chats()
        self.draw_all()

    def preload_models(self):
        try:
            self.all_models = api.get_models(self.provider, self.config)
        except Exception:
            pass

    def init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_WHITE)

    def setup_windows(self):
        h, w = self.stdscr.getmaxyx()
        sidebar_w = self.sidebar_width if self.show_sidebar else 0
        
        # Clear existing windows if they exist to avoid memory leaks/artifacts
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

    def refresh_chats(self):
        self.chats = self.db.get_chats()
        if not self.chats:
            self.current_chat_id = self.db.create_chat(self.provider, self.model)
            self.chats = self.db.get_chats()
            self.sidebar_idx = 0
            self.messages = []
            return

        if not self.current_chat_id:
            self.current_chat_id = self.chats[0]['id']
            self.sidebar_idx = 0
            self.model = self.chats[0]['model']
            self.config['model'] = self.model
        else:
            # Find the index of the current chat in the updated list
            found = False
            for i, chat in enumerate(self.chats):
                if chat['id'] == self.current_chat_id:
                    self.sidebar_idx = i
                    self.model = chat['model'] # Update current model from DB
                    self.config['model'] = self.model # Update config for API calls
                    found = True
                    break
            if not found:
                self.current_chat_id = self.chats[0]['id']
                self.sidebar_idx = 0
                self.model = self.chats[0]['model']
                self.config['model'] = self.model
        
        self.messages = [dict(m) for m in self.db.get_messages(self.current_chat_id)]

    def draw_sidebar(self):
        if not self.show_sidebar or not self.sidebar_win:
            return
        self.sidebar_win.erase()
        self.sidebar_win.box()
        title_attr = curses.A_BOLD | (curses.color_pair(3) if self.focus == "sidebar" else curses.A_NORMAL)
        self.sidebar_win.addstr(1, 2, "CHATS", title_attr)
        self.sidebar_win.hline(2, 1, curses.ACS_HLINE, self.sidebar_width - 2)
        
        for i, chat in enumerate(self.chats):
            if 3 + i >= self.sidebar_win.getmaxyx()[0] - 1: break
            is_current = chat['id'] == self.current_chat_id
            is_focused = (self.focus == "sidebar" and i == self.sidebar_idx)
            attr = curses.A_NORMAL
            if is_focused: attr = curses.color_pair(3)
            prefix = "* " if is_current else "  "
            title = chat['title']
            if len(title) > self.sidebar_width - 6:
                title = title[:self.sidebar_width - 9] + "..."
            self.sidebar_win.addstr(3 + i, 1, f"{prefix}{title}".ljust(self.sidebar_width - 2), attr)
        self.sidebar_win.noutrefresh()

    def draw_messages(self):
        self.chat_win.erase()
        self.chat_win.box()
        h, w = self.chat_win.getmaxyx()
        
        # Roomy Split: 50% width centered area
        # margin = (100% - 50%) / 2 = 25%
        margin = int(w * 0.25)
        max_msg_w = int(w * 0.50)
        all_lines = [] # List of (text, x_pos, color, is_bold)
        
        for msg in self.messages:
            is_user = msg['role'] == 'user'
            color = curses.color_pair(1) if is_user else curses.color_pair(2)
            
            # Header
            if is_user:
                header = "User"
                header_x = margin + max_msg_w - len(header)
            else:
                header = f"{self.model}"
                header_x = margin
            
            all_lines.append((header, header_x, color, True))
            
            # Content with line break support
            content = msg['content']
            lines = content.split('\n')
            wrapped_lines = []
            for line in lines:
                if not line.strip():
                    wrapped_lines.append("")
                else:
                    wrapped_lines.extend(textwrap.wrap(line, width=max_msg_w))
            
            for line in wrapped_lines:
                if is_user:
                    x = margin + max_msg_w - len(line)
                else:
                    x = margin
                all_lines.append((line, x, curses.A_NORMAL, False))
            
            all_lines.append(("", 0, 0, False)) # Spacer

        # Handle scrolling
        max_visible = h - 2
        max_scroll = max(0, len(all_lines) - max_visible)
        
        if self.auto_scroll:
            self.scroll_offset = max_scroll
        else:
            self.scroll_offset = min(self.scroll_offset, max_scroll)
        
        visible_lines = all_lines[self.scroll_offset : self.scroll_offset + max_visible]
        
        for i, (line, x, attr, is_bold) in enumerate(visible_lines):
            y = i + 1
            if y >= h - 1: break
            final_attr = attr
            if is_bold: final_attr |= curses.A_BOLD
            try:
                self.chat_win.addstr(y, x, line, final_attr)
            except curses.error:
                pass

            
        if self.is_thinking:
            self.chat_win.addstr(h-1, 2, " Thinking... ", curses.A_REVERSE)
            
        self.chat_win.noutrefresh()

    def draw_input(self):
        input_attr = curses.color_pair(3) if self.focus == "input" else curses.A_NORMAL
        self.input_rect.erase()
        self.input_rect.box()
        if self.focus == "input":
            self.input_rect.addstr(0, 2, " INPUT ", input_attr)
        self.input_rect.noutrefresh()
        
        self.input_win.erase()
        # Handle input text longer than window
        display_text = self.input_text
        win_w = self.input_win.getmaxyx()[1]
        if len(display_text) >= win_w:
            display_text = display_text[-(win_w-1):]
            
        self.input_win.addstr(0, 0, display_text)
        if self.focus == "input":
            curses.curs_set(1)
            self.input_win.move(0, min(self.cursor_pos, win_w - 1))
        else:
            curses.curs_set(0)
        self.input_win.noutrefresh()

    def draw_all(self):
        h, w = self.stdscr.getmaxyx()
        self.stdscr.erase()
        self.stdscr.addstr(h-1, 1, f"Model: {self.model} | Ctrl+H: Help | Esc: Exit", curses.A_DIM)
        self.stdscr.noutrefresh()
        
        self.draw_sidebar()
        self.draw_messages()
        self.draw_input()
        curses.doupdate()

    def show_help(self):
        h, w = self.stdscr.getmaxyx()
        commands = [
            ("Tab", "Switch Focus"),
            ("Enter", "Send / Select"),
            ("Ctrl+B", "Toggle Sidebar"),
            ("Ctrl+G", "Switch Model"),
            ("Ctrl+N", "New Chat"),
            ("Ctrl+D", "Delete Chat"),
            ("Ctrl+H", "Show Help"),
            ("Esc", "Exit")
        ]
        
        win_h, win_w = len(commands) + 7, 34
        win_y, win_x = (h - win_h) // 2, (w - win_w) // 2
        win = curses.newwin(win_h, win_w, win_y, win_x)
        win.box()
        curses.curs_set(0)
        
        win.addstr(1, (win_w - 8) // 2, "COMMANDS", curses.A_BOLD)
        
        max_key_len = max(len(k) for k, v in commands)
        formatted_lines = [f"{key.rjust(max_key_len)} : {desc}" for key, desc in commands]
        max_line_len = max(len(line) for line in formatted_lines)
        start_x = (win_w - max_line_len) // 2
        
        for i, line in enumerate(formatted_lines):
            win.addstr(i + 3, start_x, line)
            
        win.addstr(win_h - 2, (win_w - 22) // 2, "Press any key to close")
        win.refresh()
        # Blocking getch for help screen
        self.stdscr.nodelay(False)
        win.getch()
        self.stdscr.nodelay(True)
        curses.curs_set(1)

    def show_confirm(self, message):
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
            if ch in [9, curses.KEY_RIGHT, curses.KEY_LEFT]: selected = 1 - selected
            elif ch in [10, 13]:
                self.stdscr.nodelay(True)
                curses.curs_set(1)
                return selected == 0
            elif ch == 27:
                self.stdscr.nodelay(True)
                curses.curs_set(1)
                return False

    def show_model_selector(self):
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
                self.show_confirm(f"Error fetching models: {str(e)}")
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
                if idx >= len(filtered_models): break
                
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
                
            if ch == '\x1b': # Esc
                break
            elif ch in ['\n', '\r', 10, 13]: # Enter
                if filtered_models:
                    new_model = filtered_models[selected_idx]
                    self.model = new_model
                    self.config['model'] = new_model
                    if self.current_chat_id:
                        self.db.update_chat_model(self.current_chat_id, new_model)
                        self.refresh_chats()
                    break
            elif ch == curses.KEY_UP:
                selected_idx = max(0, selected_idx - 1)
            elif ch == curses.KEY_DOWN:
                selected_idx = min(len(filtered_models) - 1, selected_idx + 1)
            elif ch in [curses.KEY_BACKSPACE, '\x7f', '\x08', 127, 8]:
                search_text = search_text[:-1]
                selected_idx = 0
            elif isinstance(ch, str) and ch.isprintable():
                search_text += ch
                selected_idx = 0
                
        self.stdscr.nodelay(True)
        curses.curs_set(1)
        self.draw_all()

    def api_worker(self, messages):
        try:
            for chunk in api.call_llm(self.provider, self.config, messages):
                if self.stop_event.is_set():
                    self.stream_queue.put(("cancelled", None))
                    return
                self.stream_queue.put(("chunk", chunk))
            self.stream_queue.put(("done", None))
        except Exception as e:
            self.stream_queue.put(("error", str(e)))

    def title_worker(self, chat_id, first_message):
        try:
            prompt = f"Create a very short title (max 5 words) for a conversation that starts with: '{first_message}'. Respond ONLY with the title text, no quotes or punctuation."
            messages = [{"role": "user", "content": prompt}]
            title = ""
            for chunk in api.call_llm(self.provider, self.config, messages):
                title += chunk
            title = title.strip().strip('"').strip("'").strip()
            if title:
                self.db.update_chat_title(chat_id, title)
                self.stream_queue.put(("title_updated", None))
        except Exception:
            pass

    def run(self):
        while True:
            try:
                # Process streaming queue
                try:
                    while True:
                        msg_type, data = self.stream_queue.get_nowait()
                        if msg_type == "chunk":
                            if not self.messages or self.messages[-1]['role'] != 'assistant':
                                self.messages.append({'role': 'assistant', 'content': ''})
                            self.messages[-1]['content'] += data
                        elif msg_type == "done":
                            self.is_thinking = False
                            self.db.add_message(self.current_chat_id, 'assistant', self.messages[-1]['content'])
                            if len(self.messages) == 2: # First exchange
                                threading.Thread(target=self.title_worker, args=(self.current_chat_id, self.messages[0]['content']), daemon=True).start()
                        elif msg_type == "title_updated":
                            self.refresh_chats()
                        elif msg_type == "cancelled":
                            self.is_thinking = False
                            if self.messages and self.messages[-1]['role'] == 'assistant':
                                self.messages[-1]['content'] += " [Cancelled]"
                                self.db.add_message(self.current_chat_id, 'assistant', self.messages[-1]['content'])
                        elif msg_type == "error":
                            self.is_thinking = False
                            error_msg = f"Error: {data}"
                            self.messages.append({'role': 'assistant', 'content': error_msg})
                            self.db.add_message(self.current_chat_id, 'assistant', error_msg)
                except queue.Empty:
                    pass

                self.draw_all()
                
                try:
                    ch = self.input_win.get_wch()
                except curses.error:
                    # Check if we should re-enable auto-scroll if user scrolled to bottom
                    # This is handled in draw_messages logic mostly, but we can be explicit
                    curses.napms(10)
                    continue
                
                if ch == '\x1b': # Esc
                    if self.show_confirm("Exit application?"): break
                elif ch == '\t': # Tab
                    if self.show_sidebar:
                        self.focus = "sidebar" if self.focus == "input" else "input"
                    else:
                        self.focus = "input"
                elif ch == curses.KEY_PPAGE: # Page Up
                    self.auto_scroll = False
                    self.scroll_offset = max(0, self.scroll_offset - (self.chat_win.getmaxyx()[0] // 2))
                elif ch == curses.KEY_NPAGE: # Page Down
                    self.scroll_offset += (self.chat_win.getmaxyx()[0] // 2)
                elif ch == 339 or ch == 566: # Shift + Up
                    self.auto_scroll = False
                    self.scroll_offset = max(0, self.scroll_offset - 1)
                elif ch == 338 or ch == 525: # Shift + Down
                    self.scroll_offset += 1
                elif ch == curses.KEY_UP and self.focus == "input":
                    # Only scroll if we are not at the top
                    if self.scroll_offset > 0:
                        self.auto_scroll = False
                        self.scroll_offset -= 1
                elif ch == curses.KEY_DOWN and self.focus == "input":
                    self.scroll_offset += 1

                elif ch == curses.KEY_RESIZE:
                    curses.update_lines_cols()
                    self.setup_windows()
                    self.draw_all()
                elif ch == '\x02': # Ctrl+B
                    self.show_sidebar = not self.show_sidebar
                    if not self.show_sidebar: self.focus = "input"
                    self.setup_windows()
                elif ch == '\x08': # Ctrl+H
                    self.show_help()
                elif ch == '\x07': # Ctrl+G
                    self.show_model_selector()
                elif ch == '\x0e': # Ctrl+N
                    self.current_chat_id = self.db.create_chat(self.provider, self.model)
                    self.input_text = ""
                    self.cursor_pos = 0
                    self.refresh_chats()
                elif ch == '\x04': # Ctrl+D
                    target_id = self.chats[self.sidebar_idx]['id'] if self.focus == "sidebar" else self.current_chat_id
                    if target_id and self.show_confirm("Delete this chat?"):
                        self.db.delete_chat(target_id)
                        self.current_chat_id = None
                        self.refresh_chats()
                
                elif self.focus == "input":
                    if ch in ['\n', '\r', 10, 13]: # Enter
                        if self.input_text.strip() and not self.is_thinking:
                            user_text = self.input_text.strip()
                            self.db.add_message(self.current_chat_id, 'user', user_text)
                            self.messages.append({'role': 'user', 'content': user_text})
                            self.input_text = ""
                            self.cursor_pos = 0
                            self.is_thinking = True
                            self.stop_event.clear()
                            
                            threading.Thread(target=self.api_worker, args=(self.messages,), daemon=True).start()
                            
                    elif ch in [curses.KEY_BACKSPACE, '\x7f', '\x08', 127, 8]:
                        if self.cursor_pos > 0:
                            self.input_text = self.input_text[:self.cursor_pos-1] + self.input_text[self.cursor_pos:]
                            self.cursor_pos -= 1
                    elif ch == curses.KEY_DC: # Delete
                        if self.cursor_pos < len(self.input_text):
                            self.input_text = self.input_text[:self.cursor_pos] + self.input_text[self.cursor_pos+1:]
                    elif ch == curses.KEY_LEFT:
                        self.cursor_pos = max(0, self.cursor_pos - 1)
                    elif ch == curses.KEY_RIGHT:
                        self.cursor_pos = min(len(self.input_text), self.cursor_pos + 1)
                    elif ch == curses.KEY_HOME:
                        self.cursor_pos = 0
                    elif ch == curses.KEY_END:
                        self.cursor_pos = len(self.input_text)
                    elif isinstance(ch, str) and ch.isprintable():
                        self.input_text = self.input_text[:self.cursor_pos] + ch + self.input_text[self.cursor_pos:]
                        self.cursor_pos += 1
                
                elif self.focus == "sidebar":
                    if ch == curses.KEY_UP:
                        self.sidebar_idx = max(0, self.sidebar_idx - 1)
                    elif ch == curses.KEY_DOWN:
                        self.sidebar_idx = min(len(self.chats) - 1, self.sidebar_idx + 1)
                    elif ch in ['\n', '\r', 10, 13]: # Enter
                        if self.chats:
                            self.current_chat_id = self.chats[self.sidebar_idx]['id']
                            self.refresh_chats()
                            self.focus = "input"
            except KeyboardInterrupt:
                if self.is_thinking:
                    self.stop_event.set()
                elif self.input_text:
                    self.input_text = ""
                    self.cursor_pos = 0
                else:
                    if self.show_confirm("Exit application?"):
                        break
