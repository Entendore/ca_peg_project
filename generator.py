import sys
import os
import random
import re
import io
import argparse

# ==========================================
# 1. CELLULAR AUTOMATON (Entropy Source)
# ==========================================
class CellularAutomaton:
    def __init__(self, rule=30, width=80):
        self.rule = rule
        self.width = width
        self.cells = [random.randint(0, 1) for _ in range(width)]
        self.bit_buffer = []
        self.history = [self.cells.copy()]

    def _step(self):
        new_cells = []
        for i in range(self.width):
            left = self.cells[(i - 1) % self.width]
            center = self.cells[i]
            right = self.cells[(i + 1) % self.width]
            pattern = (left << 2) | (center << 1) | right
            new_cells.append((self.rule >> pattern) & 1)
        self.cells = new_cells
        self.history.append(self.cells.copy())

    def get_bits(self, n):
        while len(self.bit_buffer) < n:
            self._step()
            self.bit_buffer.extend(self.cells)
        bits = self.bit_buffer[:n]
        self.bit_buffer = self.bit_buffer[n:]
        return bits

def get_vocabulary(grammar_text):
    literals = re.findall(r"'(.*?)'", grammar_text)
    vocab = list(set(literals + [' ', '\n', ';', '\t']))
    vocab.sort(key=len, reverse=True)
    return vocab

def get_ca_text(ca, vocab, length=2000):
    text = ""
    while len(text) < length:
        bits = ca.get_bits(8)
        idx = int("".join(map(str, bits)), 2) % len(vocab)
        text += vocab[idx]
    return text

# ==========================================
# 2. GRAMMA PREPROCESSOR
# ==========================================
def remove_actions(text):
    res, depth = "", 0
    for char in text:
        if char == '{': depth += 1
        elif char == '}': depth = max(0, depth - 1)
        elif depth == 0: res += char
    return res

def replace_optional(text):
    res, in_str, in_opt = "", False, 0
    for c in text:
        if c == "'": in_str = not in_str; res += c
        elif not in_str and c == '[': in_opt += 1; res += '('
        elif not in_str and c == ']' and in_opt > 0: in_opt -= 1; res += ')?'
        else: res += c
    return res

def clean_grammar(text):
    if 'START OF THE GRAMMAR' in text:
        text = text.split('START OF THE GRAMMAR')[1]
    if 'END OF THE GRAMMAR' in text:
        text = text.split('END OF THE GRAMMAR')[0]
        
    lines, valid_lines = text.split('\n'), []
    skip_rule = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and ':' in stripped and not stripped.startswith('|'):
            name = stripped.split(':')[0].strip()
            skip_rule = name.startswith('invalid_')
        if not skip_rule: valid_lines.append(line)
    text = '\n'.join(valid_lines)
    
    text = re.sub(r'#.*', '', text)
    text = remove_actions(text)
    text = re.sub(r'^(\w+)\[.*?\]:', r'\1:', text, flags=re.MULTILINE)
    text = re.sub(r'\b\w+\s*=\s*', '', text)
    text = replace_optional(text)
    text = re.sub(r"'([^']*)'\.(\w+)\+", r"(\2 ('\1' \2)*)", text)
    
    # Left recursion elimination (for CPython)
    text = text.replace("bitwise_or: bitwise_or '|' bitwise_xor | bitwise_xor", "bitwise_or: bitwise_xor ('|' bitwise_xor)*")
    text = text.replace("bitwise_xor: bitwise_xor '^' bitwise_and | bitwise_and", "bitwise_xor: bitwise_and ('^' bitwise_xor)*")
    text = text.replace("bitwise_and: bitwise_and '&' shift_expr | shift_expr", "bitwise_and: shift_expr ('&' bitwise_and)*")
    text = text.replace("sum: sum '+' term | sum '-' term | term", "sum: term (('+' | '-') term)*")
    text = text.replace("dotted_name: dotted_name '.' NAME | NAME", "dotted_name: NAME ('.' NAME)*")
    text = re.sub(r"term: term '\*' factor \| term '/' factor \| term '//' factor \| term '%' factor \| term '@' factor \| invalid_factor \| factor", "term: factor (('*' | '/' | '//' | '%' | '@') factor)*", text)
    
    text = text.replace("NEWLINE", "'\\n'").replace("INDENT", "").replace("DEDENT", "")
    text = text.replace("ENDMARKER", "").replace("TYPE_COMMENT", "")
    text = text.replace("NAME", "'x' / 'y' / 'z' / 'w' / 'i' / 'j'")
    text = text.replace("NUMBER", "'0' / '1' / '2' / '10'")
    text = text.replace("SOFT_KEYWORD", "")
    
    rules = {}
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line: i += 1; continue
        
        # Handle both ':' (CPython) and '<-' (Standard PEG) rule separators
        if '<-' in line:
            name, expr = line.split('<-', 1)
            name, expr = name.strip(), expr.strip()
        elif ':' in line and not line.startswith('|'):
            name, expr = line.split(':', 1)
            name, expr = name.strip(), expr.strip()
        else:
            i += 1
            continue
            
        if not expr:
            expr_parts = []
            i += 1
            while i < len(lines) and lines[i].strip().startswith('|'):
                expr_parts.append(lines[i].strip()[1:].strip())
                i += 1
            expr = ' / '.join(expr_parts)
        else: 
            i += 1
            
        expr = re.sub(r'\s+', ' ', expr).strip()
        if expr: rules[name] = expr
        
    return '\n'.join([f"{n} <- {r}" for n, r in rules.items()])

