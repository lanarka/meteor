#!/usr/bin/env python3
"""
Meteor - a compiler backend that turns a JSON AST into Quartz VM assembler.

    python3 meteor.py program.bson                # BSON or JSON AST (auto-detected) -> program.quartz
    python3 meteor.py program.json                # same, the format is detected from the content
    python3 meteor.py program.bson -o out.quartz  # custom output ("-" = stdout)
    python3 meteor.py program.bson --ext-data     # static data -> out.data.json + %data
    python3 meteor.py program.bson --comments     # annotate the output with structure comments

Meteor only generates assembler text. Assemble it yourself with qasm.py (run qasm from the same
directory as Meteor when --ext-data is used: the %data path is relative to the working directory).

Input format
------------
The root AST and every imported module may be BSON or JSON. The format is detected for each file
when it is loaded, from the content only (file names and extensions are ignored): Meteor first
tries to read the file as BSON and, if that fails, parses it as JSON. The two formats describe the
same tree (objects, arrays, strings, numbers, booleans, null); this document writes examples as
JSON. BSON is read with the `bson` library (`pip install pymongo`; the standalone `bson` package
also works). The library is only needed for BSON files: without it JSON files still load.
tools/bsontool.py converts between the two formats.

Design rules
------------
* Registers are managed exclusively by Meteor. Every variable lives in RAM and a
  register is only alive while a single statement executes. Because of that, code
  in an {"asm": ...} statement may clobber any register #0..#15 between statements.
* Static data (initialised variables, arrays, maps) is written into RAM by plain
  `movi` + `store` instructions at program start (default), or - with --ext-data - placed
  in a data segment: Meteor writes <output>.data.json and emits `%data "<path>"`.
  Either way RAM is assumed to be zero at program start.

======================================================================
PROGRAM (root object)
======================================================================
{
  "program":      "Name",                        # header comment only
  "runtime":      5,                             # -> %runtime
  "imports":      ["lib/text.json"],             # merge other Meteor modules (see MODULES)
  "includes":     ["std.quartz"],                # -> %include; %define constants of the file
                                                 #    become usable in expressions as "std.NAME"
  "ram":          {"start": "0x0100", "size": 32768},  # where variables start, RAM size
  "type":         "i32",                         # type of untyped integer constants (default i32)
  "bounds_check": false,                         # true = check array indexes (halt on violation)
  "consts":       {"N": 10, "PI": 3.14159, "M": {"*": ["N", 2]}},
  "globals":      [ DECL, ... ],
  "functions":    [ {"name": "f", "params": [{"name": "a", "type": "i32"}],
                     "returns": "i32", "locals": [ DECL, ... ], "body": [ STMT, ... ]} ],
  "statements":   [ STMT, ... ]                  # main program (a halt is appended)
}

MODULES: a module is a JSON file with the keys module, imports, includes, consts, globals,
functions and init (a list of statements that run before the main program). Paths are
relative to the importing file. Names are global: duplicates are an error.

======================================================================
DECLARATIONS (DECL) - globals, locals
======================================================================
 scalar : {"name": "x", "type": "i32", "init": 5}
 array  : {"name": "a", "type": "u8", "size": 16, "init": [1, 2, 3]}
          "string": "text"          -> characters + terminating zero (size is inferred;
                                       add "terminated": false to omit the zero)
          "init": {"fill": 7}  |  {"file": "picture.bin"}
 map    : {"name": "m", "map": {"key": "u8", "value": "i32", "capacity": 16},
           "init": [[1, 100], [2, 200]]}
 fixed address (e.g. a memory region shared with a device; no init, not allocated):
          {"name": "fb", "type": "u32", "size": 1000, "at": "0x2000"}

 Types: i8 i16 i32 u8 u16 u32 i64 u64 f32 f64
 - A string "init" of a scalar is a constant expression such as "N" or "0xFF".
 - Scalar: a constant "init" is stored at program start; a non-constant "init" is an
   expression evaluated at program start. A local "init" runs on every function entry.
 - Array/map "init" is always constant and is stored once at program start.
 - A map is a fixed-capacity hash table (linear probing, integer keys). Inserting into
   a full map is ignored. A missing key yields 0 (or "default").
 - Locals are static, so functions cannot be recursive (Meteor reports it).
 - Everything not initialised is zero.

======================================================================
EXPRESSIONS (EXPR)
======================================================================
 5  2.5  "0xFF"  "0b101"  "'A'"     constants (char escapes: \\n \\t \\r \\0 \\\\ \\')
 "x"  "N"  "std.BUS_DEV_VIDEO"       variable / constant
 {"+": [a, b, ...]}   - * / % & | ^ << >>      (several args = left to right)
 {"-": [a]}  unary minus        {"~": [a]}  bitwise NOT
 {"get": "arr", "at": i}         array element; map: {"get": "m", "at": key, "default": -1}
 {"cast": "u8", "of": e}         integer <-> integer conversion (the VM has no int <-> float)
 {"call": "f", "args": [a, b]}   call a function that returns a value
 {"len": "arr"}  {"sizeof": "x"}  {"addr": "x"}  {"addr": "arr", "at": i}
 {"peek": ADDR, "type": "u8"}    read memory at an arbitrary address
 {"rnd": 100}   {"sys": "tick" | "time" | "ramsize"}
 BUS:
 {"in": {"dev": d, "port": p}, "type": "u32"}              read a device port
 {"inb":  {"dev": d, "port": p, "addr": a, "len": n}}      device -> RAM, returns i64 count
 {"outb": {"dev": d, "port": p, "addr": a, "len": n}}      RAM -> device, returns i64 count
 {"bus_status": dev}                                        1 if the device is present
 Constant expressions are folded at compile time. Types never mix implicitly: an untyped
 constant adapts to the other operand, two different types are an error (use cast).

======================================================================
CONDITIONS (COND)
======================================================================
 {"left": EXPR, "op": "<", "right": EXPR}      op: == != < <= > >=
 {"and": [..]}  {"or": [..]}  {"not": COND}    (short-circuit evaluation)
 {"has": "map", "key": EXPR}                   is the key present in the map?
 true / false

======================================================================
STATEMENTS (STMT)
======================================================================
 {"asm": "movi u32 #0 {x}"}    raw assembler (string or list). {x} = address of a variable
                               or the value of a constant. Registers are free to use.
 {"comment": "text"}
 {"set": "x", "to": EXPR}                       assignment
 {"set": "arr", "at": i, "to": EXPR}            array element / map store
 {"inc": "x", "by": EXPR}  {"dec": "arr", "at": i}       (by is optional, default 1)
 {"del": "map", "at": key}     {"clear": "array_or_map"}
 {"if": COND, "then": [..], "elif": [{"if": COND, "then": [..]}], "else": [..]}
 {"while": COND, "body": [..]}
 {"do": [..], "while": COND}
 {"for": {"init": [STMT], "cond": COND, "step": [STMT]}, "body": [..]}
 {"for": {"var": "i", "from": 0, "to": 10, "step": 1}, "body": [..]}   # from <= i < to
 {"loop": [..]}                                 infinite loop
 {"switch": EXPR, "cases": [{"value": 1, "body": [..]}], "default": [..]}   # no fallthrough
 {"break": true | n}   {"continue": true | n}   (break/continue only affect loops)
 {"call": "f", "args": [..]}    {"return": EXPR | true}    {"halt": true}
 {"label": "n"}  {"goto": "n"}
 {"poke": ADDR, "type": "u8", "value": EXPR}   write memory at an arbitrary address
 {"memset": {"addr": a, "value": v, "len": n}}  {"memcpy": {"dst": d, "src": s, "len": n}}
 BUS:
 {"out":  {"dev": d, "port": p, "value": v}}
 {"inb"|"outb": {"dev": d, "port": p, "addr": a, "len": n}}   (result discarded;
                               use the expression form to get the count)
 {"bus_reset": dev}
 Machine:
 {"video": {"mode": 1, "fps": 60, "scale": 1.0, "cursor": 0}}
 {"debug": EXPR}  {"wait": EXPR_us}  {"bell": true}  {"seed": EXPR}
"""

import argparse
import json
import math
import os
import re
import struct
import sys

IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
QIDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$')
NUM_STR_RE = re.compile(r'^-?(0[xX][0-9a-fA-F]+|0[bB][01]+)$')
CHAR_RE = re.compile(r"^'(\\.|[^'\\])'$")
INTERP_RE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_.]*)\}')
DEFINE_RE = re.compile(r'^\s*%define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(\S+)')

TYPE_SIZE = {"i8": 1, "u8": 1, "i16": 2, "u16": 2, "i32": 4, "u32": 4,
             "f32": 4, "i64": 8, "u64": 8, "f64": 8}
TYPES = tuple(sorted(TYPE_SIZE))
NONE_ADDR = "0xFFFFFFFF"          # "not found" marker used by the map helper
NREGS = 16
MASK64 = (1 << 64) - 1

# comparison holds -> jump
JUMP_IF = {"==": "jz", "!=": "jnz", "<": "jl", "<=": "jle", ">": "jg", ">=": "jge"}
# comparison does not hold -> jump
JUMP_IF_NOT = {"==": "jnz", "!=": "jz", "<": "jge", "<=": "jg", ">": "jle", ">=": "jl"}
FLIP = {"==": "==", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}
PY_CMP = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b,
          "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}

BINARY = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "mod",
          "&": "and", "|": "or", "^": "xor", "<<": "shl", ">>": "shr"}
IMM_FORM = {"+": "addi", "-": "subi"}
COMMUTATIVE = ("+", "*", "&", "|", "^")
INT_ONLY = ("&", "|", "^", "<<", ">>", "~")
OPERATORS = tuple(BINARY) + ("~",)
EXPR_HEADS = ("get", "cast", "call", "len", "sizeof", "addr", "peek", "in", "inb", "outb",
              "bus_status", "rnd", "sys")

