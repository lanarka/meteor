#!/usr/bin/env python3
"""
Meteor frontend - parses a JavaScript-like .meteor source file and writes the Meteor AST (JSON)
that meteor.py (the backend) turns into Quartz assembler.

    python3 frontend.py hello.meteor -o hello.json             # program AST
    python3 frontend.py hello.meteor -o hello.json --module    # module AST (for "imports")
    python3 frontend.py hello.meteor                           # -> hello.json
    python3 frontend.py hello.meteor -o -                      # JSON to stdout

The frontend only builds the tree. It checks the syntax and the expression / condition split,
everything else (undeclared names, types, ...) is checked by the backend. Needs: pip install lark

======================================================================
LANGUAGE OVERVIEW
======================================================================
Comments:   // line comment        /* block comment */
            /// text               a *comment statement*  ->  {"comment": "text"}

File layout (the order of declarations is kept, directives may appear anywhere at top level):

    program "Title";               // header comment                    (program only)
    module  name;                  // module name, default = file name  (--module only)
    runtime 5;                     //                                   (program only)
    import  "../lib/text.obj";     // "imports"
    include "../inc/std.quartz";   // "includes"
    ram(start: 0x0100, size: 32768);        // "ram"                  (program only)
    const_type i32;                // type of untyped constants         (program only)
    bounds_check true;             //                                   (program only)

    const N = 8;                   // "consts"      (value = constant expression)
    const MASK = N * 2 - 1;

    let a: i32;                    // "globals" - scalar
    let b: u8 = 'A';
    let data: i32[N] = [5, 3, 9];  // array (size optional when there is an initialiser)
    let pal: u32[] = [COLOR_RED, COLOR_BLUE];
    let msg: u8[] = "Hello\\n";     // string, zero terminated
    let raw: u8[] = raw "abc";     // string without the terminating zero
    let buf: u8[256] = fill(7);    // {"fill": 7}
    let pic: u8[] = file("picture.bin");
    let m: map<u8, u32>[16] = {1: 100, 2: 200};   // hash map: key type, value type, capacity
    let fb: u32[1000] at 0x2000;   // fixed address

    function gcd(a: i32, b: i32): i32 {    // params, return type are optional
        let t: i32;                        // locals: only at the start of the body
        while (b != 0) { t = a % b; a = b; b = t; }
        return a;
    }

    <statements at top level>      // main program ("statements"); in a module: "init"

Types: i8 i16 i32 u8 u16 u32 i64 u64 f32 f64

Numbers: 10  0xFF  0b101  2.5  1e-3  'A' (chars: \\n \\t \\r \\0 \\\\ \\')   -5 and -0x10 are literals.
Names:   x   std.BUS_DEV_VIDEO   kb.KEY_LEFT

Expressions (precedence, low -> high):
    ||  &&  == != < <= > >= (one comparison only)  |  ^  &  << >>  + -  * / %  - ~ !
  The same operator chain becomes one n-ary node:  a + b + c  ->  {"+": [a, b, c]}
  Comparison, && || ! has() true false are *conditions*; they are only valid in if / while /
  do-while / for conditions (and inside other conditions). Everything else is an *expression*.

    arr[i]                      {"get": "arr", "at": i}        (also map lookup)
    get(m, key, default)        map lookup with default
    has(m, key)                 condition: is the key present?
    u8(e) i32(e) ...            {"cast": "u8", "of": e}
    f(a, b)                     call of a function that returns a value
    len(x) sizeof(x) addr(x) addr(arr, i)
    peek(addr, u8)              read memory
    rnd(100)   sys("tick")      sys: "tick" | "time" | "ramsize"
    in(dev, port)  in(dev, port, u32)         read a device port
    inb(dev, port, addr, len)  outb(dev, port, addr, len)       (expression: count)
    bus_status(dev)

Statements:
    x = e;   arr[i] = e;   m[k] = e;
    x += e;  x -= e;  x++;  x--;        arr[i] += e;  arr[i]++;
    delete m[k];   clear(arr);
    if (c) { } else if (c) { } else { }  (braces are always required)
    while (c) { }      do { } while (c);      loop { }
    for (i of 0 .. 10) { }               counted loop, from <= i < to
    for (i of 8 .. 0 step -1) { }
    for (i = 0, j = 9; i < j; i++, j--) { }       general loop (all three parts optional)
    switch (e) { case 1: { } case kb.KEY_UP: { } default: { } }   (no fallthrough)
    break;  break 2;  continue;  continue 2;
    f(a, b);   return;   return e;   halt;
    label name;   goto name;
    asm "movi u32 #0 {x}";    asm ["line 1", "line 2"];
    poke(addr, u8, value);    memset(addr, value, len);    memcpy(dst, src, len);
    out(dev, port, value);    inb(dev, port, addr, len);   outb(dev, port, addr, len);
    bus_reset(dev);
    video(mode: 1, fps: 60, scale: 1.0, cursor: 0);
    debug(e);   wait(us);   bell();   seed(e);

Reserved words: let const function return if else while do for of loop switch case default
    break continue halt label goto asm delete step at true false program module runtime import
    include const_type bounds_check and the type names.
The built-in names (len sizeof addr peek rnd sys get has in inb outb bus_status out poke memset
memcpy bus_reset video debug wait bell seed clear ram fill file map raw) are special only when
used like a call, e.g. 'addr(x)'; a plain variable or parameter may still be called 'addr'.
"""

import argparse
import json
import os
import re
import sys

