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
        self.show_sidebar = True
        self.current_chat_id = None
        self.chats = []
        self.messages = []
        self.focus = "input" # "input" or "sidebar"
        self.sidebar_idx = 0
        self.input_text = ""
        self.cursor_pos = 0
        
        self.is_thinking = False
        self.stream_queue = queue.Queue()
        
        curses.curs_set(1)
        self.stdscr.keypad(True)
        self.stdscr.nodelay(True) # Non-blocking input
        self.init_colors()
        self.setup_windows()
        self.refresh_chats()
        self.draw_all()

    def init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_WHITE)

    def setup_windows(self):
        h, w = self.stdscr.getmaxyx()
        sidebar_w = self.sidebar_width if self.show_sidebar else 0
        
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
        else:
            # Find the index of the current chat in the updated list
            found = False
            for i, chat in enumerate(self.chats):
                if chat['id'] == self.current_chat_id:
                    self.sidebar_idx = i
                    found = True
                    break
            if not found:
                self.current_chat_id = self.chats[0]['id']
                self.sidebar_idx = 0
        
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
        y = 1
        
        # Simple scroll-to-bottom logic: only draw what fits from the end
        all_lines = []
        for msg in self.messages:
            role = msg['role'].upper()
            color = curses.color_pair(1) if msg['role'] == 'user' else curses.color_pair(2)
            prefix = f"[{role}]: "
            max_w = w - 15
            
            content = msg['content']
            wrapped = textwrap.wrap(content, width=max_w)
            
            if not wrapped: # Handle empty content during streaming
                all_lines.append((prefix, color, ""))
            else:
                for i, line in enumerate(wrapped):
                    all_lines.append((prefix if i == 0 else " " * len(prefix), color, line))
            all_lines.append(("", 0, "")) # Spacer

        # Only show lines that fit in the window
        visible_lines = all_lines[-(h-2):] if len(all_lines) > (h-2) else all_lines
        
        for prefix, color, line in visible_lines:
            if y >= h - 1: break
            if prefix.strip():
                self.chat_win.addstr(y, 1, prefix, color | curses.A_BOLD)
            self.chat_win.addstr(y, 15, line)
            y += 1
            
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
        self.stdscr.addstr(h-1, 1, "Ctrl+H: Help | Esc: Exit", curses.A_DIM)
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

    def api_worker(self, messages):
        try:
            for chunk in api.call_llm(self.provider, self.config, messages):
                self.stream_queue.put(("chunk", chunk))
            self.stream_queue.put(("done", None))
        except Exception as e:
            self.stream_queue.put(("error", str(e)))

    def run(self):
        while True:
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
                        if len(self.messages) <= 2: # First exchange
                            title = self.messages[0]['content'][:20]
                            self.db.update_chat_title(self.current_chat_id, title)
                            self.refresh_chats()
                    elif msg_type == "error":
                        self.is_thinking = False
                        error_msg = f"Error: {data}"
                        self.messages.append({'role': 'assistant', 'content': error_msg})
                        self.db.add_message(self.current_chat_id, 'assistant', error_msg)
                    self.draw_all()
            except queue.Empty:
                pass

            self.draw_all()
            
            ch = self.input_win.getch()
            if ch == -1:
                curses.napms(10)
                continue
            
            if ch == 27: # Esc
                if self.show_confirm("Exit application?"): break
            elif ch == 9: # Tab
                if self.show_sidebar:
                    self.focus = "sidebar" if self.focus == "input" else "input"
                else:
                    self.focus = "input"
            elif ch == 2: # Ctrl+B
                self.show_sidebar = not self.show_sidebar
                if not self.show_sidebar: self.focus = "input"
                self.setup_windows()
            elif ch == 8: # Ctrl+H
                self.show_help()
            elif ch == 14: # Ctrl+N
                self.current_chat_id = self.db.create_chat(self.provider, self.model)
                self.input_text = ""
                self.cursor_pos = 0
                self.refresh_chats()
            elif ch == 4: # Ctrl+D
                target_id = self.chats[self.sidebar_idx]['id'] if self.focus == "sidebar" else self.current_chat_id
                if target_id and self.show_confirm("Delete this chat?"):
                    self.db.delete_chat(target_id)
                    self.current_chat_id = None
                    self.refresh_chats()
            
            elif self.focus == "input":
                if ch in [10, 13]: # Enter
                    if self.input_text.strip() and not self.is_thinking:
                        user_text = self.input_text.strip()
                        self.db.add_message(self.current_chat_id, 'user', user_text)
                        self.messages.append({'role': 'user', 'content': user_text})
                        self.input_text = ""
                        self.cursor_pos = 0
                        self.is_thinking = True
                        
                        threading.Thread(target=self.api_worker, args=(self.messages,), daemon=True).start()
                        
                elif ch in [curses.KEY_BACKSPACE, 127, 8]:
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
                elif 32 <= ch <= 126: # ASCII
                    self.input_text = self.input_text[:self.cursor_pos] + chr(ch) + self.input_text[self.cursor_pos:]
                    self.cursor_pos += 1
            
            elif self.focus == "sidebar":
                if ch == curses.KEY_UP:
                    self.sidebar_idx = max(0, self.sidebar_idx - 1)
                elif ch == curses.KEY_DOWN:
                    self.sidebar_idx = min(len(self.chats) - 1, self.sidebar_idx + 1)
                elif ch in [10, 13]: # Enter
                    if self.chats:
                        self.current_chat_id = self.chats[self.sidebar_idx]['id']
                        self.messages = [dict(m) for m in self.db.get_messages(self.current_chat_id)]
                        self.focus = "input"