# ==========================================
# 3. PACKRAT PEG PARSER ENGINE
# ==========================================
class PEGParser:
    def __init__(self, grammar_text):
        self.rules = {}
        self.cache = {}
        self._parse_grammar(grammar_text)

    def _parse_grammar(self, text):
        for line in text.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('#'): continue
            if '<-' in line:
                name, expr = line.split('<-', 1)
                self.rules[name.strip()] = self._parse_expr(expr.strip())

    def _parse_expr(self, expr):
        choices = [c.strip() for c in expr.split('/')]
        if len(choices) > 1: return ('CHOICE', [self._parse_seq(c) for c in choices])
        return self._parse_seq(choices[0])

    def _parse_seq(self, seq):
        terms = []
        for part in seq.split():
            if part.endswith('*'): terms.append(('STAR', self._parse_prim(part[:-1])))
            elif part.endswith('+'): terms.append(('PLUS', self._parse_prim(part[:-1])))
            elif part.endswith('?'): terms.append(('OPT', self._parse_prim(part[:-1])))
            else: terms.append(self._parse_prim(part))
        return ('SEQ', terms)

    def _parse_prim(self, p):
        if p.startswith('&'): return ('POSITIVE_LOOKAHEAD', self._parse_prim(p[1:]))
        if p.startswith('!'): return ('NEGATIVE_LOOKAHEAD', self._parse_prim(p[1:]))
        if p.startswith('~'): return ('COMMIT', self._parse_prim(p[1:]))
        if p.startswith("'") and p.endswith("'"): return ('LIT', p[1:-1])
        if p.startswith('[') and p.endswith(']'): return ('CLASS', p[1:-1])
        if p.startswith('(') and p.endswith(')'): return self._parse_expr(p[1:-1])
        return ('REF', p)

    def match(self, pattern, text, pos):
        cache_key = (id(pattern), pos)
        if cache_key in self.cache: return self.cache[cache_key]
        res = self._match_impl(pattern, text, pos)
        self.cache[cache_key] = res
        return res

    def _match_impl(self, pattern, text, pos):
        t = pattern[0]
        if t == 'LIT':
            lit = pattern[1]
            if text[pos:pos+len(lit)] == lit: return (lit, pos + len(lit))
            return (None, pos)
        elif t == 'CLASS':
            if pos < len(text) and text[pos] in pattern[1]: return (text[pos], pos + 1)
            return (None, pos)
        elif t == 'REF':
            if pattern[1] in self.rules:
                res, new_pos = self.match(self.rules[pattern[1]], text, pos)
                if res is not None: return ((pattern[1], res), new_pos)
            return (None, pos)
        elif t == 'SEQ':
            res, curr = [], pos
            for p in pattern[1]:
                r, curr = self.match(p, text, curr)
                if r is None: return (None, pos)
                res.append(r)
            return (res, curr)
        elif t == 'CHOICE':
            for p in pattern[1]:
                r, curr = self.match(p, text, pos)
                if r is not None: return (r, curr)
            return (None, pos)
        elif t == 'STAR':
            res, curr = [], pos
            while True:
                r, new_pos = self.match(pattern[1], text, curr)
                if r is None or new_pos == curr: break
                res.append(r); curr = new_pos
            return (res, curr)
        elif t == 'PLUS':
            r, curr = self.match(pattern[1], text, pos)
            if r is None: return (None, pos)
            res = [r]
            while True:
                r, new_pos = self.match(pattern[1], text, curr)
                if r is None or new_pos == curr: break
                res.append(r); curr = new_pos
            return (res, curr)
        elif t == 'OPT':
            r, new_pos = self.match(pattern[1], text, pos)
            return ([r], new_pos) if r is not None else ([], pos)
        elif t == 'POSITIVE_LOOKAHEAD':
            r, _ = self.match(pattern[1], text, pos)
            return (r if r is not None else None, pos)
        elif t == 'NEGATIVE_LOOKAHEAD':
            r, _ = self.match(pattern[1], text, pos)
            return (None, pos) if r is not None else ([], pos)
        elif t == 'COMMIT':
            return self.match(pattern[1], text, pos)
        return (None, pos)