try:
    from lark import Lark, Transformer, v_args
    from lark.exceptions import (UnexpectedCharacters, UnexpectedEOF, UnexpectedInput,
                                 UnexpectedToken, VisitError, LarkError)
except ImportError:
    sys.stderr.write("frontend.py needs the 'lark' package:  pip install lark\n")
    sys.exit(2)


GRAMMAR = r"""
start: item*

?item: directive
     | decl
     | function
     | statement

// ------------------------------------------------------------ directives
directive: "program" STRING ";"               -> d_program
         | "module" NAME ";"                  -> d_module
         | "runtime" INT ";"                  -> d_runtime
         | "import" STRING ";"                -> d_import
         | "include" STRING ";"               -> d_include
         | "ram" "(" named_args ")" ";"       -> d_ram
         | "const_type" TYPE ";"              -> d_const_type
         | "bounds_check" boolean ";"         -> d_bounds
         | "const" NAME "=" expr ";"          -> d_const

named_args: named_arg ("," named_arg)*
named_arg: NAME ":" expr
         | NAME ":" STRING                    -> named_arg_str

// ------------------------------------------------------------ declarations
decl: "let" NAME ":" typespec decl_init? decl_at? ";"
decl_init: "=" init_val
decl_at: "at" expr

typespec: TYPE                                       -> ts_scalar
        | TYPE "[" expr? "]"                         -> ts_array
        | "map" "<" TYPE "," TYPE ">" "[" expr "]"   -> ts_map

?init_val: expr                          -> init_expr
         | "[" exprlist "]"              -> init_list
         | "{" pairlist "}"              -> init_map
         | STRING                        -> init_string
         | "raw" STRING                  -> init_raw
         | "fill" "(" expr ")"           -> init_fill
         | "file" "(" STRING ")"         -> init_file

pairlist: (pair ("," pair)* ","?)?
pair: expr ":" expr

exprlist: (expr ("," expr)* ","?)?

// ------------------------------------------------------------ functions
function: "function" NAME "(" params ")" ret_type? "{" fitem* "}"
params: (param ("," param)*)?
param: NAME ":" TYPE
ret_type: ":" TYPE
?fitem: decl
      | statement

// ------------------------------------------------------------ statements
block: "{" statement* "}"

cond: expr
cond_opt: expr?

?statement: simple ";"
          | COMMENT_STMT                                              -> s_comment
          | "if" "(" cond ")" block elif_part* else_part?             -> s_if
          | "while" "(" cond ")" block                                -> s_while
          | "do" block "while" "(" cond ")" ";"                       -> s_do
          | "for" "(" NAME "of" expr ".." expr for_step? ")" block    -> s_for_range
          | "for" "(" slist ";" cond_opt ";" slist ")" block          -> s_for_c
          | "loop" block                                              -> s_loop
          | "switch" "(" expr ")" "{" case_clause* default_clause? "}" -> s_switch
          | "break" INT? ";"                                          -> s_break
          | "continue" INT? ";"                                       -> s_continue
          | "return" expr? ";"                                        -> s_return
          | "halt" ";"                                                -> s_halt
          | "label" NAME ";"                                          -> s_label
          | "goto" NAME ";"                                           -> s_goto
          | "asm" asm_arg ";"                                         -> s_asm
          | "delete" NAME "[" expr "]" ";"                            -> s_del
          | "clear" "(" NAME ")" ";"                                  -> s_clear
          | "poke" "(" expr "," TYPE "," expr ")" ";"                 -> s_poke
          | "memset" "(" expr "," expr "," expr ")" ";"               -> s_memset
          | "memcpy" "(" expr "," expr "," expr ")" ";"               -> s_memcpy
          | "out" "(" expr "," expr "," expr ")" ";"                  -> s_out
          | "inb" "(" expr "," expr "," expr "," expr ")" ";"         -> s_inb
          | "outb" "(" expr "," expr "," expr "," expr ")" ";"        -> s_outb
          | "bus_reset" "(" expr ")" ";"                              -> s_bus_reset
          | "video" "(" named_args ")" ";"                            -> s_video
          | "debug" "(" expr ")" ";"                                  -> s_debug
          | "wait" "(" expr ")" ";"                                   -> s_wait
          | "bell" "(" ")" ";"                                        -> s_bell
          | "seed" "(" expr ")" ";"                                   -> s_seed

?simple: lvalue "=" expr          -> s_set
       | lvalue "+=" expr         -> s_incby
       | lvalue "-=" expr         -> s_decby
       | lvalue "++"              -> s_inc
       | lvalue "--"              -> s_dec
       | NAME "(" exprlist ")"    -> s_call

lvalue: NAME                      -> lv_name
      | NAME "[" expr "]"         -> lv_index

slist: (simple ("," simple)*)?

for_step: "step" expr
elif_part: "else" "if" "(" cond ")" block
else_part: "else" block
case_clause: "case" expr ":" block
default_clause: "default" ":" block

asm_arg: STRING                   -> asm_str
       | "[" strlist "]"          -> asm_list
strlist: (STRING ("," STRING)* ","?)?

// ------------------------------------------------------------ expressions
?expr: or_e
?or_e: and_e
     | and_e ("||" and_e)+                -> e_or
?and_e: cmp_e
      | cmp_e ("&&" cmp_e)+               -> e_and
?cmp_e: bor
      | bor CMPOP bor                     -> e_cmp
?bor: bxor
    | bxor ("|" bxor)+                    -> e_bor
?bxor: band
     | band ("^" band)+                   -> e_bxor
?band: shift
     | shift ("&" shift)+                 -> e_band
?shift: sum
      | sum (SHIFTOP sum)+                -> e_chain
?sum: prod
    | prod (ADDOP prod)+                  -> e_chain
?prod: unary
     | unary (MULOP unary)+               -> e_chain
?unary: primary
      | "-" unary                         -> e_neg
      | "~" unary                         -> e_inv
      | "!" unary                         -> e_not

?primary: INT                             -> lit_int
        | FLOAT                           -> lit_float
        | HEX                             -> lit_str
        | BIN                             -> lit_str
        | CHAR                            -> lit_char
        | boolean
        | NAME                            -> var
        | QNAME                           -> var
        | NAME "[" expr "]"               -> index
        | NAME "(" exprlist ")"           -> call_e
        | TYPE "(" expr ")"               -> cast_e
        | "(" expr ")"
        | "get" "(" ref "," expr ")"                      -> b_get
        | "get" "(" ref "," expr "," expr ")"             -> b_get_default
        | "has" "(" ref "," expr ")"                      -> b_has
        | "len" "(" ref ")"                               -> b_len
        | "sizeof" "(" ref ")"                            -> b_sizeof
        | "addr" "(" ref ")"                              -> b_addr
        | "addr" "(" ref "," expr ")"                     -> b_addr_at
        | "peek" "(" expr "," TYPE ")"                    -> b_peek
        | "rnd" "(" expr ")"                              -> b_rnd
        | "sys" "(" STRING ")"                            -> b_sys
        | "in" "(" expr "," expr ")"                      -> b_in
        | "in" "(" expr "," expr "," TYPE ")"             -> b_in_typed
        | "inb" "(" expr "," expr "," expr "," expr ")"   -> b_inb
        | "outb" "(" expr "," expr "," expr "," expr ")"  -> b_outb
        | "bus_status" "(" expr ")"                       -> b_bus_status

?ref: NAME
    | QNAME

?boolean: TRUE                            -> lit_true
        | FALSE                           -> lit_false

// ------------------------------------------------------------ terminals
TRUE: "true"
FALSE: "false"
TYPE.2: /(?:i8|i16|i32|i64|u8|u16|u32|u64|f32|f64)(?![A-Za-z0-9_])/
QNAME.2: /[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*/
NAME: /[A-Za-z_][A-Za-z0-9_]*/
HEX.3: /0[xX][0-9a-fA-F]+/
BIN.3: /0[bB][01]+/
FLOAT.2: /(?:\d+\.\d+(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)/
INT.1: /\d+/
CHAR: /'(?:\\.|[^'\\\n])'/
STRING: /"(?:\\.|[^"\\\n])*"/
CMPOP: /==|!=|<=|>=|<(?!<)|>(?!>)/
SHIFTOP: /<<|>>/
ADDOP: /[+-]/
MULOP: /[*\/%]/

COMMENT_STMT.3: /\/\/\/[^\n]*/
LINE_COMMENT.1: /\/\/[^\n]*/
BLOCK_COMMENT.1: /\/\*[\s\S]*?\*\//
%ignore LINE_COMMENT
%ignore BLOCK_COMMENT
%import common.WS
%ignore WS
"""