STATEMENT_KINDS = {
    "asm": ("asm",), "comment": ("comment",),
    "set": ("set", "at", "to"), "inc": ("inc", "at", "by"), "dec": ("dec", "at", "by"),
    "del": ("del", "at"), "clear": ("clear",),
    "if": ("if", "then", "elif", "else"), "do": ("do", "while"), "while": ("while", "body"),
    "for": ("for", "body"), "loop": ("loop",),
    "switch": ("switch", "cases", "default"),
    "break": ("break",), "continue": ("continue",),
    "call": ("call", "args"), "return": ("return",), "halt": ("halt",),
    "label": ("label",), "goto": ("goto",),
    "poke": ("poke", "type", "value"), "memset": ("memset",), "memcpy": ("memcpy",),
    "out": ("out",), "inb": ("inb",), "outb": ("outb",), "bus_reset": ("bus_reset",),
    "video": ("video",), "debug": ("debug",), "wait": ("wait",), "bell": ("bell",),
    "seed": ("seed",),
}
# lookup order for the statement kind ("do" must come before "while")
KIND_ORDER = ("asm", "comment", "set", "inc", "dec", "del", "clear", "if", "do", "while",
              "for", "loop", "switch", "break", "continue", "call", "return", "halt",
              "label", "goto", "poke", "memset", "memcpy", "out", "inb", "outb", "bus_reset",
              "video", "debug", "wait", "bell", "seed")

ROOT_KEYS = ("program", "runtime", "imports", "includes", "ram", "type", "bounds_check",
             "consts", "globals", "functions", "statements")
MODULE_KEYS = ("module", "imports", "includes", "consts", "globals", "functions", "init")


class MeteorError(Exception):
    def __init__(self, path, message):
        Exception.__init__(self, "%s: %s" % (path, message))
        self.path = path
        self.message = message


class NotConst(Exception):
    """Raised while evaluating a constant expression that needs run-time code."""


def check_keys(obj, allowed, required, path):
    if not isinstance(obj, dict):
        raise MeteorError(path, "expected an object {...}")
    for key in obj:
        if key not in allowed:
            raise MeteorError(path, "unknown key '%s' (allowed: %s)" % (key, ", ".join(allowed)))
    for key in required:
        if key not in obj:
            raise MeteorError(path, "missing required key '%s'" % key)


# ---------------------------------------------------------------- types
def is_float(t):
    return t in ("f32", "f64")


def is_signed(t):
    return t.startswith("i")


def int_range(t):
    bits = TYPE_SIZE[t] * 8
    if is_signed(t):
        return -(1 << (bits - 1)), (1 << bits) - 1      # upper bound also allows hex literals
    return 0, (1 << bits) - 1


def wrap(value, t):
    """Two's complement wrap-around of an integer to type t."""
    bits = TYPE_SIZE[t] * 8
    value &= (1 << bits) - 1
    if is_signed(t) and value >> (bits - 1):
        value -= 1 << bits
    return value


def fmt_float(value):
    text = repr(float(value))
    if "e" in text or "inf" in text or "nan" in text:
        text = format(float(value), ".12f")
    return text


def pack(value, t, path):
    """Encode a constant as little-endian bytes of type t."""
    if is_float(t):
        return struct.pack("<f" if t == "f32" else "<d", float(value))
    if isinstance(value, float):
        if not value.is_integer():
            raise MeteorError(path, "float %r used with integer type %s" % (value, t))
        value = int(value)
    lo, hi = int_range(t)
    if not lo <= value <= hi:
        raise MeteorError(path, "value %r is out of range for type %s" % (value, t))
    return (value & ((1 << (TYPE_SIZE[t] * 8)) - 1)).to_bytes(TYPE_SIZE[t], "little")