# ==========================================
# 4. GENERIC AST EXTRACTOR
# ==========================================
def get_text(node):
    if isinstance(node, str): return node
    if isinstance(node, list): return "".join([get_text(n) for n in node])
    if isinstance(node, tuple): return get_text(node[1])
    return ""

def extract(node, rule_name):
    if isinstance(node, tuple) and node[0].lower() == rule_name.lower(): return node[1]
    if isinstance(node, list):
        for n in node:
            res = extract(n, rule_name)
            if res is not None: return res
    if isinstance(node, tuple) and isinstance(node[1], (list, tuple)):
        return extract(node[1], rule_name)
    return None

def extract_all(node, rule_name):
    res = []
    if isinstance(node, list):
        for n in node: res.extend(extract_all(n, rule_name))
    elif isinstance(node, tuple):
        if node[0].lower() == rule_name.lower(): res.append(node[1])
        if isinstance(node[1], (list, tuple)): res.extend(extract_all(node[1], rule_name))
    return res

def build_ast_stmt(stmt_tree):
    if not isinstance(stmt_tree, tuple): return None
    rule_name, content = stmt_tree
    
    if rule_name.lower() in ['statement', 'simple_stmts', 'compound_stmt', 'simple_stmt', 'block', 'statements', 'file', 'interactive', 'sourcefile', 'stmt', 'stmts']:
        if isinstance(content, list):
            for item in content:
                res = build_ast_stmt(item)
                if res: return res
        return build_ast_stmt(content)
        
    if rule_name.lower() in ['assign', 'assignment', 'letstmt', 'let_stmt']:
        var_node = extract(content, 'NAME') or extract(content, 'Identifier') or extract(content, 'identifier')
        expr_node = extract(content, 'expression') or extract(content, 'Expr') or extract(content, 'expr') or extract(content, 'annotated_rhs')
        if var_node and expr_node:
            return {'type': 'Assign', 'var': get_text(var_node), 'expr': build_expr(expr_node)}
            
    elif rule_name.lower() in ['if', 'if_stmt', 'ifstmt', 'if_statement']:
        expr_node = extract(content, 'named_expression') or extract(content, 'expression') or extract(content, 'Expr') or extract(content, 'expr')
        stmts = extract_all(content, 'Stmt') or extract_all(content, 'statement') or extract_all(content, 'stmt')
        stmts = [build_ast_stmt(s) for s in stmts if isinstance(s, tuple)]
        stmts = [s for s in stmts if s]
        
        else_node = extract(content, 'ElseBlock') or extract(content, 'else_block') or extract(content, 'else')
        else_body = []
        if else_node:
            else_stmts = extract_all(else_node, 'Stmt') or extract_all(else_node, 'statement') or extract_all(else_node, 'stmt')
            else_body = [build_ast_stmt(s) for s in else_stmts if isinstance(s, tuple)]
            else_body = [s for s in else_body if s]
            
        if expr_node:
            return {'type': 'If', 'cond': build_expr(expr_node), 'body': stmts, 'else': else_body}
            
    elif rule_name.lower() in ['while', 'while_stmt', 'whilestmt', 'while_statement', 'for', 'for_stmt', 'forstmt', 'for_statement']:
        expr_node = extract(content, 'named_expression') or extract(content, 'expression') or extract(content, 'Expr') or extract(content, 'expr')
        stmts = extract_all(content, 'Stmt') or extract_all(content, 'statement') or extract_all(content, 'stmt')
        stmts = [build_ast_stmt(s) for s in stmts if isinstance(s, tuple)]
        stmts = [s for s in stmts if s]
        if expr_node:
            return {'type': 'While', 'cond': build_expr(expr_node), 'body': stmts}
            
    elif rule_name.lower() in ['return', 'return_stmt', 'returnstmt', 'return_statement']:
        expr_node = extract(content, 'expression') or extract(content, 'Expr') or extract(content, 'expr')
        if expr_node: return {'type': 'Return', 'expr': build_expr(expr_node)}
        
    elif rule_name.lower() in ['pass', 'pass_stmt', 'break', 'break_stmt', 'breakstmt']:
        return {'type': 'Pass'}
        
    return None