# ---------------------------------------------------------------- errors
class FrontendError(Exception):
    def __init__(self, message, line=None, column=None):
        Exception.__init__(self, message)
        self.message = message
        self.line = line
        self.column = column


class Cond(dict):
    """A condition node ({"left","op","right"}, {"and"}, {"or"}, {"not"}, {"has"})."""


NUM_STR_RE = re.compile(r'^-?(0[xX][0-9a-fA-F]+|0[bB][01]+)$')
CHAR_ESCAPES = {"n": 10, "r": 13, "t": 9, "0": 0, "\\": 92, "'": 39}
STRING_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "\\": "\\", "\"": "\"", "'": "'"}


def pos(meta):
    if meta is None or getattr(meta, "empty", True):
        return None, None
    return meta.line, meta.column


def fail(message, where=None):
    """where: a lark Token, a lark Meta or None."""
    line = column = None
    if where is not None:
        if hasattr(where, "empty"):
            line, column = pos(where)
        else:
            line, column = getattr(where, "line", None), getattr(where, "column", None)
    raise FrontendError(message, line, column)


def decode_string(token):
    """STRING token (with quotes) -> Python str."""
    body = str(token)[1:-1]
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        esc = body[i]
        if esc in STRING_ESCAPES:
            out.append(STRING_ESCAPES[esc])
            i += 1
        elif esc == "x" and re.match(r"[0-9a-fA-F]{2}", body[i + 1:i + 3]):
            out.append(chr(int(body[i + 1:i + 3], 16)))
            i += 3
        elif esc == "u" and re.match(r"[0-9a-fA-F]{4}", body[i + 1:i + 5]):
            out.append(chr(int(body[i + 1:i + 5], 16)))
            i += 5
        else:
            fail("unknown escape sequence '\\%s' in string" % esc, token)
    return "".join(out)


# ---------------------------------------------------------------- tree building
class Directive(object):
    def __init__(self, key, value, line):
        self.key = key
        self.value = value
        self.line = line


class Decl(object):
    def __init__(self, data, line):
        self.data = data
        self.line = line


class FuncDef(object):
    def __init__(self, data):
        self.data = data


def is_cond(v):
    return isinstance(v, (Cond, bool))


def ex(v, where=None):
    """Value must be an expression, not a condition."""
    if is_cond(v):
        fail("a condition (comparison, &&, ||, !, has(), true/false) cannot be used as a value; "
             "conditions are only allowed in if / while / for", where)
    return v


