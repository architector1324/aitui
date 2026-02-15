import curses
import re
import textwrap

def parse_inline(text, base_attr):
    """Parses inline markdown (bold, italic, code) and returns list of (text, attr)."""
    segments = []
    # Combined regex for bold (** or __), italic (* or _), and inline code (`)
    pattern = re.compile(r'(\*\*.*?\*\*|__.*?__|`.*?`|\*.*?\*|_.*?_)')
    
    last_end = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        # Add text before match
        if start > last_end:
            segments.append((text[last_end:start], base_attr))
        
        m_text = match.group(0)
        if m_text.startswith('**') or m_text.startswith('__'):
            segments.append((m_text[2:-2], base_attr | curses.A_BOLD))
        elif m_text.startswith('`'):
            segments.append((m_text[1:-1], curses.color_pair(4)))
        elif m_text.startswith('*') or m_text.startswith('_'):
            segments.append((m_text[1:-1], base_attr | curses.A_UNDERLINE))
            
        last_end = end
        
    if last_end < len(text):
        segments.append((text[last_end:], base_attr))
        
    return segments

def wrap_styled_text(segments, width):
    """Wraps a list of (text, attr) segments into multiple lines."""
    if not segments:
        return []
        
    lines = []
    current_line = []
    current_width = 0
    
    for text, attr in segments:
        parts = re.split(r'(\s+)', text)
        for part in parts:
            if not part:
                continue
            
            part_len = len(part)
            
            if current_width + part_len > width:
                if part.isspace():
                    if current_line:
                        lines.append(current_line)
                        current_line = []
                        current_width = 0
                    continue
                
                if part_len > width:
                    while len(part) > 0:
                        space_left = width - current_width
                        if space_left <= 0:
                            lines.append(current_line)
                            current_line = []
                            current_width = 0
                            space_left = width
                        
                        chunk = part[:space_left]
                        current_line.append((chunk, attr))
                        current_width += len(chunk)
                        part = part[space_left:]
                        if len(part) > 0:
                            lines.append(current_line)
                            current_line = []
                            current_width = 0
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = [(part, attr)]
                    current_width = part_len
            else:
                current_line.append((part, attr))
                current_width += part_len
                
    if current_line:
        lines.append(current_line)
        
    return lines

def truncate_segments(segments, max_len):
    """Truncates segments to a maximum length, adding ellipsis if needed."""
    result = []
    curr_len = 0
    for text, attr in segments:
        if curr_len + len(text) > max_len:
            remaining = max_len - curr_len
            if remaining > 1:
                result.append((text[:remaining-1] + "…", attr))
            elif remaining == 1:
                result.append(("…", attr))
            curr_len = max_len
            break
        result.append((text, attr))
        curr_len += len(text)
    return result, curr_len