def build_expr(expr_node):
    if isinstance(expr_node, tuple):
        rule_name, content = expr_node
        if rule_name.lower() in ['expression', 'disjunction', 'conjunction', 'inversion', 'comparison', 'bitwise_or', 'bitwise_xor', 'bitwise_and', 'shift_expr', 'sum', 'term', 'factor', 'power', 'await_primary', 'primary', 'named_expression', 'star_expression', 'atom', 'expr', 'binop', 'primary']:
            return build_expr(content)
        if rule_name.lower() in ['name', 'identifier']:
            return {'type': 'Var', 'name': get_text(content)}
        if rule_name.lower() in ['number', 'num']:
            return {'type': 'Num', 'val': get_text(content)}
        
    if isinstance(expr_node, list):
        left = None
        for item in expr_node:
            if isinstance(item, tuple) and item[0].lower() not in ('skip',):
                left = build_expr(item)
                break
                
        for item in expr_node:
            if isinstance(item, list):
                for seq in item:
                    if isinstance(seq, list) and len(seq) == 2:
                        op = seq[0] if isinstance(seq[0], str) else get_text(seq[0])
                        right = build_expr(seq[1])
                        if left and op and right:
                            left = {'type': 'BinOp', 'left': left, 'op': op, 'right': right}
        return left
        
    if isinstance(expr_node, str):
        if expr_node in ('x', 'y', 'z', 'w', 'i', 'j'): return {'type': 'Var', 'name': expr_node}
        if expr_node in ('0', '1', '2', '10'): return {'type': 'Num', 'val': expr_node}
    return None

# ==========================================
# 5. TRANSPILER
# ==========================================
def collect_vars(ast):
    vars = set()
    for node in ast:
        if node['type'] == 'Assign': vars.add(node['var'])
        elif node['type'] in ('If', 'While'):
            if 'cond' in node: vars.update(collect_vars_expr(node['cond']))
            vars.update(collect_vars(node['body']))
            if 'else' in node: vars.update(collect_vars(node['else']))
        elif 'expr' in node: vars.update(collect_vars_expr(node['expr']))
    return vars

def collect_vars_expr(expr):
    if expr is None: return set()
    if expr['type'] == 'Var': return {expr['name']}
    if expr['type'] == 'BinOp': return collect_vars_expr(expr['left']).union(collect_vars_expr(expr['right']))
    return set()

def emit_expr(expr):
    if expr is None: return ""
    if expr['type'] == 'Var': return expr['name']
    if expr['type'] == 'Num': return expr['val']
    if expr['type'] == 'BinOp': return f"{emit_expr(expr['left'])} {expr['op']} {emit_expr(expr['right'])}"

def emit_python(ast, indent=0):
    code, pad = "", "    " * indent
    for node in ast:
        if node['type'] == 'Assign': code += f"{pad}{node['var']} = {emit_expr(node['expr'])}\n"
        elif node['type'] == 'If':
            code += f"{pad}if {emit_expr(node['cond'])}:\n"
            code += emit_python(node['body'], indent+1) if node['body'] else f"{pad}    pass\n"
            if node.get('else'): code += f"{pad}else:\n" + emit_python(node['else'], indent+1)
        elif node['type'] == 'While':
            code += f"{pad}while {emit_expr(node['cond'])} and __ca_safety > 0:\n{pad}    __ca_safety -= 1\n"
            code += emit_python(node['body'], indent+1) if node['body'] else f"{pad}    pass\n"
        elif node['type'] == 'Return': code += f"{pad}return {emit_expr(node['expr'])}\n"
        elif node['type'] == 'Pass': code += f"{pad}pass\n"
    return code