def exs(values, where=None):
    return [ex(v, where) for v in values]


def cd(v, where=None):
    """Value must be a condition."""
    if not is_cond(v):
        fail("a condition is expected here (comparison such as 'a < b', &&, ||, !, has(), "
             "true/false)", where)
    return v


def negate(v):
    if isinstance(v, bool):
        fail("cannot negate a boolean")
    if isinstance(v, (int, float)):
        return -v
    if isinstance(v, str) and NUM_STR_RE.match(v):
        return v[1:] if v.startswith("-") else "-" + v
    return {"-": [v]}


@v_args(tree=True)
class Builder(Transformer):
    def __init__(self, module_mode, default_name):
        Transformer.__init__(self)
        self.module_mode = module_mode
        self.default_name = default_name

    # ------------------------------------------------------ whole file
    def start(self, tree):
        meta, items = tree.meta, tree.children
        singles = {}
        imports, includes, consts = [], [], {}
        globals_, functions, statements = [], [], []
        for item in items:
            if isinstance(item, Directive):
                key = item.key
                if key in ("program", "runtime", "ram", "type", "bounds_check", "module"):
                    if key == "module":
                        if not self.module_mode:
                            fail("'module' is only allowed with --module", Tok(item.line))
                    elif self.module_mode:
                        fail("'%s' is not allowed in a module (it belongs to a program)"
                             % {"type": "const_type"}.get(key, key), Tok(item.line))
                    if key in singles:
                        fail("duplicate '%s' directive" % {"type": "const_type"}.get(key, key),
                             Tok(item.line))
                    singles[key] = item.value
                elif key == "import":
                    imports.append(item.value)
                elif key == "include":
                    includes.append(item.value)
                elif key == "const":
                    name, value = item.value
                    if name in consts:
                        fail("constant '%s' is declared twice" % name, Tok(item.line))
                    consts[name] = value
            elif isinstance(item, Decl):
                globals_.append(item.data)
            elif isinstance(item, FuncDef):
                functions.append(item.data)
            else:
                statements.append(item)

        out = {}
        if self.module_mode:
            out["module"] = singles.get("module", self.default_name)
        else:
            for key in ("program", "runtime"):
                if key in singles:
                    out[key] = singles[key]
        if imports:
            out["imports"] = imports
        if includes:
            out["includes"] = includes
        if not self.module_mode:
            for key in ("ram", "type", "bounds_check"):
                if key in singles:
                    out[key] = singles[key]
        if consts:
            out["consts"] = consts
        if globals_:
            out["globals"] = globals_
        if functions:
            out["functions"] = functions
        if self.module_mode:
            if statements:
                out["init"] = statements
        else:
            out["statements"] = statements
        return out

    # ------------------------------------------------------ directives
    def d_program(self, tree):
        meta, c = tree.meta, tree.children
        return Directive("program", decode_string(c[0]), meta.line)

    def d_module(self, tree):
        meta, c = tree.meta, tree.children
        return Directive("module", str(c[0]), meta.line)

    def d_runtime(self, tree):
        meta, c = tree.meta, tree.children
        return Directive("runtime", int(c[0]), meta.line)

    def d_import(self, tree):
        meta, c = tree.meta, tree.children
        return Directive("import", decode_string(c[0]), meta.line)

    def d_include(self, tree):
        meta, c = tree.meta, tree.children
        return Directive("include", decode_string(c[0]), meta.line)

    def d_ram(self, tree):
        meta, c = tree.meta, tree.children
        return Directive("ram", dict(c[0]), meta.line)

    def d_const_type(self, tree):
        meta, c = tree.meta, tree.children
        return Directive("type", str(c[0]), meta.line)

    def d_bounds(self, tree):
        meta, c = tree.meta, tree.children
        return Directive("bounds_check", c[0], meta.line)

    def d_const(self, tree):
        meta, c = tree.meta, tree.children
        return Directive("const", (str(c[0]), ex(c[1], meta)), meta.line)

    def named_args(self, tree):
        meta, c = tree.meta, tree.children
        result = []
        for name, value in c:
            if any(name == n for n, _ in result):
                fail("duplicate argument '%s'" % name, meta)
            result.append((name, value))
        return result

    def named_arg(self, tree):
        meta, c = tree.meta, tree.children
        return (str(c[0]), ex(c[1], meta))

    def named_arg_str(self, tree):
        meta, c = tree.meta, tree.children
        return (str(c[0]), decode_string(c[1]))

    # ------------------------------------------------------ declarations
    def ts_scalar(self, tree):
        meta, c = tree.meta, tree.children
        return ("scalar", str(c[0]))

    def ts_array(self, tree):
        meta, c = tree.meta, tree.children
        return ("array", str(c[0]), ex(c[1], meta) if len(c) > 1 else None)

    def ts_map(self, tree):
        meta, c = tree.meta, tree.children
        return ("map", str(c[0]), str(c[1]), ex(c[2], meta))

    def init_expr(self, tree):
        meta, c = tree.meta, tree.children
        return ("expr", ex(c[0], meta))

    def init_list(self, tree):
        meta, c = tree.meta, tree.children
        return ("list", exs(c[0], meta))

    def init_map(self, tree):
        meta, c = tree.meta, tree.children
        return ("map", c[0])

    def init_string(self, tree):
        meta, c = tree.meta, tree.children
        return ("string", decode_string(c[0]))

    def init_raw(self, tree):
        meta, c = tree.meta, tree.children
        return ("raw", decode_string(c[0]))

    def init_fill(self, tree):
        meta, c = tree.meta, tree.children
        return ("fill", ex(c[0], meta))

    def init_file(self, tree):
        meta, c = tree.meta, tree.children
        return ("file", decode_string(c[0]))

    def pairlist(self, tree):
        meta, c = tree.meta, tree.children
        return list(c)

    def pair(self, tree):
        meta, c = tree.meta, tree.children
        return [ex(c[0], meta), ex(c[1], meta)]

    def exprlist(self, tree):
        meta, c = tree.meta, tree.children
        return list(c)

    def decl_init(self, tree):
        meta, c = tree.meta, tree.children
        return ("init", c[0], meta)

    def decl_at(self, tree):
        meta, c = tree.meta, tree.children
        return ("at", ex(c[0], meta), meta)

    def decl(self, tree):
        meta, c = tree.meta, tree.children
        name = str(c[0])
        spec = c[1]
        init = at = None
        for extra in c[2:]:
            if extra[0] == "init":
                init = extra
            else:
                at = extra
        data = {"name": name}
        kind = spec[0]
        if kind == "scalar":
            data["type"] = spec[1]
            if init is not None:
                what, value = init[1]
                if what != "expr":
                    fail("scalar '%s' needs a single value as initialiser (use '%s[]' for "
                         "arrays)" % (name, spec[1]), init[2])
                data["init"] = value
        elif kind == "array":
            data["type"] = spec[1]
            if spec[2] is not None:
                data["size"] = spec[2]
            if init is not None:
                what, value = init[1]
                if what == "list":
                    data["init"] = value
                elif what == "string":
                    data["string"] = value
                elif what == "raw":
                    data["string"] = value
                    data["terminated"] = False
                elif what == "fill":
                    data["init"] = {"fill": value}
                elif what == "file":
                    data["init"] = {"file": value}
                else:
                    fail("array '%s' needs [..], a string, fill(..) or file(..) as initialiser"
                         % name, init[2])
        else:
            data["map"] = {"key": spec[1], "value": spec[2], "capacity": spec[3]}
            if init is not None:
                what, value = init[1]
                if what != "map":
                    fail("map '%s' needs {key: value, ...} as initialiser" % name, init[2])
                data["init"] = value
        if at is not None:
            data["at"] = at[1]
        return Decl(data, meta.line)

    # ------------------------------------------------------ functions
    def param(self, tree):
        meta, c = tree.meta, tree.children
        return {"name": str(c[0]), "type": str(c[1])}

    def params(self, tree):
        meta, c = tree.meta, tree.children
        return list(c)

    def ret_type(self, tree):
        meta, c = tree.meta, tree.children
        return ("ret", str(c[0]))

    def function(self, tree):
        meta, c = tree.meta, tree.children
        name = str(c[0])
        params = c[1]
        rest = c[2:]
        returns = None
        if rest and isinstance(rest[0], tuple) and rest[0][0] == "ret":
            returns = rest[0][1]
            rest = rest[1:]
        locals_, body = [], []
        started = False
        for item in rest:
            if isinstance(item, Decl):
                if started:
                    fail("local variables must be declared at the start of the function '%s', "
                         "before the first statement" % name, Tok(item.line))
                locals_.append(item.data)
            else:
                if item.get("comment") is None or len(item) != 1:
                    started = True
                body.append(item)
        data = {"name": name}
        if params:
            data["params"] = params
        if returns:
            data["returns"] = returns
        if locals_:
            data["locals"] = locals_
        data["body"] = body
        return FuncDef(data)

    # ------------------------------------------------------ statements
    def block(self, tree):
        meta, c = tree.meta, tree.children
        return list(c)

    def cond(self, tree):
        meta, c = tree.meta, tree.children
        return cd(c[0], meta)

    def cond_opt(self, tree):
        meta, c = tree.meta, tree.children
        return cd(c[0], meta) if c else None

    def s_comment(self, tree):
        meta, c = tree.meta, tree.children
        text = str(c[0])[3:].rstrip()
        if text.startswith(" "):
            text = text[1:]
        return {"comment": text}

    def elif_part(self, tree):
        meta, c = tree.meta, tree.children
        return ("elif", {"if": c[0], "then": c[1]})

    def else_part(self, tree):
        meta, c = tree.meta, tree.children
        return ("else", c[0])

    def s_if(self, tree):
        meta, c = tree.meta, tree.children
        stmt = {"if": c[0], "then": c[1]}
        elifs = [x[1] for x in c[2:] if x[0] == "elif"]
        elses = [x[1] for x in c[2:] if x[0] == "else"]
        if elifs:
            stmt["elif"] = elifs
        if elses:
            stmt["else"] = elses[0]
        return stmt

    def s_while(self, tree):
        meta, c = tree.meta, tree.children
        return {"while": c[0], "body": c[1]}

    def s_do(self, tree):
        meta, c = tree.meta, tree.children
        return {"do": c[0], "while": c[1]}

    def for_step(self, tree):
        meta, c = tree.meta, tree.children
        return ex(c[0], meta)

    def s_for_range(self, tree):
        meta, c = tree.meta, tree.children
        spec = {"var": str(c[0]), "from": ex(c[1], meta), "to": ex(c[2], meta)}
        if len(c) == 5:
            spec["step"] = c[3]
        return {"for": spec, "body": c[-1]}

    def slist(self, tree):
        meta, c = tree.meta, tree.children
        return list(c)

    def s_for_c(self, tree):
        meta, c = tree.meta, tree.children
        init, cond, step, body = c
        spec = {}
        if init:
            spec["init"] = init
        if cond is not None:
            spec["cond"] = cond
        if step:
            spec["step"] = step
        return {"for": spec, "body": body}

    def s_loop(self, tree):
        meta, c = tree.meta, tree.children
        return {"loop": c[0]}

    def case_clause(self, tree):
        meta, c = tree.meta, tree.children
        return ("case", {"value": ex(c[0], meta), "body": c[1]})

    def default_clause(self, tree):
        meta, c = tree.meta, tree.children
        return ("default", c[0])

    def s_switch(self, tree):
        meta, c = tree.meta, tree.children
        stmt = {"switch": ex(c[0], meta),
                "cases": [x[1] for x in c[1:] if x[0] == "case"]}
        defaults = [x[1] for x in c[1:] if x[0] == "default"]
        if defaults:
            stmt["default"] = defaults[0]
        return stmt

    def s_break(self, tree):
        meta, c = tree.meta, tree.children
        return {"break": int(c[0]) if c else True}

    def s_continue(self, tree):
        meta, c = tree.meta, tree.children
        return {"continue": int(c[0]) if c else True}

    def s_return(self, tree):
        meta, c = tree.meta, tree.children
        return {"return": ex(c[0], meta) if c else True}

    def s_halt(self, tree):
        meta, c = tree.meta, tree.children
        return {"halt": True}

    def s_label(self, tree):
        meta, c = tree.meta, tree.children
        return {"label": str(c[0])}

    def s_goto(self, tree):
        meta, c = tree.meta, tree.children
        return {"goto": str(c[0])}

    def asm_str(self, tree):
        meta, c = tree.meta, tree.children
        return decode_string(c[0])

    def strlist(self, tree):
        meta, c = tree.meta, tree.children
        return [decode_string(t) for t in c]

    def asm_list(self, tree):
        meta, c = tree.meta, tree.children
        return c[0]

    def s_asm(self, tree):
        meta, c = tree.meta, tree.children
        return {"asm": c[0]}

    def s_del(self, tree):
        meta, c = tree.meta, tree.children
        return {"del": str(c[0]), "at": ex(c[1], meta)}

    def s_clear(self, tree):
        meta, c = tree.meta, tree.children
        return {"clear": str(c[0])}

    def s_poke(self, tree):
        meta, c = tree.meta, tree.children
        return {"poke": ex(c[0], meta), "type": str(c[1]), "value": ex(c[2], meta)}

    def s_memset(self, tree):
        meta, c = tree.meta, tree.children
        a, v, n = exs(c, meta)
        return {"memset": {"addr": a, "value": v, "len": n}}

    def s_memcpy(self, tree):
        meta, c = tree.meta, tree.children
        d, s, n = exs(c, meta)
        return {"memcpy": {"dst": d, "src": s, "len": n}}

    def s_out(self, tree):
        meta, c = tree.meta, tree.children
        d, p, v = exs(c, meta)
        return {"out": {"dev": d, "port": p, "value": v}}

    def s_inb(self, tree):
        meta, c = tree.meta, tree.children
        d, p, a, n = exs(c, meta)
        return {"inb": {"dev": d, "port": p, "addr": a, "len": n}}

    def s_outb(self, tree):
        meta, c = tree.meta, tree.children
        d, p, a, n = exs(c, meta)
        return {"outb": {"dev": d, "port": p, "addr": a, "len": n}}

    def s_bus_reset(self, tree):
        meta, c = tree.meta, tree.children
        return {"bus_reset": ex(c[0], meta)}

    def s_video(self, tree):
        meta, c = tree.meta, tree.children
        return {"video": dict(c[0])}

    def s_debug(self, tree):
        meta, c = tree.meta, tree.children
        return {"debug": ex(c[0], meta)}

    def s_wait(self, tree):
        meta, c = tree.meta, tree.children
        return {"wait": ex(c[0], meta)}

    def s_bell(self, tree):
        meta, c = tree.meta, tree.children
        return {"bell": True}

    def s_seed(self, tree):
        meta, c = tree.meta, tree.children
        return {"seed": ex(c[0], meta)}

    def lv_name(self, tree):
        meta, c = tree.meta, tree.children
        return (str(c[0]), None)

    def lv_index(self, tree):
        meta, c = tree.meta, tree.children
        return (str(c[0]), ex(c[1], meta))

    def _target(self, key, lv, extra):
        stmt = {key: lv[0]}
        if lv[1] is not None:
            stmt["at"] = lv[1]
        stmt.update(extra)
        return stmt

    def s_set(self, tree):
        meta, c = tree.meta, tree.children
        return self._target("set", c[0], {"to": ex(c[1], meta)})

    def s_incby(self, tree):
        meta, c = tree.meta, tree.children
        return self._target("inc", c[0], {"by": ex(c[1], meta)})

    def s_decby(self, tree):
        meta, c = tree.meta, tree.children
        return self._target("dec", c[0], {"by": ex(c[1], meta)})

    def s_inc(self, tree):
        meta, c = tree.meta, tree.children
        return self._target("inc", c[0], {})

    def s_dec(self, tree):
        meta, c = tree.meta, tree.children
        return self._target("dec", c[0], {})

    def s_call(self, tree):
        meta, c = tree.meta, tree.children
        return {"call": str(c[0]), "args": exs(c[1], meta)}

    # ------------------------------------------------------ expressions
    def lit_int(self, tree):
        meta, c = tree.meta, tree.children
        return int(c[0])

    def lit_float(self, tree):
        meta, c = tree.meta, tree.children
        return float(c[0])

    def lit_str(self, tree):
        meta, c = tree.meta, tree.children
        return str(c[0])

    def lit_char(self, tree):
        meta, c = tree.meta, tree.children
        text = str(c[0])
        body = text[1:-1]
        if body.startswith("\\") and body[1] not in CHAR_ESCAPES:
            fail("unknown escape sequence in character literal %s" % text, c[0])
        return text

    def lit_true(self, tree):
        meta, c = tree.meta, tree.children
        return True

    def lit_false(self, tree):
        meta, c = tree.meta, tree.children
        return False

    def var(self, tree):
        meta, c = tree.meta, tree.children
        return str(c[0])

    def index(self, tree):
        meta, c = tree.meta, tree.children
        return {"get": str(c[0]), "at": ex(c[1], meta)}

    def call_e(self, tree):
        meta, c = tree.meta, tree.children
        return {"call": str(c[0]), "args": exs(c[1], meta)}

    def cast_e(self, tree):
        meta, c = tree.meta, tree.children
        return {"cast": str(c[0]), "of": ex(c[1], meta)}

    def e_neg(self, tree):
        meta, c = tree.meta, tree.children
        return negate(ex(c[0], meta))

    def e_inv(self, tree):
        meta, c = tree.meta, tree.children
        return {"~": [ex(c[0], meta)]}

    def e_not(self, tree):
        meta, c = tree.meta, tree.children
        return Cond({"not": cd(c[0], meta)})

    def e_chain(self, tree):
        meta, c = tree.meta, tree.children
        """a + b + c -> one n-ary node; a + b - c -> {"-": [{"+": [a, b]}, c]}."""
        current = ex(c[0], meta)
        node = None
        last = None
        for i in range(1, len(c), 2):
            op = str(c[i])
            rhs = ex(c[i + 1], meta)
            if node is not None and op == last:
                node[op].append(rhs)
            else:
                node = {op: [current, rhs]}
                current = node
                last = op
        return current

    def _nary(self, op, c, meta):
        return {op: exs(c, meta)}

    def e_bor(self, tree):
        meta, c = tree.meta, tree.children
        return self._nary("|", c, meta)

    def e_bxor(self, tree):
        meta, c = tree.meta, tree.children
        return self._nary("^", c, meta)

    def e_band(self, tree):
        meta, c = tree.meta, tree.children
        return self._nary("&", c, meta)

    def e_cmp(self, tree):
        meta, c = tree.meta, tree.children
        return Cond({"left": ex(c[0], meta), "op": str(c[1]), "right": ex(c[2], meta)})

    def e_and(self, tree):
        meta, c = tree.meta, tree.children
        return Cond({"and": [cd(v, meta) for v in c]})

    def e_or(self, tree):
        meta, c = tree.meta, tree.children
        return Cond({"or": [cd(v, meta) for v in c]})

    def b_get(self, tree):
        meta, c = tree.meta, tree.children
        return {"get": str(c[0]), "at": ex(c[1], meta)}

    def b_get_default(self, tree):
        meta, c = tree.meta, tree.children
        return {"get": str(c[0]), "at": ex(c[1], meta), "default": ex(c[2], meta)}

    def b_has(self, tree):
        meta, c = tree.meta, tree.children
        return Cond({"has": str(c[0]), "key": ex(c[1], meta)})

    def b_len(self, tree):
        meta, c = tree.meta, tree.children
        return {"len": str(c[0])}

    def b_sizeof(self, tree):
        meta, c = tree.meta, tree.children
        return {"sizeof": str(c[0])}

    def b_addr(self, tree):
        meta, c = tree.meta, tree.children
        return {"addr": str(c[0])}

    def b_addr_at(self, tree):
        meta, c = tree.meta, tree.children
        return {"addr": str(c[0]), "at": ex(c[1], meta)}

    def b_peek(self, tree):
        meta, c = tree.meta, tree.children
        return {"peek": ex(c[0], meta), "type": str(c[1])}

    def b_rnd(self, tree):
        meta, c = tree.meta, tree.children
        return {"rnd": ex(c[0], meta)}

    def b_sys(self, tree):
        meta, c = tree.meta, tree.children
        return {"sys": decode_string(c[0])}

    def b_in(self, tree):
        meta, c = tree.meta, tree.children
        d, p = exs(c, meta)
        return {"in": {"dev": d, "port": p}}

    def b_in_typed(self, tree):
        meta, c = tree.meta, tree.children
        d, p = exs(c[:2], meta)
        return {"in": {"dev": d, "port": p}, "type": str(c[2])}

    def b_inb(self, tree):
        meta, c = tree.meta, tree.children
        d, p, a, n = exs(c, meta)
        return {"inb": {"dev": d, "port": p, "addr": a, "len": n}}

    def b_outb(self, tree):
        meta, c = tree.meta, tree.children
        d, p, a, n = exs(c, meta)
        return {"outb": {"dev": d, "port": p, "addr": a, "len": n}}

    def b_bus_status(self, tree):
        meta, c = tree.meta, tree.children
        return {"bus_status": ex(c[0], meta)}