def trunc_div(a, b):
    """Integer division rounding toward zero (like the VM)."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def parse_char(text):
    body = text[1:-1]
    if body.startswith("\\"):
        table = {"n": 10, "r": 13, "t": 9, "0": 0, "\\": 92, "'": 39}
        if body[1] not in table:
            raise ValueError(text)
        return table[body[1]]
    return ord(body)


class BsonUnavailable(ValueError):
    """The 'bson' library is not installed."""


def _bson_module():
    """Import the 'bson' library lazily, so JSON files load without it."""
    try:
        import bson
    except ImportError:
        raise BsonUnavailable("reading BSON needs the 'bson' library: pip install pymongo")
    return bson


def _plain(obj, path="$"):
    """Convert a decoded BSON value to plain JSON-like Python types (dict/list/str/int/float/bool/None).
    Library specific types are normalised (e.g. pymongo's Int64 -> int); anything else is rejected."""
    if obj is None or isinstance(obj, (bool, str, float)):
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, dict):
        return {str(k): _plain(v, "%s.%s" % (path, k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v, "%s[%d]" % (path, i)) for i, v in enumerate(obj)]
    raise ValueError("unsupported BSON value of type %s at %s (only objects, arrays, strings, "
                     "numbers, booleans and null are allowed)" % (type(obj).__name__, path))


def bson_loads(raw):
    """BSON bytes -> dict/list/str/int/float/bool/None, using the 'bson' library
    (pymongo's `bson.decode` or the standalone package's `bson.loads`)."""
    bson = _bson_module()
    raw = bytes(raw)
    # the libraries differ on trailing garbage: require the declared length to match the file size
    if len(raw) < 5 or struct.unpack("<i", raw[:4])[0] != len(raw):
        raise ValueError("malformed BSON (document length does not match the file size)")
    try:
        doc = bson.decode(raw) if hasattr(bson, "decode") else bson.loads(raw)
    except Exception as e:             # exception types differ between the two libraries
        raise ValueError("malformed BSON (%s)" % e)
    return _plain(doc)


def bson_dumps(obj):
    """dict -> BSON bytes, using the 'bson' library (`bson.encode` or `bson.dumps`)."""
    if not isinstance(obj, dict):
        raise ValueError("the root of a BSON file must be an object")
    bson = _bson_module()
    try:
        return bytes(bson.encode(obj) if hasattr(bson, "encode") else bson.dumps(obj))
    except OverflowError:
        raise ValueError("an integer does not fit in int64 (write large values as strings like \"0xFFFFFFFF\")")
    except Exception as e:
        raise ValueError("cannot encode as BSON (%s)" % e)


def load_ast_file(path):
    """Read an AST file; the format is detected from the content: BSON is tried first, then JSON.
    Raises OSError / ValueError."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return bson_loads(raw)
    except ValueError as bson_error:
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as json_error:
            raise ValueError("not a valid AST file: neither BSON (%s) nor JSON (%s)" % (bson_error, json_error))


class Val(object):
    """Result of an expression: a constant (kind 'const') or a register (kind 'reg')."""

    def __init__(self, kind, value=None, vtype=None, reg=None):
        self.kind = kind
        self.value = value
        self.vtype = vtype          # None = untyped constant
        self.reg = reg


class Var(object):
    def __init__(self, name, kind, vtype, count=1):
        self.name = name
        self.kind = kind            # scalar | array | map
        self.vtype = vtype          # scalar type / array element type / map value type
        self.count = count          # array length / map capacity
        self.key_t = None
        self.entry = 0              # map entry size: state byte + key + value
        self.size = 0               # total size in bytes
        self.addr = None
        self.fixed = False          # placed at an explicit address ("at")
        self.init = None            # initial bytes (written at program start)
        self.rt_init = None         # (expr, path): run-time initialiser of a scalar
        self.slots = None           # map: hidden key/found/free slots
        self.uid = 0


class Func(object):
    def __init__(self, name):
        self.name = name
        self.params = []
        self.ret_t = None
        self.ret_var = None
        self.locals = {}
        self.body = None
        self.path = ""
        self.label = "fn_" + name


class Result(object):
    """Compiler output: assembler text and (only with ext_data) the data segment description."""

    def __init__(self, quartz, data_json=None):
        self.quartz = quartz
        self.data_json = data_json


class Generator(object):
    def __init__(self, base_dir=".", out_dir=".", comments=False, root_path=None,
                 ext_data=False, data_path=None):
        self.ext_data = ext_data
        self.data_path = data_path or "program.data.json"
        self.root_path = os.path.normpath(root_path) if root_path else None
        self.base_dir = base_dir
        self.out_dir = out_dir
        self.comments = comments
        self.items = []             # ("label", name, generated) | ("code", text)
        self.used_labels = set()
        self.counter = 0
        self.uid = 0
        self.loops = []             # stack of (break_label, continue_label)
        self.regs = []              # currently allocated registers
        self.const_mode = 0
        self.default_int = "i32"
        self.bounds = False
        self.consts = {}
        self.globals = {}
        self.funcs = {}
        self.all_vars = []
        self.scope = {}
        self.cur_func = None
        self.edges = {}             # call graph: function name (None = main) -> callee names
        self.cur_maps = []          # maps used by the code being generated
        self.func_maps = {}         # function name -> maps it uses
        self.image = bytearray()
        self.ram_start = 0x0100
        self.ram_size = 32768
        # collected from the root program and its imported modules
        self.include_files = []
        self.src_consts = []        # (prefix, name, expr)
        self.src_globals = []       # (path, decl)
        self.src_funcs = []         # (path, fn)
        self.src_init = []          # (path, stmts)
        self.loaded = set()

    # ------------------------------------------------------- low level
    def new_label(self, kind):
        self.counter += 1
        return "_m%d_%s" % (self.counter, kind)

    def emit(self, text):
        if self.const_mode:
            raise NotConst()
        self.items.append(("code", text))

    def note(self, text):
        if self.comments:
            self.items.append(("code", "// " + text))

    def label(self, name, generated=True):
        self.items.append(("label", name, generated))

    def jump(self, mnemonic, label):
        self.used_labels.add(label)
        self.emit("%s %s" % (mnemonic, label))

    def render_code(self):
        lines = []
        for item in self.items:
            if item[0] == "label":
                if item[2] and item[1] not in self.used_labels:
                    continue            # generated label nobody jumps to
                lines.append("%s:" % item[1])
            else:
                lines.append("\t" + item[1])
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------- registers
    def alloc(self, path="?"):
        if self.const_mode:
            raise NotConst()
        for r in range(NREGS):
            if r not in self.regs:
                self.regs.append(r)
                return r
        raise MeteorError(path, "expression is too complex (out of registers)")

    def release(self, val):
        if val is not None and val.kind == "reg" and val.reg in self.regs:
            self.regs.remove(val.reg)

    def free_reg(self, r):
        if r in self.regs:
            self.regs.remove(r)

    def save_regs(self):
        live = list(self.regs)
        for r in live:
            self.emit("push #%d" % r)
        return live

    def restore_regs(self, live):
        for r in reversed(live):
            self.emit("pop #%d" % r)

    def call_saving(self, label):
        """Call a routine that clobbers registers: live temporaries are pushed/popped around it."""
        live = self.save_regs()
        self.jump("call", label)
        self.restore_regs(live)

    # ------------------------------------------------------- types / values
    def vtype(self, value, path):
        if value not in TYPES:
            raise MeteorError(path, "unknown type %r (allowed: %s)" % (value, " ".join(TYPES)))
        return value

    def unify(self, a, b, hint, path):
        found = []
        for t in (a.vtype, b.vtype, hint):
            if t is not None and t not in found:
                found.append(t)
        if len(found) > 1:
            raise MeteorError(path, "mismatched types %s and %s (use {\"cast\": ...})" % (found[0], found[1]))
        return found[0] if found else None

    def imm_text(self, value, t, path):
        if is_float(t):
            return fmt_float(value)
        if isinstance(value, float):
            if not value.is_integer():
                raise MeteorError(path, "float constant %r used with integer type %s" % (value, t))
            value = int(value)
        lo, hi = int_range(t)
        if not lo <= value <= hi:
            raise MeteorError(path, "constant %r is out of range for type %s" % (value, t))
        return str(value)

    def to_reg(self, v, t, path):
        """Constant -> register of type t (movi). A register is only type-checked."""
        if v.kind == "reg":
            if v.vtype != t:
                raise MeteorError(path, "mismatched types: expected %s, expression is %s (use {\"cast\": ...})" % (t, v.vtype))
            return v
        if v.vtype is not None and v.vtype != t:
            raise MeteorError(path, "mismatched types: expected %s, constant is %s" % (t, v.vtype))
        text = self.imm_text(v.value, t, path)
        r = self.alloc(path)
        self.emit("movi %s #%d %s" % (t, r, text))
        return Val("reg", vtype=t, reg=r)

    def default_type_of(self, v):
        if v.vtype:
            return v.vtype
        return "f32" if isinstance(v.value, float) else self.default_int

    def materialize(self, v, path):
        """Expression -> register; an untyped constant gets the default type."""
        if v.kind == "reg":
            return v
        return self.to_reg(v, self.default_type_of(v), path)

    def mkconst(self, value, t=None):
        if t is not None and not is_float(t) and isinstance(value, int):
            value = wrap(value, t)
        return Val("const", value=value, vtype=t)

    # ------------------------------------------------------- constants
    def const_value(self, e, path, soft=False):
        """Evaluate e at compile time. With soft=True returns None instead of raising."""
        self.const_mode += 1
        try:
            v = self.expr(e, path)
        except NotConst:
            if soft:
                return None
            raise MeteorError(path, "expression is not a constant (no variables or calls allowed)")
        finally:
            self.const_mode -= 1
        if v.kind != "const":
            if soft:
                return None
            raise MeteorError(path, "expression is not a constant")
        return v

    def const_int(self, e, path):
        v = self.const_value(e, path)
        if isinstance(v.value, float) and not v.value.is_integer():
            raise MeteorError(path, "expected an integer")
        return int(v.value)

    def cast_const(self, v, t, path):
        if v.vtype is not None and v.vtype != t:
            raise MeteorError(path, "mismatched types: expected %s, constant is %s" % (t, v.vtype))
        return v.value

    # ------------------------------------------------------- modules / includes
    def collect(self, ast, base_dir, prefix, chain, is_root):
        """Gather includes, consts, globals, functions and init code from a program or module."""
        if is_root:
            check_keys(ast, ROOT_KEYS, ("statements",), "program")
        else:
            check_keys(ast, MODULE_KEYS, (), prefix.rstrip(":"))
        for i, imp in enumerate(ast.get("imports", [])):
            ipath = "%simports[%d]" % (prefix, i)
            if not isinstance(imp, str):
                raise MeteorError(ipath, "expected a string")
            full = os.path.normpath(os.path.join(base_dir, imp))
            if full in chain:
                raise MeteorError(ipath, "circular import: %s" % " -> ".join(chain + [full]))
            if full in self.loaded:
                continue
            self.loaded.add(full)
            try:
                module = load_ast_file(full)
            except (OSError, ValueError) as e:
                raise MeteorError(ipath, "cannot read module %s: %s" % (full, e))
            self.collect(module, os.path.dirname(full), imp + ":", chain + [full], False)
        for i, inc in enumerate(ast.get("includes", [])):
            if not isinstance(inc, str):
                raise MeteorError("%sincludes[%d]" % (prefix, i), "expected a string")
            full = os.path.normpath(os.path.join(base_dir, inc))
            if full not in self.include_files:
                self.include_files.append(full)
        for name, expr in ast.get("consts", {}).items():
            self.src_consts.append((prefix, name, expr))
        for i, d in enumerate(ast.get("globals", [])):
            self.src_globals.append(("%sglobals[%d]" % (prefix, i), d))
        for i, fn in enumerate(ast.get("functions", [])):
            self.src_funcs.append(("%sfunctions[%d]" % (prefix, i), fn))
        if "init" in ast:
            self.src_init.append(("%sinit" % prefix, ast["init"]))

    def load_includes(self):
        """Read %define constants of included files; yields the %include directives."""
        for full in self.include_files:
            fid = os.path.splitext(os.path.basename(full))[0]
            try:
                with open(full, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except OSError as e:
                raise MeteorError("includes", "cannot read %s: %s" % (full, e))
            for line in lines:
                m = DEFINE_RE.match(line.split("//")[0])
                if not m:
                    continue
                raw = m.group(2)
                try:
                    if CHAR_RE.match(raw):
                        value = parse_char(raw)
                    elif "." in raw:
                        value = float(raw)
                    else:
                        value = int(raw, 0)
                except ValueError:
                    continue
                self.consts["%s.%s" % (fid, m.group(1))] = value
            yield '%%include "%s"' % os.path.relpath(full, self.out_dir).replace(os.sep, "/")

    # ------------------------------------------------------- declarations
    def new_var(self, name, kind, vtype, count=1):
        v = Var(name, kind, vtype, count)
        self.uid += 1
        v.uid = self.uid
        v.size = TYPE_SIZE[vtype] * count if kind != "map" else 0
        self.all_vars.append(v)
        return v

    def hidden(self, name, vtype):
        return self.new_var(name, "scalar", vtype)

    def declare(self, decl, scope, path, is_local):
        check_keys(decl, ("name", "type", "size", "map", "init", "at", "string", "terminated"), ("name",), path)
        name = decl["name"]
        if not isinstance(name, str) or not IDENT_RE.match(name) or name.startswith("_m"):
            raise MeteorError(path + ".name", "invalid variable name %r (prefix '_m' is reserved)" % (name,))
        if name in scope or (not is_local and name in self.consts):
            raise MeteorError(path + ".name", "'%s' is already defined" % name)

        if "map" in decl:
            spec = decl["map"]
            check_keys(spec, ("key", "value", "capacity"), ("key", "value", "capacity"), path + ".map")
            kt = self.vtype(spec["key"], path + ".map.key")
            vt = self.vtype(spec["value"], path + ".map.value")
            if is_float(kt):
                raise MeteorError(path + ".map.key", "a map key must be an integer type")
            cap = self.const_int(spec["capacity"], path + ".map.capacity")
            if cap < 1:
                raise MeteorError(path + ".map.capacity", "capacity must be >= 1")
            if "type" in decl or "size" in decl:
                raise MeteorError(path, "a map has no 'type'/'size' keys")
            var = self.new_var(name, "map", vt, cap)
            var.key_t = kt
            var.entry = 1 + TYPE_SIZE[kt] + TYPE_SIZE[vt]
            var.size = cap * var.entry
            var.slots = {"key": self.hidden("_key_" + name, kt),
                         "found": self.hidden("_found_" + name, "u32"),
                         "free": self.hidden("_free_" + name, "u32")}
            if "init" in decl:
                var.init = self.map_init(var, decl["init"], path + ".init")
        else:
            if "type" not in decl:
                raise MeteorError(path, "missing required key 'type'")
            t = self.vtype(decl["type"], path + ".type")
            if "size" in decl or "string" in decl or isinstance(decl.get("init"), (list, dict)):
                var = self.array_decl(decl, name, t, path)
            else:
                var = self.new_var(name, "scalar", t)
                if "init" in decl:
                    self.scalar_init(var, decl["init"], path + ".init", is_local)
        if "at" in decl:
            if "init" in decl:
                raise MeteorError(path, "a variable with 'at' cannot have 'init'")
            var.fixed = True
            var.addr = self.const_int(decl["at"], path + ".at")
        if var.init is not None and not any(var.init):
            var.init = None            # all zero: RAM starts zeroed anyway
        scope[name] = var
        return var

    def scalar_init(self, var, init, path, is_local):
        if is_local:
            var.rt_init = (init, path)
            return
        v = self.const_value(init, path, soft=True)
        if v is None:
            var.rt_init = (init, path)
        else:
            var.init = pack(self.cast_const(v, var.vtype, path), var.vtype, path)

    def array_decl(self, decl, name, t, path):
        init = decl.get("init")
        sz = TYPE_SIZE[t]
        values = None
        raw = None
        if "terminated" in decl and "string" not in decl:
            raise MeteorError(path, "'terminated' is only valid together with 'string'")
        if "string" in decl:
            if init is not None:
                raise MeteorError(path, "use either 'string' or 'init', not both")
            if not isinstance(decl["string"], str):
                raise MeteorError(path + ".string", "expected a string")
            values = [ord(c) for c in decl["string"]]
            if decl.get("terminated", True):
                values.append(0)
        elif isinstance(init, list):
            values = []
            for i, x in enumerate(init):
                ipath = "%s.init[%d]" % (path, i)
                values.append(self.cast_const(self.const_value(x, ipath), t, ipath))
        elif isinstance(init, dict):
            check_keys(init, ("fill", "file"), (), path + ".init")
            if "file" in init:
                fpath = os.path.join(self.base_dir, init["file"])
                try:
                    with open(fpath, "rb") as f:
                        raw = f.read()
                except OSError as e:
                    raise MeteorError(path + ".init.file", "cannot read %s: %s" % (fpath, e))
                if len(raw) % sz:
                    raise MeteorError(path + ".init.file", "file size is not a multiple of %d" % sz)
            elif "size" not in decl:
                raise MeteorError(path, "'fill' requires 'size'")
        elif init is not None:
            raise MeteorError(path + ".init", "array init must be a list or {fill|file} (for text use the 'string' key)")
        count = self.const_int(decl["size"], path + ".size") if "size" in decl else None
        have = len(values) if values is not None else (len(raw) // sz if raw is not None else 0)
        if count is None:
            count = have
        if count < 1:
            raise MeteorError(path, "an array needs at least 1 element")
        if have > count:
            raise MeteorError(path + ".init", "init has %d elements, the array only %d" % (have, count))
        var = self.new_var(name, "array", t, count)
        if values is not None:
            body = b"".join(pack(v, t, path + ".init") for v in values)
        elif raw is not None:
            body = raw
        elif isinstance(init, dict):
            fill = self.cast_const(self.const_value(init["fill"], path + ".init.fill"), t, path)
            body = pack(fill, t, path) * count
        else:
            body = b""
        var.init = body + b"\x00" * (var.size - len(body)) if body else None
        return var

    def map_init(self, var, init, path):
        """Build the initial hash table bytes (same probing as the generated find routine)."""
        if not isinstance(init, list):
            raise MeteorError(path, "expected a list of pairs [[key, value], ...]")
        table = bytearray(var.size)
        ks = TYPE_SIZE[var.key_t]
        for i, pair in enumerate(init):
            ppath = "%s[%d]" % (path, i)
            if not isinstance(pair, list) or len(pair) != 2:
                raise MeteorError(ppath, "expected a pair [key, value]")
            key = self.cast_const(self.const_value(pair[0], ppath + "[0]"), var.key_t, ppath)
            val = self.cast_const(self.const_value(pair[1], ppath + "[1]"), var.vtype, ppath)
            kb = pack(key, var.key_t, ppath)
            vb = pack(val, var.vtype, ppath)
            idx = (int(key) & MASK64) % var.count
            for _ in range(var.count):
                off = idx * var.entry
                if table[off] == 0 or table[off + 1:off + 1 + ks] == kb:
                    table[off] = 1
                    table[off + 1:off + 1 + ks] = kb
                    table[off + 1 + ks:off + var.entry] = vb
                    break
                idx = (idx + 1) % var.count
            else:
                raise MeteorError(ppath, "the map is full (capacity %d)" % var.count)
        return bytes(table)

    def layout(self):
        """Assign RAM addresses and build the initial image. Initialised variables come first so
        that the image (data segment / store sequence) is one contiguous block starting at ram.start."""
        fixed = [v for v in self.all_vars if v.fixed]
        dyn = [v for v in self.all_vars if not v.fixed and v.init is not None]
        dyn += [v for v in self.all_vars if not v.fixed and v.init is None]
        pos = self.ram_start
        for v in dyn:
            v.addr = pos
            pos += v.size
        if pos > self.ram_size:
            raise MeteorError("ram", "variables need %d B but RAM is %d B" % (pos - self.ram_start, self.ram_size))
        ranges = [(self.ram_start, pos, "Meteor variables")]
        for v in fixed:
            if v.addr + v.size > self.ram_size:
                raise MeteorError("ram", "'%s' @0x%X does not fit in RAM" % (v.name, v.addr))
            for lo, hi, who in ranges:
                if v.addr < hi and lo < v.addr + v.size:
                    raise MeteorError("ram", "'%s' @0x%X overlaps with: %s" % (v.name, v.addr, who))
            ranges.append((v.addr, v.addr + v.size, v.name))
        self.image = bytearray()
        for v in dyn:
            if v.init is None:
                break
            self.image += v.init

    def emit_image(self):
        """Write the initial RAM image with movi + store (8/4/2/1 byte pieces, zero pieces skipped)."""
        kinds = {8: "u64", 4: "u32", 2: "u16", 1: "u8"}
        image = self.image
        r = None
        count = 0
        for base in range(0, len(image), 8):
            chunk = image[base:base + 8]
            if not any(chunk):
                continue
            off = 0
            while off < len(chunk):
                size = next(s for s in (8, 4, 2, 1) if len(chunk) - off >= s)
                piece = chunk[off:off + size]
                if any(piece):
                    if r is None:
                        self.emit("// initialize static data")
                        r = self.alloc()
                    t = kinds[size]
                    self.emit("movi %s #%d %d" % (t, r, int.from_bytes(piece, "little")))
                    self.emit("store %s %d #%d" % (t, self.ram_start + base + off, r))
                    count += 1
                off += size
        if r is not None:
            self.free_reg(r)

    def memory_map(self):
        end = max([v.addr + v.size for v in self.all_vars if not v.fixed] + [self.ram_start])
        lines = ["// RAM map: variables 0x%04X..0x%04X (%d B)" % (self.ram_start, end, end - self.ram_start)]
        for v in self.all_vars:
            what = {"scalar": v.vtype, "array": "%s[%d]" % (v.vtype, v.count),
                    "map": "map<%s,%s>[%d]" % (v.key_t, v.vtype, v.count)}[v.kind]
            lines.append("//   0x%04X %-14s %s (%d B)" % (v.addr, what, v.name, v.size))
        return lines

    # ------------------------------------------------------- lookup
    def lookup(self, name, path):
        if name in self.scope:
            return self.scope[name]
        if name in self.globals:
            return self.globals[name]
        raise MeteorError(path, "unknown variable '%s'" % name)

    def var_of(self, name, path, kind=None):
        if not isinstance(name, str):
            raise MeteorError(path, "expected a variable name")
        var = self.lookup(name, path)
        if kind and var.kind not in kind:
            raise MeteorError(path, "'%s' is a %s, expected: %s" % (name, var.kind, "/".join(kind)))
        return var

    # ------------------------------------------------------- expressions
    def expr(self, e, path):
        if isinstance(e, bool):
            raise MeteorError(path, "true/false is not a value (use 0/1)")
        if isinstance(e, (int, float)):
            return Val("const", value=e)
        if isinstance(e, str):
            return self.ident(e, path)
        if isinstance(e, dict):
            head = None
            for k in e:
                if k in OPERATORS or k in EXPR_HEADS:
                    head = k
                    break
            if head is None:
                raise MeteorError(path, "unknown expression, keys: %s" % ", ".join(e.keys()))
            if head in OPERATORS:
                check_keys(e, (head,), (head,), path)
                return self.operator(head, e[head], path + "." + head)
            return getattr(self, "ex_" + head)(e, path)
        raise MeteorError(path, "invalid expression %r" % (e,))

    def ident(self, s, path):
        if CHAR_RE.match(s):
            try:
                return Val("const", value=parse_char(s))
            except (ValueError, IndexError):
                raise MeteorError(path, "invalid character literal %s" % s)
        if NUM_STR_RE.match(s):
            return Val("const", value=int(s, 0))
        if not QIDENT_RE.match(s):
            raise MeteorError(path, "invalid identifier or literal %r" % s)
        if s in self.scope or s in self.globals:
            var = self.lookup(s, path)
            if var.kind != "scalar":
                raise MeteorError(path, "'%s' is a %s - use {\"get\": \"%s\", \"at\": ...}" % (s, var.kind, s))
            if self.const_mode:
                raise NotConst()
            r = self.alloc(path)
            self.emit("load %s #%d %d" % (var.vtype, r, var.addr))
            return Val("reg", vtype=var.vtype, reg=r)
        if s in self.consts:
            return Val("const", value=self.consts[s])
        raise MeteorError(path, "unknown variable or constant '%s'" % s)

    def operator(self, op, args, path):
        if not isinstance(args, list) or not args:
            raise MeteorError(path, "expected a non-empty list of arguments")
        if op == "~":
            if len(args) != 1:
                raise MeteorError(path, "'~' takes 1 argument")
            return self.unary(op, self.expr(args[0], path + "[0]"), path)
        if op == "-" and len(args) == 1:
            return self.unary(op, self.expr(args[0], path + "[0]"), path)
        if len(args) < 2:
            raise MeteorError(path, "operator '%s' needs at least 2 arguments" % op)
        acc = self.expr(args[0], path + "[0]")
        for i in range(1, len(args)):
            nxt = self.expr(args[i], "%s[%d]" % (path, i))
            acc = self.binop(op, acc, nxt, "%s[%d]" % (path, i))
        return acc

    def unary(self, op, a, path):
        if a.kind == "const":
            if op == "~":
                if isinstance(a.value, float):
                    raise MeteorError(path, "'~' does not work on floats")
                return self.mkconst(~a.value, a.vtype)
            return self.mkconst(-a.value, a.vtype)
        t = a.vtype
        if op == "~":
            if is_float(t):
                raise MeteorError(path, "'~' does not work on floats")
            self.emit("not %s #%d" % (t, a.reg))
            return a
        z = self.alloc(path)
        self.emit("movi %s #%d %s" % (t, z, "0.0" if is_float(t) else "0"))
        self.emit("sub %s #%d #%d" % (t, z, a.reg))
        self.release(a)
        return Val("reg", vtype=t, reg=z)

    def fold(self, op, a, b, path):
        isf = isinstance(a, float) or isinstance(b, float)
        if op in INT_ONLY and isf:
            raise MeteorError(path, "operator '%s' does not work on floats" % op)
        if op in ("/", "%") and b == 0:
            raise MeteorError(path, "division by zero in a constant expression")
        if op == "+": return a + b
        if op == "-": return a - b
        if op == "*": return a * b
        if op == "/": return a / b if isf else trunc_div(a, b)
        if op == "%": return math.fmod(a, b) if isf else a - b * trunc_div(a, b)
        if op == "&": return a & b
        if op == "|": return a | b
        if op == "^": return a ^ b
        if op == "<<": return a << b
        return a >> b

    def binop(self, op, a, b, path):
        t = self.unify(a, b, None, path)
        if a.kind == "const" and b.kind == "const":
            return self.mkconst(self.fold(op, a.value, b.value, path), t)
        if op in INT_ONLY and is_float(t):
            raise MeteorError(path, "operator '%s' does not work on floats" % op)
        if a.kind == "const":
            if op in COMMUTATIVE:
                a, b = b, a
            else:
                a = self.to_reg(a, t, path)
        if b.kind == "const":
            if op in ("/", "%") and b.value == 0:
                raise MeteorError(path, "division by zero")
            if op in IMM_FORM:
                self.emit("%s %s #%d %s" % (IMM_FORM[op], t, a.reg, self.imm_text(b.value, t, path)))
                return a
            b = self.to_reg(b, t, path)
        self.emit("%s %s #%d #%d" % (BINARY[op], t, a.reg, b.reg))
        self.release(b)
        return a

    # ---- arrays / maps
    def elem_addr(self, var, at, path):
        """-> ("const", address) or ("reg", Val holding the address)."""
        iv = self.expr(at, path)
        if iv.kind == "const":
            if isinstance(iv.value, float) and not iv.value.is_integer():
                raise MeteorError(path, "an index must be an integer")
            i = int(iv.value)
            if not 0 <= i < var.count:
                raise MeteorError(path, "index %d is outside array '%s' (size %d)" % (i, var.name, var.count))
            return "const", var.addr + i * TYPE_SIZE[var.vtype]
        if is_float(iv.vtype):
            raise MeteorError(path, "an index must not be a float")
        r = iv.reg
        if self.bounds:
            ok = self.new_label("ok")
            self.emit("cmpi u32 #%d %d" % (r, var.count))
            self.jump("jl", ok)
            self.emit("halt")
            self.label(ok)
        sz = TYPE_SIZE[var.vtype]
        if sz != 1:
            t = self.alloc(path)
            self.emit("movi u32 #%d %d" % (t, sz))
            self.emit("mul u32 #%d #%d" % (r, t))
            self.free_reg(t)
        if var.addr:
            self.emit("addi u32 #%d %d" % (r, var.addr))
        return "reg", Val("reg", vtype="u32", reg=r)

    def map_lookup(self, var, key, path):
        """Stage the key and call the find routine. Afterwards the 'found' slot holds the
        entry address (or 0xFFFFFFFF) and the 'free' slot the first reusable entry."""
        k = self.to_reg(self.expr(key, path), var.key_t, path)
        self.emit("store %s %d #%d" % (var.key_t, var.slots["key"].addr, k.reg))
        self.release(k)
        if var not in self.cur_maps:
            self.cur_maps.append(var)
        self.call_saving("_m_find_%d_%s" % (var.uid, var.name))

    def ex_get(self, e, path):
        check_keys(e, ("get", "at", "default"), ("get", "at"), path)
        var = self.var_of(e["get"], path + ".get", ("array", "map"))
        if self.const_mode:
            raise NotConst()
        if var.kind == "array":
            if "default" in e:
                raise MeteorError(path, "'default' is only valid for maps")
            kind, a = self.elem_addr(var, e["at"], path + ".at")
            t = var.vtype
            if kind == "const":
                r = self.alloc(path)
                self.emit("load %s #%d %d" % (t, r, a))
                return Val("reg", vtype=t, reg=r)
            self.emit("loadr %s #%d #%d" % (t, a.reg, a.reg))
            return Val("reg", vtype=t, reg=a.reg)
        d = "0.0" if is_float(var.vtype) else "0"
        if "default" in e:
            dv = self.cast_const(self.const_value(e["default"], path + ".default"), var.vtype, path)
            d = self.imm_text(dv, var.vtype, path)
        self.map_lookup(var, e["at"], path + ".at")
        r = self.alloc(path)
        miss, done = self.new_label("miss"), self.new_label("done")
        self.emit("load u32 #%d %d" % (r, var.slots["found"].addr))
        self.emit("cmpi u32 #%d %s" % (r, NONE_ADDR))
        self.jump("jz", miss)
        self.emit("addi u32 #%d %d" % (r, 1 + TYPE_SIZE[var.key_t]))
        self.emit("loadr %s #%d #%d" % (var.vtype, r, r))
        self.jump("jmp", done)
        self.label(miss)
        self.emit("movi %s #%d %s" % (var.vtype, r, d))
        self.label(done)
        return Val("reg", vtype=var.vtype, reg=r)

    def ex_cast(self, e, path):
        check_keys(e, ("cast", "of"), ("cast", "of"), path)
        t = self.vtype(e["cast"], path + ".cast")
        v = self.expr(e["of"], path + ".of")
        if v.kind == "const":
            if is_float(t):
                return Val("const", value=float(v.value), vtype=t)
            return self.mkconst(int(v.value), t)
        if v.vtype == t:
            return v
        if is_float(t) or is_float(v.vtype):
            raise MeteorError(path, "the VM has no conversion instruction %s -> %s (cast works between integer types only)" % (v.vtype, t))
        self.emit("addi %s #%d 0" % (t, v.reg))      # re-normalises the register to type t
        return Val("reg", vtype=t, reg=v.reg)

    def ex_call(self, e, path):
        check_keys(e, ("call", "args"), ("call",), path)
        return self.call_function(e["call"], e.get("args", []), path, True)

    def ex_len(self, e, path):
        check_keys(e, ("len",), ("len",), path)
        var = self.var_of(e["len"], path + ".len", ("array", "map"))
        return Val("const", value=var.count)

    def ex_sizeof(self, e, path):
        check_keys(e, ("sizeof",), ("sizeof",), path)
        var = self.var_of(e["sizeof"], path + ".sizeof")
        return Val("const", value=var.size if var.kind != "scalar" else TYPE_SIZE[var.vtype])

    def ex_addr(self, e, path):
        check_keys(e, ("addr", "at"), ("addr",), path)
        var = self.var_of(e["addr"], path + ".addr")
        if var.addr is None:
            raise NotConst()                # addresses are unknown until layout
        if "at" not in e:
            return Val("const", value=var.addr, vtype="u32")
        if var.kind != "array":
            raise MeteorError(path, "'at' is only valid for arrays")
        kind, a = self.elem_addr(var, e["at"], path + ".at")
        if kind == "const":
            return Val("const", value=a, vtype="u32")
        return a

    def ex_peek(self, e, path):
        check_keys(e, ("peek", "type"), ("peek", "type"), path)
        t = self.vtype(e["type"], path + ".type")
        a = self.expr(e["peek"], path + ".peek")
        if a.kind == "const":
            r = self.alloc(path)
            self.emit("load %s #%d %d" % (t, r, int(a.value)))
            return Val("reg", vtype=t, reg=r)
        if is_float(a.vtype):
            raise MeteorError(path + ".peek", "an address must not be a float")
        self.emit("loadr %s #%d #%d" % (t, a.reg, a.reg))
        return Val("reg", vtype=t, reg=a.reg)

    # ---- bus expressions
    def ex_in(self, e, path):
        check_keys(e, ("in", "type"), ("in",), path)
        spec = e["in"]
        check_keys(spec, ("dev", "port"), ("dev", "port"), path + ".in")
        t = self.vtype(e.get("type", "u32"), path + ".type")
        rd = self.materialize(self.expr(spec["dev"], path + ".in.dev"), path)
        rp = self.materialize(self.expr(spec["port"], path + ".in.port"), path)
        self.emit("busin #%d #%d #%d" % (rd.reg, rp.reg, rd.reg))
        self.release(rp)
        if TYPE_SIZE[t] < 8 and not is_float(t):
            self.emit("addi %s #%d 0" % (t, rd.reg))
        return Val("reg", vtype=t, reg=rd.reg)

    def block_io(self, e, kind, path):
        """Bulk transfer. Returns a register Val (i64): bytes moved, or -1 on failure."""
        spec = e[kind]
        check_keys(spec, ("dev", "port", "addr", "len"), ("dev", "port", "addr", "len"), path + "." + kind)
        regs = [self.materialize(self.expr(spec[k], "%s.%s.%s" % (path, kind, k)), path)
                for k in ("dev", "port", "addr", "len")]
        op = "busoutb" if kind == "outb" else "businb"
        self.emit("%s #%d #%d #%d #%d #%d" % (op, regs[0].reg, regs[1].reg, regs[2].reg, regs[3].reg, regs[0].reg))
        for r in regs[1:]:
            self.release(r)
        return Val("reg", vtype="i64", reg=regs[0].reg)

    def ex_inb(self, e, path):
        check_keys(e, ("inb",), ("inb",), path)
        return self.block_io(e, "inb", path)

    def ex_outb(self, e, path):
        check_keys(e, ("outb",), ("outb",), path)
        return self.block_io(e, "outb", path)

    def ex_bus_status(self, e, path):
        check_keys(e, ("bus_status",), ("bus_status",), path)
        rd = self.materialize(self.expr(e["bus_status"], path + ".bus_status"), path)
        self.emit("busstat #%d #%d" % (rd.reg, rd.reg))
        return Val("reg", vtype="u32", reg=rd.reg)

    def ex_rnd(self, e, path):
        check_keys(e, ("rnd",), ("rnd",), path)
        m = self.const_int(e["rnd"], path + ".rnd")
        r = self.alloc(path)
        self.emit("rnd #%d %d" % (r, m))
        self.emit("addi u32 #%d 0" % r)
        return Val("reg", vtype="u32", reg=r)

    def ex_sys(self, e, path):
        check_keys(e, ("sys",), ("sys",), path)
        types = {"tick": "u32", "time": "u64", "ramsize": "u32"}
        if e["sys"] not in types:
            raise MeteorError(path + ".sys", "allowed: tick, time, ramsize")
        r = self.alloc(path)
        self.emit("%s #%d" % (e["sys"], r))
        return Val("reg", vtype=types[e["sys"]], reg=r)

    # ------------------------------------------------------- conditions
    def cond_jump(self, cond, target, sense, path):
        """Jump to `target` if COND == sense, otherwise fall through."""
        if isinstance(cond, bool):
            if cond == sense:
                self.jump("jmp", target)
            return
        if not isinstance(cond, dict):
            raise MeteorError(path, "a condition must be an object or true/false")
        if "not" in cond:
            check_keys(cond, ("not",), ("not",), path)
            self.cond_jump(cond["not"], target, not sense, path + ".not")
            return
        for name in ("and", "or"):
            if name in cond:
                check_keys(cond, (name,), (name,), path)
                parts = cond[name]
                if not isinstance(parts, list) or not parts:
                    raise MeteorError(path + "." + name, "expected a non-empty list of conditions")
                self.logic(name, parts, target, sense, path + "." + name)
                return
        if "has" in cond:
            check_keys(cond, ("has", "key"), ("has", "key"), path)
            var = self.var_of(cond["has"], path + ".has", ("map",))
            self.map_lookup(var, cond["key"], path + ".key")
            r = self.alloc(path)
            self.emit("load u32 #%d %d" % (r, var.slots["found"].addr))
            self.emit("cmpi u32 #%d %s" % (r, NONE_ADDR))
            self.free_reg(r)
            self.jump("jnz" if sense else "jz", target)
            return
        check_keys(cond, ("left", "op", "right", "type"), ("left", "op", "right"), path)
        op = cond["op"]
        if op not in JUMP_IF:
            raise MeteorError(path + ".op", "unknown operator %r (allowed: %s)" % (op, " ".join(JUMP_IF)))
        hint = self.vtype(cond["type"], path + ".type") if "type" in cond else None
        a = self.expr(cond["left"], path + ".left")
        b = self.expr(cond["right"], path + ".right")
        t = self.unify(a, b, hint, path)
        if a.kind == "const" and b.kind == "const":
            if PY_CMP[op](a.value, b.value) == sense:
                self.jump("jmp", target)
            return
        if a.kind == "const":
            a, b, op = b, a, FLIP[op]
        if b.kind == "const":
            self.emit("cmpi %s #%d %s" % (t, a.reg, self.imm_text(b.value, t, path + ".right")))
        else:
            self.emit("cmp %s #%d #%d" % (t, a.reg, b.reg))
        self.release(a)
        self.release(b)
        self.jump((JUMP_IF if sense else JUMP_IF_NOT)[op], target)

    def logic(self, name, parts, target, sense, path):
        short = (name == "or")          # value that decides the whole expression early
        last = len(parts) - 1
        if sense == short:
            for i, part in enumerate(parts):
                self.cond_jump(part, target, short, "%s[%d]" % (path, i))
        else:
            skip = self.new_label("skip")
            for i, part in enumerate(parts):
                if i < last:
                    self.cond_jump(part, skip, short, "%s[%d]" % (path, i))
                else:
                    self.cond_jump(part, target, sense, "%s[%d]" % (path, i))
            self.label(skip)

    # ------------------------------------------------------- blocks and statements
    def block(self, stmts, path):
        if not isinstance(stmts, list):
            raise MeteorError(path, "expected a list of statements [...]")
        for i, stmt in enumerate(stmts):
            self.statement(stmt, "%s[%d]" % (path, i))

    def statement(self, stmt, path):
        if not isinstance(stmt, dict):
            raise MeteorError(path, "a statement must be an object {...}")
        kind = None
        for k in KIND_ORDER:
            if k in stmt:
                kind = k
                break
        if kind is None:
            raise MeteorError(path, "unknown statement, keys: %s" % ", ".join(stmt.keys()))
        check_keys(stmt, STATEMENT_KINDS[kind], (kind,), path)
        # invariant: no register is alive between statements
        assert not self.regs, "Meteor: leaked registers %r before %s" % (self.regs, path)
        getattr(self, "st_" + kind)(stmt, path)
        assert not self.regs, "Meteor: leaked registers %r after %s" % (self.regs, path)

    def interpolate(self, line, path):
        def repl(m):
            name = m.group(1)
            if name in self.scope or name in self.globals:
                return str(self.lookup(name, path).addr)
            if name in self.consts:
                v = self.consts[name]
                return fmt_float(v) if isinstance(v, float) else str(v)
            raise MeteorError(path, "asm: unknown variable/constant '{%s}'" % name)
        return INTERP_RE.sub(repl, line)

    def st_asm(self, stmt, path):
        value = stmt["asm"]
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise MeteorError(path + ".asm", "expected a string or a list of strings")
        for entry in value:
            for line in entry.splitlines():
                if line.strip():
                    text = self.interpolate(line.strip(), path + ".asm")
                    if text.endswith(":") and " " not in text:
                        self.label(text[:-1], generated=False)
                    else:
                        self.emit(text)

    def st_comment(self, stmt, path):
        if not isinstance(stmt["comment"], str):
            raise MeteorError(path + ".comment", "expected a string")
        for line in stmt["comment"].splitlines():
            self.emit("// " + line)

    def st_label(self, stmt, path):
        name = stmt["label"]
        if not isinstance(name, str) or not IDENT_RE.match(name) or name.startswith(("_m", "fn_")):
            raise MeteorError(path + ".label", "invalid label %r (prefixes '_m' and 'fn_' are reserved)" % (name,))
        self.label(name, generated=False)

    def st_goto(self, stmt, path):
        name = stmt["goto"]
        if not isinstance(name, str) or not QIDENT_RE.match(name):
            raise MeteorError(path + ".goto", "invalid label %r" % (name,))
        self.jump("jmp", name)

    def st_halt(self, stmt, path):
        self.emit("halt")

    def st_bell(self, stmt, path):
        self.emit("bell")

    def st_debug(self, stmt, path):
        v = self.materialize(self.expr(stmt["debug"], path + ".debug"), path)
        self.emit("debug %s #%d" % (v.vtype, v.reg))
        self.release(v)

    def st_wait(self, stmt, path):
        v = self.expr(stmt["wait"], path + ".wait")
        if v.kind == "const":
            self.emit("waiti %d" % int(v.value))
        else:
            self.emit("wait #%d" % v.reg)
            self.release(v)

    def st_seed(self, stmt, path):
        v = self.materialize(self.expr(stmt["seed"], path + ".seed"), path)
        self.emit("rndseed #%d" % v.reg)
        self.release(v)

    def st_video(self, stmt, path):
        spec = stmt["video"]
        check_keys(spec, ("mode", "fps", "scale", "cursor"), ("mode",), path + ".video")
        mode = self.const_int(spec["mode"], path + ".video.mode")
        fps = self.const_int(spec.get("fps", 60), path + ".video.fps")
        scale = float(self.const_value(spec.get("scale", 1.0), path + ".video.scale").value)
        cursor = self.const_int(spec.get("cursor", 0), path + ".video.cursor")
        self.emit("videoset %d %d %s %d" % (mode, fps, fmt_float(scale), cursor))

    # ---- variables
    def store_var(self, var, at, has_at, value_expr, path):
        if var.kind == "scalar":
            if has_at:
                raise MeteorError(path, "'%s' is a scalar - 'at' is not allowed" % var.name)
            v = self.to_reg(self.expr(value_expr, path + ".to"), var.vtype, path + ".to")
            self.emit("store %s %d #%d" % (var.vtype, var.addr, v.reg))
            self.release(v)
        elif var.kind == "array":
            if not has_at:
                raise MeteorError(path, "array '%s' requires 'at'" % var.name)
            v = self.to_reg(self.expr(value_expr, path + ".to"), var.vtype, path + ".to")
            kind, a = self.elem_addr(var, at, path + ".at")
            if kind == "const":
                self.emit("store %s %d #%d" % (var.vtype, a, v.reg))
            else:
                self.emit("storer %s #%d #%d" % (var.vtype, a.reg, v.reg))
                self.release(a)
            self.release(v)
        else:
            if not has_at:
                raise MeteorError(path, "map '%s' requires 'at' (the key)" % var.name)
            v = self.to_reg(self.expr(value_expr, path + ".to"), var.vtype, path + ".to")
            self.map_lookup(var, at, path + ".at")
            s = var.slots
            ra, rt, rk = self.alloc(path), self.alloc(path), self.alloc(path)
            have, done = self.new_label("have"), self.new_label("done")
            self.emit("load u32 #%d %d" % (ra, s["found"].addr))
            self.emit("cmpi u32 #%d %s" % (ra, NONE_ADDR))
            self.jump("jnz", have)
            self.emit("load u32 #%d %d" % (ra, s["free"].addr))       # new entry
            self.emit("cmpi u32 #%d %s" % (ra, NONE_ADDR))
            self.jump("jz", done)                                     # map full: ignore
            self.emit("movi u8 #%d 1" % rt)
            self.emit("storer u8 #%d #%d" % (ra, rt))                 # state = used
            self.emit("load %s #%d %d" % (var.key_t, rk, s["key"].addr))
            self.emit("mov #%d #%d" % (rt, ra))
            self.emit("addi u32 #%d 1" % rt)
            self.emit("storer %s #%d #%d" % (var.key_t, rt, rk))      # key
            self.label(have)
            self.emit("addi u32 #%d %d" % (ra, 1 + TYPE_SIZE[var.key_t]))
            self.emit("storer %s #%d #%d" % (var.vtype, ra, v.reg))   # value
            self.label(done)
            for r in (ra, rt, rk):
                self.free_reg(r)
            self.release(v)

    def st_set(self, stmt, path):
        var = self.var_of(stmt["set"], path + ".set")
        if "to" not in stmt:
            raise MeteorError(path, "missing required key 'to'")
        self.store_var(var, stmt.get("at"), "at" in stmt, stmt["to"], path)

    def st_incdec(self, stmt, key, op, path):
        var = self.var_of(stmt[key], path + "." + key)
        if var.kind == "scalar":
            current = stmt[key]
        else:
            if "at" not in stmt:
                raise MeteorError(path, "'%s' requires 'at'" % var.name)
            current = {"get": stmt[key], "at": stmt["at"]}
        self.store_var(var, stmt.get("at"), "at" in stmt, {op: [current, stmt.get("by", 1)]}, path)

    def st_inc(self, stmt, path):
        self.st_incdec(stmt, "inc", "+", path)

    def st_dec(self, stmt, path):
        self.st_incdec(stmt, "dec", "-", path)

    def st_del(self, stmt, path):
        var = self.var_of(stmt["del"], path + ".del", ("map",))
        if "at" not in stmt:
            raise MeteorError(path, "missing required key 'at' (the key)")
        self.map_lookup(var, stmt["at"], path + ".at")
        ra, rt = self.alloc(path), self.alloc(path)
        done = self.new_label("done")
        self.emit("load u32 #%d %d" % (ra, var.slots["found"].addr))
        self.emit("cmpi u32 #%d %s" % (ra, NONE_ADDR))
        self.jump("jz", done)
        self.emit("movi u8 #%d 2" % rt)                               # state = tombstone
        self.emit("storer u8 #%d #%d" % (ra, rt))
        self.label(done)
        self.free_reg(ra)
        self.free_reg(rt)

    def st_clear(self, stmt, path):
        var = self.var_of(stmt["clear"], path + ".clear", ("array", "map"))
        self.mem_op("memset", [("addr", var.addr), ("value", 0), ("len", var.size)], path)

    def st_poke(self, stmt, path):
        if "value" not in stmt:
            raise MeteorError(path, "missing required key 'value'")
        if "type" not in stmt:
            raise MeteorError(path, "missing required key 'type'")
        t = self.vtype(stmt["type"], path + ".type")
        v = self.to_reg(self.expr(stmt["value"], path + ".value"), t, path + ".value")
        a = self.expr(stmt["poke"], path + ".poke")
        if a.kind == "const":
            self.emit("store %s %d #%d" % (t, int(a.value), v.reg))
        else:
            if is_float(a.vtype):
                raise MeteorError(path + ".poke", "an address must not be a float")
            self.emit("storer %s #%d #%d" % (t, a.reg, v.reg))
            self.release(a)
        self.release(v)

    def mem_op(self, mnemonic, fields, path):
        regs = []
        for key, value in fields:
            regs.append(self.materialize(self.expr(value, "%s.%s" % (path, key)), path))
        self.emit("%s %s" % (mnemonic, " ".join("#%d" % r.reg for r in regs)))
        for r in regs:
            self.release(r)

    def st_memset(self, stmt, path):
        spec = stmt["memset"]
        check_keys(spec, ("addr", "value", "len"), ("addr", "value", "len"), path + ".memset")
        self.mem_op("memset", [(k, spec[k]) for k in ("addr", "value", "len")], path + ".memset")

    def st_memcpy(self, stmt, path):
        spec = stmt["memcpy"]
        check_keys(spec, ("dst", "src", "len"), ("dst", "src", "len"), path + ".memcpy")
        self.mem_op("memcpy", [(k, spec[k]) for k in ("dst", "src", "len")], path + ".memcpy")

    # ---- bus statements
    def st_out(self, stmt, path):
        spec = stmt["out"]
        check_keys(spec, ("dev", "port", "value"), ("dev", "port", "value"), path + ".out")
        regs = [self.materialize(self.expr(spec[k], "%s.out.%s" % (path, k)), path) for k in ("dev", "port", "value")]
        self.emit("busout #%d #%d #%d" % tuple(r.reg for r in regs))
        for r in regs:
            self.release(r)

    def st_inb(self, stmt, path):
        self.release(self.block_io(stmt, "inb", path))

    def st_outb(self, stmt, path):
        self.release(self.block_io(stmt, "outb", path))

    def st_bus_reset(self, stmt, path):
        r = self.materialize(self.expr(stmt["bus_reset"], path + ".bus_reset"), path)
        self.emit("busrst #%d" % r.reg)
        self.release(r)

    # ---- functions
    def call_function(self, name, args, path, want):
        if not isinstance(name, str) or name not in self.funcs:
            raise MeteorError(path, "unknown function %r" % (name,))
        f = self.funcs[name]
        if not isinstance(args, list) or len(args) != len(f.params):
            raise MeteorError(path, "function '%s' takes %d argument(s)" % (name, len(f.params)))
        if want and f.ret_t is None:
            raise MeteorError(path, "function '%s' does not return a value" % name)
        # evaluate every argument first, then store them: a nested call of the same function
        # cannot overwrite already stored parameters
        vals = []
        for i, (a, p) in enumerate(zip(args, f.params)):
            apath = "%s.args[%d]" % (path, i)
            vals.append(self.to_reg(self.expr(a, apath), p.vtype, apath))
        for v, p in zip(vals, f.params):
            self.emit("store %s %d #%d" % (p.vtype, p.addr, v.reg))
        for v in vals:
            self.release(v)
        self.edges.setdefault(self.cur_func, set()).add(name)
        self.call_saving(f.label)
        if not want:
            return None
        r = self.alloc(path)
        self.emit("load %s #%d %d" % (f.ret_t, r, f.ret_var.addr))
        return Val("reg", vtype=f.ret_t, reg=r)

    def st_call(self, stmt, path):
        self.call_function(stmt["call"], stmt.get("args", []), path, False)

    def st_return(self, stmt, path):
        f = self.funcs[self.cur_func] if self.cur_func else None
        if f is None:
            raise MeteorError(path, "'return' is only allowed inside a function (use 'halt' in the main program)")
        if f.ret_t is None:
            if stmt["return"] is not True:
                raise MeteorError(path, "function '%s' returns no value - use {\"return\": true}" % f.name)
        else:
            if stmt["return"] is True:
                raise MeteorError(path, "function '%s' returns %s - an expression is required" % (f.name, f.ret_t))
            v = self.to_reg(self.expr(stmt["return"], path + ".return"), f.ret_t, path + ".return")
            self.emit("store %s %d #%d" % (f.ret_t, f.ret_var.addr, v.reg))
            self.release(v)
        self.emit("ret")

    # ---- control flow
    def levels(self, value, what, path):
        if value is True:
            value = 1
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise MeteorError(path, "'%s' expects true or an integer >= 1" % what)
        if value > len(self.loops):
            raise MeteorError(path, "'%s' outside of a loop (loop depth is %d)" % (what, len(self.loops)))
        return value

    def st_break(self, stmt, path):
        self.jump("jmp", self.loops[-self.levels(stmt["break"], "break", path + ".break")][0])

    def st_continue(self, stmt, path):
        self.jump("jmp", self.loops[-self.levels(stmt["continue"], "continue", path + ".continue")][1])

    def st_if(self, stmt, path):
        if "then" not in stmt:
            raise MeteorError(path, "missing required key 'then'")
        branches = [(stmt["if"], stmt["then"], path)]
        for i, entry in enumerate(stmt.get("elif", [])):
            epath = "%s.elif[%d]" % (path, i)
            check_keys(entry, ("if", "then"), ("if", "then"), epath)
            branches.append((entry["if"], entry["then"], epath))
        has_else = "else" in stmt
        end = self.new_label("endif")
        for i, (cond, body, bpath) in enumerate(branches):
            last = (i == len(branches) - 1) and not has_else
            nxt = end if last else self.new_label("else")
            self.note("if")
            self.cond_jump(cond, nxt, False, bpath + ".if")
            self.block(body, bpath + ".then")
            if not last:
                self.jump("jmp", end)
                self.label(nxt)
        if has_else:
            self.block(stmt["else"], path + ".else")
        self.label(end)

    def run_loop(self, init, cond, cond_path, body, body_path, step, post_test=False):
        """Common skeleton: init; top: [cond -> end]; body; cont: step; jmp top; end:"""
        top, cont, end = self.new_label("top"), self.new_label("cont"), self.new_label("end")
        if init:
            init()
        self.label(top)
        if not post_test:
            self.cond_jump(cond, end, False, cond_path)
        self.loops.append((end, cont))
        self.block(body, body_path)
        self.loops.pop()
        self.label(cont)
        if step:
            step()
        if post_test:
            self.cond_jump(cond, top, True, cond_path)
        else:
            self.jump("jmp", top)
        self.label(end)

    def st_while(self, stmt, path):
        if "body" not in stmt:
            raise MeteorError(path, "missing required key 'body'")
        self.note("while")
        self.run_loop(None, stmt["while"], path + ".while", stmt["body"], path + ".body", None)

    def st_do(self, stmt, path):
        if "while" not in stmt:
            raise MeteorError(path, "do-while: missing required key 'while'")
        self.note("do-while")
        self.run_loop(None, stmt["while"], path + ".while", stmt["do"], path + ".do", None, post_test=True)

    def st_loop(self, stmt, path):
        self.note("loop")
        self.run_loop(None, True, path, stmt["loop"], path + ".loop", None)

    def st_for(self, stmt, path):
        if "body" not in stmt:
            raise MeteorError(path, "missing required key 'body'")
        spec = stmt["for"]
        fpath = path + ".for"
        if isinstance(spec, dict) and "var" in spec:
            check_keys(spec, ("var", "from", "to", "step"), ("var", "from", "to"), fpath)
            var = self.var_of(spec["var"], fpath + ".var", ("scalar",))
            step = self.const_value(spec.get("step", 1), fpath + ".step").value
            if step == 0:
                raise MeteorError(fpath + ".step", "step must not be 0")
            cond = {"left": spec["var"], "op": "<" if step > 0 else ">", "right": spec["to"]}
            advance = {"+" if step > 0 else "-": [spec["var"], abs(step)]}
            self.note("for %s" % var.name)
            self.run_loop(lambda: self.store_var(var, None, False, spec["from"], fpath + ".from"),
                          cond, fpath, stmt["body"], path + ".body",
                          lambda: self.store_var(var, None, False, advance, fpath + ".step"))
            return
        check_keys(spec, ("init", "cond", "step"), (), fpath)
        init, step = spec.get("init", []), spec.get("step", [])
        self.note("for")
        self.run_loop(lambda: self.block(init, fpath + ".init"), spec.get("cond", True), fpath + ".cond",
                      stmt["body"], path + ".body", (lambda: self.block(step, fpath + ".step")) if step else None)

    def st_switch(self, stmt, path):
        cases = stmt.get("cases")
        if not isinstance(cases, list) or not cases:
            raise MeteorError(path + ".cases", "expected a non-empty list of cases")
        v = self.materialize(self.expr(stmt["switch"], path + ".switch"), path)
        t = v.vtype
        end = self.new_label("endsw")
        default = self.new_label("default") if "default" in stmt else end
        labels = []
        self.note("switch")
        for i, case in enumerate(cases):
            cpath = "%s.cases[%d]" % (path, i)
            check_keys(case, ("value", "body"), ("value", "body"), cpath)
            lab = self.new_label("case")
            labels.append(lab)
            cv = self.const_value(case["value"], cpath + ".value")
            self.emit("cmpi %s #%d %s" % (t, v.reg, self.imm_text(cv.value, t, cpath + ".value")))
            self.jump("jz", lab)
        self.release(v)
        self.jump("jmp", default)
        for i, case in enumerate(cases):
            self.label(labels[i])
            self.block(case["body"], "%s.cases[%d].body" % (path, i))
            self.jump("jmp", end)
        if "default" in stmt:
            self.label(default)
            self.block(stmt["default"], path + ".default")
        self.label(end)

    # ------------------------------------------------------- map helper routine
    def map_helper(self, var):
        """Emit the shared find routine of a map. In: key slot. Out: 'found' slot (entry address
        or 0xFFFFFFFF) and 'free' slot (first empty/deleted entry on the probe path)."""
        L = "_m_f%d_" % var.uid
        kt, K, E, C, B = var.key_t, var.slots["key"].addr, var.entry, var.count, var.addr
        FOUND, FREE = var.slots["found"].addr, var.slots["free"].addr
        self.note("find routine of map %s" % var.name)
        self.label("_m_find_%d_%s" % (var.uid, var.name))
        lines = [
            "load %s #0 %d" % (kt, K), "mov #1 #0", "movi u64 #2 %d" % C, "mod u64 #1 #2",   # idx = key % capacity
            "movi u32 #3 %s" % NONE_ADDR, "movi u32 #4 0",                                   # free = none, probes = 0
            L + "loop:",
            "cmpi u32 #4 %d" % C, "jge " + L + "nf",
            "mov #5 #1", "movi u32 #6 %d" % E, "mul u32 #5 #6", "addi u32 #5 %d" % B,       # entry address
            "loadr u8 #6 #5",                                                                # state
            "cmpi u32 #6 0", "jz " + L + "empty",
            "cmpi u32 #6 2", "jz " + L + "tomb",
            "mov #7 #5", "addi u32 #7 1", "loadr %s #8 #7" % kt, "cmp %s #8 #0" % kt, "jz " + L + "found",
            "jmp " + L + "next",
            L + "tomb:",
            "cmpi u32 #3 %s" % NONE_ADDR, "jnz " + L + "next", "mov #3 #5", "jmp " + L + "next",
            L + "empty:",
            "cmpi u32 #3 %s" % NONE_ADDR, "jnz " + L + "nf", "mov #3 #5", "jmp " + L + "nf",
            L + "next:",
            "inc u32 #1", "cmpi u32 #1 %d" % C, "jl " + L + "cont", "movi u32 #1 0",
            L + "cont:",
            "inc u32 #4", "jmp " + L + "loop",
            L + "found:",
            "store u32 %d #5" % FOUND, "store u32 %d #3" % FREE, "ret",
            L + "nf:",
            "movi u32 #5 %s" % NONE_ADDR, "store u32 %d #5" % FOUND, "store u32 %d #3" % FREE, "ret",
        ]
        for line in lines:
            if line.endswith(":"):
                self.label(line[:-1], generated=False)
            else:
                self.emit(line)

    # ------------------------------------------------------- whole program
    def check_recursion(self):
        state = {}

        def visit(name, chain):
            if state.get(name) == 1:
                raise MeteorError("functions", "recursion is not supported (locals are static): %s" % " -> ".join(chain + [name]))
            if state.get(name) == 2:
                return
            state[name] = 1
            for callee in sorted(self.edges.get(name, ())):
                visit(callee, chain + [name])
            state[name] = 2
        for name in sorted(self.funcs):
            visit(name, [])

    def declare_function(self, fn, path):
        check_keys(fn, ("name", "params", "returns", "locals", "body"), ("name", "body"), path)
        name = fn["name"]
        if not isinstance(name, str) or not IDENT_RE.match(name) or name.startswith("_m"):
            raise MeteorError(path + ".name", "invalid function name %r" % (name,))
        if name in self.funcs:
            raise MeteorError(path + ".name", "function '%s' is defined more than once" % name)
        f = Func(name)
        f.body, f.path = fn["body"], path
        for i, p in enumerate(fn.get("params", [])):
            ppath = "%s.params[%d]" % (path, i)
            check_keys(p, ("name", "type"), ("name", "type"), ppath)
            f.params.append(self.declare({"name": p["name"], "type": p["type"]}, f.locals, ppath, True))
        if "returns" in fn:
            f.ret_t = self.vtype(fn["returns"], path + ".returns")
            f.ret_var = self.hidden("_ret_" + name, f.ret_t)
        for i, d in enumerate(fn.get("locals", [])):
            self.declare(d, f.locals, "%s.locals[%d]" % (path, i), True)
        self.funcs[name] = f

    def program(self, ast):
        chain = [self.root_path] if self.root_path else []
        if self.root_path:
            self.loaded.add(self.root_path)
        self.collect(ast, self.base_dir, "", chain, True)
        if "type" in ast:
            self.default_int = self.vtype(ast["type"], "type")
            if is_float(self.default_int):
                raise MeteorError("type", "the default type must be an integer type")
        self.bounds = bool(ast.get("bounds_check", False))
        if "ram" in ast:
            check_keys(ast["ram"], ("start", "size"), (), "ram")
            if "start" in ast["ram"]:
                self.ram_start = self.const_int(ast["ram"]["start"], "ram.start")
            if "size" in ast["ram"]:
                self.ram_size = self.const_int(ast["ram"]["size"], "ram.size")

        directives = []
        if "runtime" in ast:
            if isinstance(ast["runtime"], bool) or not isinstance(ast["runtime"], int) or not 0 <= ast["runtime"] <= 255:
                raise MeteorError("runtime", "expected an integer 0..255")
            directives.append("%%runtime %d" % ast["runtime"])
        directives.extend(self.load_includes())

        for prefix, name, value in self.src_consts:
            if not IDENT_RE.match(name):
                raise MeteorError("%sconsts.%s" % (prefix, name), "invalid constant name")
            if name in self.consts:
                raise MeteorError("%sconsts.%s" % (prefix, name), "constant is already defined")
            self.consts[name] = self.const_value(value, "%sconsts.%s" % (prefix, name)).value
        for path, decl in self.src_globals:
            self.declare(decl, self.globals, path, False)
        for path, fn in self.src_funcs:
            self.declare_function(fn, path)
        self.layout()

        # prologue: static data, then non-constant global initialisers, then module init code
        self.scope = {}
        if not self.ext_data:
            self.emit_image()
        for var in [v for v in self.globals.values() if v.rt_init]:
            expr, ipath = var.rt_init
            self.store_var(var, None, False, expr, ipath)
        for path, stmts in self.src_init:
            self.block(stmts, path)
        self.block(ast["statements"], "statements")
        self.emit("halt")
        main_items, main_maps = self.items, self.cur_maps

        # every function is compiled (so errors are always reported), but only functions that
        # are reachable from the main program are emitted
        for f in self.funcs.values():
            self.items, self.cur_maps = [], []
            self.label(f.label, generated=False)
            self.scope = f.locals
            self.cur_func = f.name
            self.loops = []
            for v in f.locals.values():
                if v.rt_init and v.kind == "scalar" and v not in f.params:
                    self.store_var(v, None, False, v.rt_init[0], v.rt_init[1])
            self.block(f.body, f.path + ".body")
            self.emit("ret")
            f.items = self.items
            self.func_maps[f.name] = self.cur_maps
        self.scope, self.cur_func = {}, None
        self.check_recursion()

        reachable, todo = set(), sorted(self.edges.get(None, ()))
        while todo:
            name = todo.pop()
            if name not in reachable:
                reachable.add(name)
                todo.extend(sorted(self.edges.get(name, ())))
        self.items = main_items
        maps = list(main_maps)
        for f in self.funcs.values():
            if f.name in reachable:
                self.items.extend(f.items)
                maps.extend(m for m in self.func_maps[f.name] if m not in maps)
        for var in maps:
            self.map_helper(var)

        data_json = None
        if self.ext_data and self.image:
            directives.append('%%data "%s"' % self.data_path.replace(os.sep, "/"))
            data_json = json.dumps({"start": "0x%04X" % self.ram_start,
                                    "blobs": [{"meteor": {"from_bytes": list(self.image)}}]})
        header = []
        if "program" in ast:
            header.append("// Program: %s" % ast["program"])
        header.append("// Generated by Meteor - do not edit by hand")
        header.extend(self.memory_map())
        return Result("\n".join(header + directives) + "\n\n" + self.render_code(), data_json)


def compile_ast(ast, base_dir=".", out_dir=".", comments=False, root_path=None,
                ext_data=False, data_path=None):
    """Compile an AST (already decoded) to a Result (.quartz text, .data_json or None).
    base_dir: directory used to resolve imports/includes; out_dir: directory of the output file
    (%include paths are written relative to it); root_path: path of the root file (cycle detection);
    ext_data/data_path: use a %data segment and the path written into the %data directive."""
    if not isinstance(ast, dict):
        raise MeteorError("program", "the AST root must be an object")
    return Generator(base_dir, out_dir, comments, root_path, ext_data, data_path).program(ast)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Meteor: AST (BSON or JSON, auto-detected) -> Quartz assembler")
    ap.add_argument("input", help="input AST (BSON or JSON, detected from the content)")
    ap.add_argument("-o", "--output", help="output .quartz (default: <input>.quartz, '-' = stdout)")
    ap.add_argument("--ext-data", action="store_true",
                    help="put static data into <output>.data.json (+ %%data) instead of store instructions")
    ap.add_argument("--comments", action="store_true", help="annotate the output with structure comments")
    args = ap.parse_args(argv)

    try:
        ast = load_ast_file(args.input)
    except (OSError, ValueError) as e:
        sys.stderr.write("Meteor: cannot read %s: %s\n" % (args.input, e))
        return 2

    out = args.output or os.path.splitext(args.input)[0] + ".quartz"
    out_dir = os.path.dirname(os.path.abspath(out)) if out != "-" else os.getcwd()
    data_file = (os.path.splitext(args.input)[0] if out == "-" else os.path.splitext(out)[0]) + ".data.json"
    try:
        res = compile_ast(ast, os.path.dirname(os.path.abspath(args.input)), out_dir, args.comments,
                          os.path.abspath(args.input), args.ext_data, os.path.relpath(data_file))
    except MeteorError as e:
        sys.stderr.write("Meteor error in %s\n" % e)
        return 1

    if res.data_json:
        with open(data_file, "w", encoding="utf-8") as f:
            f.write(res.data_json)
        sys.stderr.write("Meteor: %s\n" % data_file)
    if out == "-":
        sys.stdout.write(res.quartz)
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(res.quartz)
        sys.stderr.write("Meteor: %s\n" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
