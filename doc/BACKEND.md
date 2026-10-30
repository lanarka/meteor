# Meteor – AST (BSON / JSON) → Quartz assembler

Meteor is a small compiler backend for the Quartz VM. You describe a program as an AST
(variables, expressions, `if` / `while` / `for` / `do` / `switch`, functions, bus I/O) and Meteor
writes Quartz assembler. Assembling is a separate step (`qasm.py`).

    python3 meteor.py examples/03_hello_world.bson          # BSON or JSON, detected automatically -> .quartz
    python3 meteor.py examples/03_hello_world.json          # exactly the same command
    python3 meteor.py prog.bson -o out.quartz               # custom output ("-" = stdout)
    python3 meteor.py prog.bson --ext-data                  # static data -> out.data.json + %data
    python3 meteor.py prog.bson --comments                  # structure comments in the output

The full format reference is the docstring at the top of `meteor.py`.

## Input formats: BSON and JSON, auto-detected

The AST is the same tree in both formats, and there is no format switch. **Every file is detected
when it is loaded** – the root file and every imported module alike: Meteor first tries to read it
as BSON and, if that fails, parses it as JSON. Only the content matters, never the file name or
extension, so a program may freely mix formats (a BSON program importing JSON libraries and the
other way round). If a file is neither, the error lists why it is not BSON and why it is not JSON.

BSON is read and written with the **`bson` library** (`import bson`):

    pip install pymongo          # provides `bson` (bson.encode / bson.decode)

The standalone PyPI package `bson` (bson.dumps / bson.loads) works too; do not install both in one
environment, they share the module name. The library is imported only when a file has to be read
as BSON, so JSON files load without it (a BSON file without the library reports what to install).
Decoded values are normalised to plain JSON types (pymongo's `Int64` becomes `int`); BSON types
outside objects/arrays/strings/numbers/booleans/null (dates, binary, ObjectId, ...) are rejected
with a clear error.

`tools/bsontool.py` converts between the formats:

    python3 tools/bsontool.py json2bson prog.json --imports-bson   # -> prog.bson ("x.json" imports -> "x.bson")
    python3 tools/bsontool.py bson2json prog.bson                  # -> prog.json

JSON integers become BSON int32/int64, floats become doubles (so `2` and `2.0` stay different);
integers beyond int64 must be written as strings such as `"0xFFFFFFFFFFFFFFFF"`.
`examples/` ships every program and library in both formats (`.json` is the readable source).

## Static data: `store` (default) or `--ext-data`

| | default | `--ext-data` |
|---|---|---|
| how | `movi` + `store` at program start (8/4/2/1-byte pieces, zero pieces skipped) | `<output>.data.json` + `%data "<path>"` |
| files | only the `.quartz` | `.quartz` + `.data.json` (not written when there is no static data) |
| notes | self-contained, larger code | smaller code; assemble from the directory you ran Meteor in (the `%data` path is relative to the working directory) |

Both modes produce the same RAM contents (initialised variables are laid out first, from `ram.start`).

## Model

* **Registers belong to Meteor.** Every variable lives in RAM; a register only lives while one
  statement executes, so a raw `{"asm": ...}` statement may use any register between statements.
  Inside `asm` you can write `{var}` (address of a variable) and `{CONST}` (value of a constant).
* **Static data.** Initialised variables, arrays, strings and maps are written to RAM at program start
  (see above). RAM is assumed to be zero at start.
* **Variables:** scalars, arrays, strings, hash maps (fixed capacity), fixed-address regions (`"at"`).
  Variables start at `ram.start` (default `0x0100`), initialised ones first; the memory map is printed in the output header.
* **Typing is strict.** An untyped constant adapts to the other operand, two different types are an
  error (use `{"cast": ...}`; the VM has no int↔float conversion, so cast is integer-only).
* **Functions** have parameters, `returns` and static locals (so recursion is rejected). Functions that
  are not reachable from the main program are not emitted.
* **Modules:** `"imports": ["lib/text.bson"]` merges consts, globals, functions and `init` code of other
  JSON files. `"includes": ["std.quartz"]` also exposes its `%define`s as `std.NAME` constants.

## Bus (BUS) support

| What | Meteor |
|---|---|
| `busout` | `{"out": {"dev": d, "port": p, "value": v}}` |
| `busin` | `{"in": {"dev": d, "port": p}, "type": "u32"}` (expression) |
| `businb` / `busoutb` | `{"inb": {"dev","port","addr","len"}}` / `{"outb": {...}}` – statement, or expression returning the i64 count (−1 on failure) |
| `busrst` / `busstat` | `{"bus_reset": dev}` / `{"bus_status": dev}` (expression) |
| buffers | `{"addr": "arr"}`, `{"sizeof": "arr"}`, `{"len": "arr"}`, `{"peek": addr, "type": t}`, `{"poke": addr, ...}` |
| machine | `{"video": {...}}` (`videoset`), `{"seed": e}`, `{"wait": us}`, `{"sys": "tick"}`, `{"rnd": n}`, `memcpy`, `memset` |

Device and port numbers come from `std.quartz` (`std.BUS_DEV_VIDEO_TEXT`, `std.VTXT_PORT_X`, …),
key codes from `kb.quartz` (`kb.KEY_LEFT`, …).

## Examples

| File | Shows |
|---|---|
| `01_sort_search` | consts, arrays, `for`, `while`, `if/elif/else`, `break` |
| `02_map_functions` | maps (`get/set/has/del`), functions with params/returns, f32, `do-while`, `switch` |
| `03_hello_world` | text mode printing (`lib/text.json`): strings, signed numbers, hex, colours |
| `04_text_table` | cursor positioning, padded numbers, nested loops, `bus_reset` |
| `05_clock` | RTC device on the bus, redraw only when the second changes |
| `06_gfx_shapes` | pixel graphics (`lib/gfx.json`): rects, gradient, Bresenham lines, circles, clipping, `video` |
| `07_mandelbrot` | f32 maths, rows pushed to the screen with a bulk `outb` |
| `08_keyboard_dot` | keyboard polling with `inb`, `switch` on key codes, `kb.quartz` constants |
| `09_life` | Game of Life on a torus: 2-D indexing, `memcpy`, text rendering |
| `10_beep` | builds a WAV in RAM and plays it via the audio device (`bus_status`, `outb`) |

Each example exists as `NN_name.bson` (default input) and `NN_name.json` (readable source);
the `.quartz` files were generated with `meteor.py NN_name.bson`.

`lib/colors.*`, `lib/text.*` (`text_print`, `text_print_int`, `text_print_hex`, `text_goto`, …)
and `lib/gfx.*` (`gfx_pixel`, `gfx_line`, `gfx_rect`, `gfx_fill_rect`, `gfx_circle`, `gfx_blit`, …)
are ordinary Meteor modules you can read and extend.

## Limits

* No int↔float and no f32↔f64 conversion (the VM has none).
* Inserting into a full map is ignored; a missing key yields 0 (or `"default"`).
* Array indexes are only checked with `"bounds_check": true` (halts on violation); constant indexes always.
* Locals are static: no recursion. At most 16 live temporaries per expression.