class Tok(object):
    """Position stand-in used for errors found after parsing (only a line is known)."""

    def __init__(self, line):
        self.line = line
        self.column = None


# ---------------------------------------------------------------- driver
SYMBOLS = {
    "NAME": "identifier", "QNAME": "name", "INT": "number", "FLOAT": "number", "HEX": "number",
    "BIN": "number", "CHAR": "character", "STRING": "string", "TYPE": "type name",
    "CMPOP": "comparison operator", "SHIFTOP": "'<<' or '>>'", "ADDOP": "'+' or '-'",
    "MULOP": "'*', '/' or '%'", "COMMENT_STMT": "'///' comment", "TRUE": "'true'",
    "FALSE": "'false'", "$END": "end of file",
}


# Built-in names that are special only when they are used like a call (followed by '('), so they
# can still be used as ordinary variable names, e.g. a parameter called 'addr' or 'len'.
CALL_WORDS = ("ram", "fill", "file", "get", "has", "len", "sizeof", "addr", "peek", "rnd", "sys",
              "in", "inb", "outb", "bus_status", "out", "poke", "memset", "memcpy", "bus_reset",
              "video", "debug", "wait", "bell", "seed", "clear")


def build_grammar():
    grammar = GRAMMAR
    terminals = []
    for word in CALL_WORDS:
        grammar = grammar.replace('"%s" "("' % word, '_%s "("' % word.upper())
        terminals.append(r'_%s.3: /%s(?![A-Za-z0-9_])(?=\s*\()/' % (word.upper(), word))
    grammar = grammar.replace('"map" "<"', '_MAP "<"')
    terminals.append(r'_MAP.3: /map(?![A-Za-z0-9_])(?=\s*<)/')
    grammar = grammar.replace('"raw" STRING', '_RAW STRING')
    terminals.append(r'_RAW.3: /raw(?![A-Za-z0-9_])(?=\s*")/')
    return grammar + "\n" + "\n".join(terminals) + "\n"