def emit_c(ast, lang="C", indent=1):
    code, pad = "", "    " * indent
    for node in ast:
        if node['type'] == 'Assign': code += f"{pad}{node['var']} = {emit_expr(node['expr'])};\n"
        elif node['type'] == 'If':
            code += f"{pad}if ({emit_expr(node['cond'])}) {{\n{emit_c(node['body'], lang, indent+1)}{pad}}}"
            if node.get('else'): code += f" else {{\n{emit_c(node['else'], lang, indent+1)}{pad}}}"
            code += "\n"
        elif node['type'] == 'While': code += f"{pad}while ({emit_expr(node['cond'])}) {{\n{pad}    if (--__ca_safety <= 0) break;\n{emit_c(node['body'], lang, indent+1)}{pad}}}\n"
        elif node['type'] == 'Return': code += f"{pad}return {emit_expr(node['expr'])};\n"
        elif node['type'] == 'Pass': code += f"{pad}/* pass */\n"
    return code

def emit_go(ast, indent=1):
    code, pad = "", "\t" * indent
    for node in ast:
        if node['type'] == 'Assign': code += f"{pad}{node['var']} = {emit_expr(node['expr'])}\n"
        elif node['type'] == 'If':
            code += f"{pad}if {emit_expr(node['cond'])} {{\n{emit_go(node['body'], indent+1)}{pad}}}"
            if node.get('else'): code += f" else {{\n{emit_go(node['else'], indent+1)}{pad}}}"
            code += "\n"
        elif node['type'] == 'While': code += f"{pad}for {emit_expr(node['cond'])} {{\n{pad}\tif __ca_safety--; __ca_safety <= 0 {{ break }}\n{emit_go(node['body'], indent+1)}{pad}}}\n"
        elif node['type'] == 'Return': code += f"{pad}return {emit_expr(node['expr'])}\n"
        elif node['type'] == 'Pass': code += f"{pad}// pass\n"
    return code

def emit_rust(ast, indent=1):
    code, pad = "", "    " * indent
    for node in ast:
        if node['type'] == 'Assign': code += f"{pad}{node['var']} = {emit_expr(node['expr'])};\n"
        elif node['type'] == 'If':
            code += f"{pad}if {emit_expr(node['cond'])} {{\n{emit_rust(node['body'], indent+1)}{pad}}}"
            if node.get('else'): code += f" else {{\n{emit_rust(node['else'], indent+1)}{pad}}}"
            code += "\n"
        elif node['type'] == 'While': code += f"{pad}while {emit_expr(node['cond'])} {{\n{pad}    if __ca_safety <= 0 {{ break; }}\n{pad}    __ca_safety -= 1;\n{emit_rust(node['body'], indent+1)}{pad}}}\n"
        elif node['type'] == 'Return': code += f"{pad}return {emit_expr(node['expr'])};\n"
        elif node['type'] == 'Pass': code += f"{pad}/* pass */\n"
    return code

def transpile(ast_list):
    vars = sorted(list(collect_vars(ast_list)))
    return {
        "Python": "# Python Auto-Generated Code\n__ca_safety = 100000\n" + "".join([f"{v} = 0\n" for v in vars]) + emit_python(ast_list),
        "C": "#include <stdio.h>\nint main() {\n    int __ca_safety = 100000;\n" + "".join([f"    int {v} = 0;\n" for v in vars]) + emit_c(ast_list, "C") + "    return 0;\n}\n",
        "C++": "#include <iostream>\nint main() {\n    int __ca_safety = 100000;\n" + "".join([f"    int {v} = 0;\n" for v in vars]) + emit_c(ast_list, "C++") + "    return 0;\n}\n",
        "Go": "package main\nimport \"fmt\"\nfunc main() {\n    __ca_safety := 100000\n" + "".join([f"    {v} := 0\n" for v in vars]) + emit_go(ast_list) + "}\n",
        "Rust": "fn main() {\n    let mut __ca_safety = 100000;\n" + "".join([f"    let mut {v} = 0;\n" for v in vars]) + emit_rust(ast_list) + "}\n"
    }