def parse_table(lines, start_idx, width, base_color_pair):
    """Parses a markdown table and returns rendered lines with Unicode borders."""
    table_lines = []
    i = start_idx
    while i < len(lines) and lines[i].strip().startswith('|'):
        table_lines.append(lines[i])
        i += 1
    
    rows_raw = []
    for tl in table_lines:
        if re.match(r'^\s*\|[ \-:|]+\|\s*$', tl):
            continue
        cols = [c.strip() for c in tl.split('|')]
        if not cols[0]: cols = cols[1:]
        if cols and not cols[-1]: cols = cols[:-1]
        if cols:
            rows_raw.append(cols)
    
    if not rows_raw:
        return [], i
    
    # Parse inline markdown for each cell
    rows_segments = []
    for r in rows_raw:
        row_cols = [parse_inline(cell, base_color_pair) for cell in r]
        rows_segments.append(row_cols)
    
    num_cols = max(len(r) for r in rows_segments)
    col_widths = [0] * num_cols
    for r in rows_segments:
        for j, segments in enumerate(r):
            cell_len = sum(len(text) for text, attr in segments)
            col_widths[j] = max(col_widths[j], cell_len)
            
    total_w = sum(col_widths) + (num_cols * 3) + 1
    
    if total_w > width:
        available = width - (num_cols * 3) - 1
        if available < num_cols * 3:
            available = num_cols * 3
        
        current_total = sum(col_widths)
        if current_total > 0:
            col_widths = [max(1, int(w * available / current_total)) for w in col_widths]

    rendered_rows = []
    
    # Unicode border helpers
    def get_top_border():
        return "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
    def get_mid_border():
        return "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
    def get_bot_border():
        return "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

    rendered_rows.append([(get_top_border(), curses.color_pair(6))])
    
    for r_idx, r_segs in enumerate(rows_segments):
        line_segments = [("│", curses.color_pair(6))]
        for j in range(num_cols):
            cell_segs = r_segs[j] if j < len(r_segs) else []
            truncated, actual_len = truncate_segments(cell_segs, col_widths[j])
            
            line_segments.append((" ", base_color_pair))
            line_segments.extend(truncated)
            if actual_len < col_widths[j]:
                line_segments.append((" " * (col_widths[j] - actual_len), base_color_pair))
            line_segments.append((" ", base_color_pair))
            line_segments.append(("│", curses.color_pair(6)))
        rendered_rows.append(line_segments)
        
        if r_idx == 0 and len(rows_segments) > 1:
             rendered_rows.append([(get_mid_border(), curses.color_pair(6))])
             
    rendered_rows.append([(get_bot_border(), curses.color_pair(6))])
    return rendered_rows, i

def render_markdown(text, width, base_color_pair):
    """Main entry point for rendering markdown text."""
    all_lines = []
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Code block
        if line.strip().startswith('```'):
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            
            all_lines.append([(" " * width, curses.color_pair(4))])
            for cl in code_lines:
                if not cl.strip():
                    all_lines.append([(" " * width, curses.color_pair(4))])
                    continue
                
                wrapped = textwrap.wrap(cl, width=width-2)
                for wcl in wrapped:
                    all_lines.append([(" " + wcl.ljust(width-1), curses.color_pair(4))])
            all_lines.append([(" " * width, curses.color_pair(4))])
            i += 1
            continue
            
        # Header
        header_match = re.match(r'^\s*(#{1,6})\s+(.*)', line)
        if header_match:
            level = len(header_match.group(1))
            content = header_match.group(2)
            all_lines.append([(f"{'#'*level} {content}", curses.color_pair(5) | curses.A_BOLD)])
            i += 1
            continue
            
        # List
        list_match = re.match(r'^(\s*)([*+-]|\d+\.)\s+(.*)', line)
        if list_match:
            indent = list_match.group(1)
            bullet_char = list_match.group(2)
            bullet = "•" if bullet_char in "*+-" else bullet_char
            content = list_match.group(3)
            
            prefix = f"{indent}{bullet} "
            segments = parse_inline(content, base_color_pair)
            wrapped = wrap_styled_text(segments, width - len(prefix))
            
            if not wrapped:
                all_lines.append([(prefix, base_color_pair)])
            else:
                for j, w_line in enumerate(wrapped):
                    if j == 0:
                        all_lines.append([(prefix, base_color_pair)] + w_line)
                    else:
                        all_lines.append([(" " * len(prefix), base_color_pair)] + w_line)
            i += 1
            continue

        # Table
        if line.strip().startswith('|'):
            table_rendered, next_i = parse_table(lines, i, width, base_color_pair)
            if table_rendered:
                all_lines.extend(table_rendered)
                i = next_i
                continue

        # Paragraph or empty line
        if line.strip():
            segments = parse_inline(line, base_color_pair)
            wrapped = wrap_styled_text(segments, width)
            for w_line in wrapped:
                all_lines.append(w_line)
        else:
            all_lines.append([("", base_color_pair)])
            
        i += 1
        
    return all_lines