def make_parser():
    return Lark(build_grammar(), parser="lalr", propagate_positions=True,
                maybe_placeholders=False)


def describe_terminals(parser, names):
    patterns = {}
    for term in parser.terminals:
        if term.pattern.type == "str":
            patterns[term.name] = "'%s'" % term.pattern.value
    out = []
    for name in sorted(names):
        text = SYMBOLS.get(name) or patterns.get(name) or name.lower()
        if text not in out:
            out.append(text)
    return out


def syntax_error(parser, exc):
    """Convert a lark parse error into a FrontendError."""
    if isinstance(exc, UnexpectedCharacters):
        char = exc.char
        message = "unexpected character %r" % char
        if char == '"':
            message = "unterminated string"
        elif char == "'":
            message = "bad character literal (use 'A' or '\\n')"
        return FrontendError(message, exc.line, exc.column)
    if isinstance(exc, UnexpectedToken):
        token = exc.token
        if token.type == "$END":
            found = "end of file"
        else:
            found = "'%s'" % token.value
        message = "unexpected %s" % found
        names = set(exc.expected or [])
        # the full "expected" set is long and noisy; closing punctuation is the useful hint
        for closing, text in (("SEMICOLON", "';'"), ("RPAR", "')'"), ("RSQB", "']'"),
                              ("RBRACE", "'}'")):
            if closing in names:
                message += ", expected " + text
                break
        else:
            expected = describe_terminals(parser, names)
            if expected:
                if len(expected) > 6:
                    expected = expected[:6] + ["..."]
                message += ", expected " + ", ".join(expected)
        return FrontendError(message, token.line, token.column)
    if isinstance(exc, UnexpectedEOF):
        return FrontendError("unexpected end of file", None, None)
    return FrontendError(str(exc), getattr(exc, "line", None), getattr(exc, "column", None))