# ==========================================
# 6. VISUALIZERS & SANDBOX
# ==========================================
C_RESET = "\033[0m"; C_GRAY = "\033[90m"; C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"; C_CYAN = "\033[96m"; C_MAGENTA = "\033[95m"; C_RED = "\033[91m"

def visualize_ca(ca, generations=15):
    print(f"{C_YELLOW}{'='*70}\n1D Cellular Automaton (Rule 30) - First {generations} Generations\n{'='*70}{C_RESET}")
    for row in ca.history[:generations]:
        print("".join(['█' if c == 1 else ' ' for c in row]))

def visualize_parse(text, parse_segments):
    print(f"\n{C_YELLOW}{'='*70}\nPEG Parser Linter Scan (Green=Valid Code, Gray=Noise)\n{'='*70}{C_RESET}")
    vis_text = ""
    for chunk, is_valid in parse_segments:
        clean_chunk = chunk.replace('\n', '¶').replace(' ', '·')
        vis_text += f"{C_GREEN}{clean_chunk}{C_RESET}" if is_valid else f"{C_GRAY}{clean_chunk}{C_RESET}"
    print(vis_text)

def visualize_ast(ast, indent="", last=True, name="Root"):
    prefix = "└── " if last else "├── "
    print(f"{C_CYAN}{indent}{prefix}{name}{C_RESET}")
    if isinstance(ast, dict):
        keys = list(ast.keys())
        for i, k in enumerate(keys):
            is_last_child = (i == len(keys) - 1)
            child_indent = indent + ("    " if last else "│   ")
            visualize_ast(ast[k], child_indent, is_last_child, k)
    elif isinstance(ast, list):
        for i, item in enumerate(ast):
            is_last_child = (i == len(ast) - 1)
            child_indent = indent + ("    " if last else "│   ")
            visualize_ast(item, child_indent, is_last_child, f"[{i}]")
    else:
        print(f"{C_MAGENTA}{indent}    └── {repr(ast)}{C_RESET}")

def execute_sandboxed(code):
    print(f"\n{C_YELLOW}{'='*70}\nLive Execution Output (Python)\n{'='*70}{C_RESET}")
    namespace = {'__ca_safety': 100000, 'x': 0, 'y': 0, 'z': 0, 'w': 0, 'i': 0, 'j': 0}
    stdout_buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = stdout_buf
    try:
        exec(code, namespace)
        output = stdout_buf.getvalue().strip()
        print(output if output else f"{C_GRAY}(Execution produced no output){C_RESET}")
    except Exception as e:
        print(f"{C_RED}Runtime Error: {e}{C_RESET}")
    finally:
        sys.stdout = old_stdout

# ==========================================
# 7. BUILT-IN GRAMMARS
# ==========================================
PY_GRAMMAR = """# mini_python.peg
Skip <- [ \\n\\t]*
Stmt <- IfStmt / WhileStmt / AssignStmt / PrintStmt
IfStmt <- 'if' Skip '(' Skip Expr Skip ')' Skip '{' Skip Stmt* Skip '}' ElseBlock?
ElseBlock <- 'else' Skip '{' Skip Stmt* Skip '}'
WhileStmt <- 'while' Skip '(' Skip Expr Skip ')' Skip '{' Skip Stmt* Skip '}'
AssignStmt <- Identifier Skip '=' Skip Expr Skip ';'
PrintStmt <- 'print' Skip '(' Skip Expr Skip ')' Skip ';'
Expr <- BinOp / Primary
BinOp <- Primary Skip Op Skip Primary
Op <- '==' / '<' / '>' / '+' / '-' / '*'
Primary <- '(' Skip Expr Skip ')' / Identifier / Number
Identifier <- 'x' / 'y' / 'z' / 'w'
Number <- '10' / '0' / '1' / '2'
"""

RUST_GRAMMAR = """# mini_rust.peg
SourceFile <- Stmt*
Stmt <- IfStmt / WhileStmt / LetStmt / ReturnStmt / BreakStmt / ExprStmt
IfStmt <- 'if' Expr '{' Stmt* '}' 'else' '{' Stmt* '}' / 'if' Expr '{' Stmt* '}'
WhileStmt <- 'while' Expr '{' Stmt* '}'
LetStmt <- 'let' Identifier '=' Expr ';'
ReturnStmt <- 'return' Expr ';'
BreakStmt <- 'break' ';'
ExprStmt <- Expr ';'
Expr <- BinOp / Primary
BinOp <- Primary Op Primary
Op <- '+' / '-' / '*' / '<' / '>' / '=='
Primary <- '(' Expr ')' / Identifier / Number
Identifier <- 'x' / 'y' / 'z' / 'w'
Number <- '0' / '1' / '2' / '10'
"""

GO_GRAMMAR = """# mini_go.peg
SourceFile <- Stmt*
Stmt <- IfStmt / ForStmt / LetStmt / ReturnStmt / BreakStmt / ExprStmt
IfStmt <- 'if' Expr '{' Stmt* '}' 'else' '{' Stmt* '}' / 'if' Expr '{' Stmt* '}'
ForStmt <- 'for' Expr '{' Stmt* '}'
LetStmt <- 'var' Identifier '=' Expr ';'
ReturnStmt <- 'return' Expr ';'
BreakStmt <- 'break' ';'
ExprStmt <- Expr ';'
Expr <- BinOp / Primary
BinOp <- Primary Op Primary
Op <- '+' / '-' / '*' / '<' / '>' / '=='
Primary <- '(' Expr ')' / Identifier / Number
Identifier <- 'x' / 'y' / 'z' / 'w'
Number <- '0' / '1' / '2' / '10'
"""

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate multi-language code using 1D Cellular Automata and a PEG Grammar.")
    parser.add_argument("grammar", nargs="?", default="python", help="Path to grammar file, or 'python', 'rust', 'go'")
    parser.add_argument("--rule", type=int, default=30, help="Elementary CA rule (0-255)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for CA initial state")
    parser.add_argument("--length", type=int, default=3000, help="Length of generated CA text")
    args = parser.parse_args()

    grammar_arg = args.grammar.lower()
    
    if grammar_arg == "python":
        args.grammar = "mini_python.peg"
        if not os.path.exists(args.grammar):
            with open(args.grammar, "w") as f: f.write(PY_GRAMMAR)
    elif grammar_arg == "rust":
        args.grammar = "mini_rust.peg"
        if not os.path.exists(args.grammar):
            with open(args.grammar, "w") as f: f.write(RUST_GRAMMAR)
    elif grammar_arg == "go":
        args.grammar = "mini_go.peg"
        if not os.path.exists(args.grammar):
            with open(args.grammar, "w") as f: f.write(GO_GRAMMAR)
            
    if not os.path.exists(args.grammar):
        print(f"{C_RED}Grammar file '{args.grammar}' not found. Please provide a valid file, or use 'rust'/'go'/'python' as the argument.{C_RESET}")
        sys.exit(1)
        
    with open(args.grammar, "r") as f: raw_grammar = f.read()

    print(f"{C_YELLOW}Loaded Grammar: {args.grammar}{C_RESET}")
    
    grammar_text = clean_grammar(raw_grammar)
    with open("cleaned_grammar.peg", "w") as f: f.write(grammar_text)
    print(f"{C_GRAY}Cleaned grammar saved to: cleaned_grammar.peg{C_RESET}")
    
    peg = PEGParser(grammar_text)
    
    vocab = get_vocabulary(grammar_text)
    random.seed(args.seed)
    ca = CellularAutomaton(rule=args.rule, width=80)
    ca_text = get_ca_text(ca, vocab, length=args.length)
    
    visualize_ca(ca)
    
    pos, ast_list, parse_segments = 0, [], []
    start_rule = 'statement' if 'statement' in peg.rules else ('Stmt' if 'Stmt' in peg.rules else 'SourceFile')
    
    while pos < len(ca_text):
        res, new_pos = peg.match(peg.rules[start_rule], ca_text, pos)
        if res and new_pos > pos:
            stmt_tree = (start_rule, res)
            ast_node = build_ast_stmt(stmt_tree)
            if ast_node:
                ast_list.append(ast_node)
                parse_segments.append((ca_text[pos:new_pos], True))
            else:
                parse_segments.append((ca_text[pos:new_pos], False))
            pos = new_pos
        else:
            parse_segments.append((ca_text[pos:pos+1], False))
            pos += 1
            
    visualize_parse(ca_text, parse_segments)
    
    print(f"\n{C_YELLOW}{'='*70}\nExtracted Abstract Syntax Tree (AST)\n{'='*70}{C_RESET}")
    visualize_ast(ast_list)
    
    print(f"\n{C_YELLOW}{'='*70}\nTranspiled Multi-Language Output\n{'='*70}{C_RESET}")
    
    codes = transpile(ast_list)
    for lang, code in codes.items():
        print(f"{C_CYAN}--- {lang} ---{C_RESET}")
        print(code)
        print("-" * 70 + "\n")
        
    execute_sandboxed(codes["Python"])