def compile_source(text, module_mode=False, default_name="module"):
    """Source text -> AST (dict). Raises FrontendError."""
    parser = make_parser()
    try:
        tree = parser.parse(text)
    except UnexpectedInput as exc:
        raise syntax_error(parser, exc)
    except LarkError as exc:
        raise FrontendError(str(exc))
    try:
        return Builder(module_mode, default_name).transform(tree)
    except VisitError as exc:
        if isinstance(exc.orig_exc, FrontendError):
            raise exc.orig_exc
        raise


def report(path, text, err):
    lines = text.splitlines()
    where = path
    if err.line:
        where += ":%d" % err.line
        if err.column:
            where += ":%d" % err.column
    sys.stderr.write("%s: error: %s\n" % (where, err.message))
    if err.line and 1 <= err.line <= len(lines):
        sys.stderr.write("    %s\n" % lines[err.line - 1])
        if err.column:
            sys.stderr.write("    %s^\n" % (" " * (err.column - 1)))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Meteor frontend: .meteor source -> Meteor AST (JSON) for meteor.py")
    ap.add_argument("source", help="source file (.meteor)")
    ap.add_argument("-o", "--output", help="output JSON file (default: <source>.json, '-' = stdout)")
    ap.add_argument("--module", action="store_true",
                    help="generate a module AST (for 'imports') instead of a program AST")
    ap.add_argument("--compact", action="store_true", help="write the JSON on a single line")
    args = ap.parse_args(argv)

    try:
        with open(args.source, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("%s: cannot read: %s\n" % (args.source, exc))
        return 1

    default_name = os.path.splitext(os.path.basename(args.source))[0]
    try:
        ast = compile_source(text, args.module, default_name)
    except FrontendError as exc:
        report(args.source, text, exc)
        return 1

    if args.compact:
        data = json.dumps(ast, ensure_ascii=False) + "\n"
    else:
        data = json.dumps(ast, indent=2, ensure_ascii=False) + "\n"
    output = args.output or os.path.splitext(args.source)[0] + ".json"
    if output == "-":
        sys.stdout.write(data)
    else:
        try:
            with open(output, "w", encoding="utf-8") as f:
                f.write(data)
        except OSError as exc:
            sys.stderr.write("%s: cannot write: %s\n" % (output, exc))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
