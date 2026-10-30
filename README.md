# Meteor

**Meteor** is a small, statically typed, JavaScript-flavoured programming language that compiles to
assembler for the **Quartz VM** - a 32-bit register virtual machine with a text terminal, a pixel
framebuffer, keyboard, mouse, sound, a real time clock, sector disks and a network card.

```js
program "Hello";
import "../lib/text.obj";
import "../lib/sys.obj";

let msg: u8[] = "Hello, World!\n";

text_clear();
text_color(COLOR_YELLOW, COLOR_BLACK);
text_print(addr(msg));
sys_idle();
```

This repository is a proof of concept of the whole tool chain and of a standard library:

| directory | contents |
|---|---|
| `meteor/` | the compiler: `frontend.py` (source -> AST), `backend.py` (AST -> Quartz assembler), `bsontool.py` |
| `inc/` | `std.quartz` (device / port constants) and `kb.quartz` (key codes) |
| `lib/` | the **Meteor standard library** - 19 modules, see [`lib/README.md`](lib/README.md) |
| `examples/` | example programs, all of them built on the library |
| `calc/` | a bigger project: a desktop with a calculator (a module + a program) |
| `tools/` | the headless VM `qhead`, `fstool.py` for disk images, test and build helpers |
| `doc/BACKEND.md` | the JSON AST that the backend reads (the format between the two compiler stages) |

Contents: [Quick start](#quick-start) - [The machine](#the-quartz-vm-in-five-minutes) -
[The language](#the-language) - [Modules and the library](#modules-and-the-standard-library) -
[Disks, volumes and files](#disks-volumes-and-files) - [Tool chain](#the-tool-chain) - [Examples](#examples) - [Testing](#testing) - [Known limits](#things-to-know)

---

## Quick start

Requirements: Python 3, `pip install lark pymongo` (the parser and the BSON reader). To *see* programs you
need the Quartz VM binary `./quartz`, built from `quartz-src/` (it uses raylib). Without it you can still run
everything in the **headless VM** (`tools/qhead`, only needs `gcc`).

```sh
./build-modules              # compile lib/*.meteor to lib/*.obj - once, and after every change of the library
./run hello_world            # compile examples/hello_world.meteor and start it
./run snake --videomode 1    # extra options go to the quartz binary
./calc-scenario2             # the calculator
```


## The Quartz VM in five minutes

* **Frames.** The host runs the VM for at most **200 000 instructions per frame**, or until the program
  executes a *vsync* (`sys_vsync()`). A program that draws its picture and then calls `sys_vsync()` runs at
  the screen's refresh rate (60 fps). A frame that needs more than 200 000 instructions is shown unfinished
  and continues in the next frame - keep per-frame work small (every Meteor variable lives in RAM, so an
  innocent looking loop costs tens of instructions per iteration).
* **Screen.** Two layers: a **text terminal** below and **pixel graphics** above it; transparent pixels
  (alpha 0) show the text. Video modes: 1 = 640 x 480 (80 x 30 characters), 2 = 1024 x 768 (128 x 48),
  3 = 1280 x 960 (160 x 60). Colours are `0xAABBGGRR`.
* **Devices** live on a bus and are used with `in` / `out` (one port) and `inb` / `outb` (a block of RAM):
  video 0, text video 1, keyboard 2, mouse 3, audio 4, disk 5 and 6, network 7, rtc 8. The constants are in
  `inc/std.quartz` (`std.BUS_DEV_VIDEO`, `std.VIDEO_CMD_PIX`, ...), key codes in `inc/kb.quartz`
  (`kb.KEY_LEFT`). The library wraps all of this - most programs never touch a port.
* **No int <-> float conversion** instruction and **no recursion** (see the language rules below). Fixed
  point arithmetic is the natural way to compute: `lib/math` has a sine table, and `math_itof` / `math_ftoi`
  convert with a bit trick.
* **RAM** is zeroed at start; the default size of a program's RAM is 32 KB (`ram(size: ...)` changes it).

## The language

### A program, in one look

```js
program "Primes";                 // the title (a comment in the generated assembler)
import "../lib/text.obj";         // a library module
include "../inc/std.quartz";      // assembler constants: std.BUS_DEV_VIDEO, ...
ram(size: 65536);                 // optional: RAM size (and where the variables start)

const LIMIT = 100;                // a compile time constant

let composite: u8[LIMIT + 1];     // global variable (an array of zeros)
let found: u32;

function is_prime(n: u32): u32 {  // parameters and the return type are typed
    let i: u32;                   // locals come first in the body
    if (n < 2) { return 0; }
    for (i of 2 .. n) {
        if (i * i > n) { break; }
        if (n % i == 0) { return 0; }
    }
    return 1;
}

for (found of 0 .. 10) {          // statements outside of functions are the main program
    if (is_prime(found) == 1) { text_print_uint(found); text_putc(32); }
}
```

A source file has directives (`program`, `import`, `include`, `ram`, `const`, ...), declarations (`const`, `let`,
`function`) and statements, in any order. The statements outside functions run top to bottom as the main
program; when they end the VM halts.

### Comments

```js
// a line comment        /* a block comment */
/// a documentation comment: becomes a comment statement in the output, and the library reference is built from them
```

### Types

| type | meaning |
|---|---|
| `i8 i16 i32 i64` | signed integers (8 / 16 / 32 / 64 bits) |
| `u8 u16 u32 u64` | unsigned integers |
| `f32 f64` | IEEE floats |

Integer arithmetic wraps around (`u8`: 250 + 10 = 4, `u32`: 4294967295 + 1 = 0). Division truncates towards zero
(`-7 / 2 = -3`), `%` takes the sign of the dividend (`-7 % 3 = -1`), `>>` is arithmetic for signed types and
logical for unsigned. Dividing by zero stops the VM with a fault.

**Types never mix implicitly.** `a + b` needs two operands of the same type; a plain number literal adapts to
the other operand (`x + 1`), but `u32 + i32` is a compile error. Convert explicitly with a cast, which looks
like a call of the type name: `i32(x)`, `u8(n)`, `u32(c)`. A cast changes between *integer* types (it truncates
or extends); there is **no** instruction to convert between integers and floats (`math_itof` / `math_ftoi` in
the library do it with bit tricks). The type of untyped literals defaults to `i32` (`const_type u32;` changes it).

### Literals and constants

```js
42   0xFF   0b1010   -5   2.5   1e-3   'A'   '\n'     // chars: \n \t \r \0 \\ \'
const N = 8;           const MASK = N * 2 - 1;         // constants are folded at compile time
```

Strings exist only as initialisers of byte arrays, never as expressions:

```js
let msg: u8[] = "Hello\n";        // the characters plus a terminating zero
let tag: u8[] = raw "abc";        // without the terminating zero
let name: u8[] = "Meteor";        // pass it to a function as its address:  text_print(addr(name))
```

### Variables

```js
let a: i32;                           // scalar, zero at the start
let b: u8 = 'A';                      // with an initial value
let data: i32[8] = [5, 3, 9];         // array; the rest is zero. `i32[]` takes the size from the initialiser
let buf: u8[256] = fill(7);           // every element 7
let pic: u32[] = file("img.raw");     // the contents of a file at compile time
let hits: map<u8, u32>[16];           // hash map: key type, value type, capacity 16
let fb: u32[1000] at 0x2000;          // an array at a fixed RAM address (it is not allocated)
```

* **Every variable lives in RAM**, at a fixed address chosen by the compiler. Locals are **static** too
  (one copy per function), so a function **cannot be recursive** (the compiler reports it).
* Locals are declared at the *start* of a function body, before the first statement.
* Arrays are indexed from 0: `data[i]`, `data[i] = 5`, `data[i] += 2`. `len(data)` is the number of elements,
  `sizeof(data)` the size in bytes. `bounds_check true;` makes the program halt on an index out of range.
* A map is a fixed-capacity hash table with integer keys: `hits[k] = 3`, `get(hits, k, default)`, `has(hits, k)`
  (a condition), `delete hits[k]`, `clear(hits)`. A missing key reads as 0; inserting into a full map is ignored.
* `addr(x)` is the RAM address of a variable (a `u32`), `addr(arr, i)` of an element. Memory can be accessed
  anywhere with `peek(address, u8)` and `poke(address, u8, value)`.

### Expressions

Precedence, from the lowest to the highest:

```
||   &&   == != < <= > >= (one comparison only)   |   ^   &   << >>   + -   * / %   unary - ~ !
```

Note that `&`, `|`, `^` bind **tighter than comparison** (unlike C): `if (flags & 4 != 0)` means
`(flags & 4) != 0`.

Comparisons, `&&`, `||`, `!`, `has(...)`, `true` and `false` are **conditions**, not values: they are allowed
in `if`, `while`, `do-while` and `for` conditions only. There are no boolean variables and no `?:`; a function
that answers yes / no returns `1` / `0` (`u32`) and is used as `if (f(x) == 1)`.

Other expressions: `f(a, b)` (call of a function with a result), `len(x)`, `sizeof(x)`, `addr(x)`, `peek(a, T)`,
`T(e)` casts, `get(m, k, d)`, `rnd(N)` (a random number 0 .. N-1, **N must be a constant**), `sys("tick")`,
`sys("time")`, `sys("ramsize")`, and the bus reads `in(dev, port)` / `in(dev, port, u64)`,
`inb(dev, port, addr, len)`, `outb(...)` (the byte count) and `bus_status(dev)`.

### Statements

```js
x = e;   a[i] = e;   m[k] = e;   x += e;   x -= e;   x++;   x--;
if (c) { ... } else if (c) { ... } else { ... }            // the braces are required
while (c) { ... }     do { ... } while (c);     loop { ... }        // loop = forever
for (i of 0 .. 10) { ... }                  // 0 <= i < 10 (the variable must be declared)
for (i of 8 .. 0 step -1) { ... }           // the step has to be a constant
for (i = 0, j = 9; i < j; i++, j--) { ... } // the general form
switch (e) { case 1: { ... } case kb.KEY_UP: { ... } default: { ... } }   // no fall through
break;   break 2;   continue;   continue 2;     // 2 = leave / continue the two innermost loops
f(a, b);   return;   return e;   halt;
label name;   goto name;
```

Machine statements: `poke`, `memset(addr, value, len)`, `memcpy(dst, src, len)`, `out(dev, port, value)`,
`inb(dev, port, addr, len)`, `outb(...)`, `bus_reset(dev)`, `debug(e)` (prints a value on the host console),
`wait(us)`, `bell()`, `seed(e)`, and `video(mode: 1, fps: 60, scale: 1.0, cursor: 1)` (all arguments must be
constants - `sys_video(mode)` in the library switches between them at run time).

`asm "movi u32 #0 {x}";` / `asm ["line 1", "line 2"];` inserts raw Quartz assembler; `{x}` is the address of a
variable (or the value of a constant), and registers `#0 .. #15` are free to use between statements.

### Functions

```js
function gcd(a: u32, b: u32): u32 {
    let t: u32;
    while (b != 0) { t = a % b; a = b; b = t; }
    return a;
}
```

Parameters are passed by value; arrays and strings are passed as their address (`u32`) and read with `peek`
(or in a library function such as `str_len`). A function that returns nothing is called as a statement. Functions
may be used before they are defined.

### Reserved words

`let const function return if else while do for of loop switch case default break continue halt label goto asm
delete step at true false program module runtime import include const_type bounds_check` and the type names.
(`label`, `step` and `at` are easy to trip over as variable names.) The built-in names `len sizeof addr peek rnd sys
get has in inb outb ...` are special only when used like a call.

### Names and the unit of compilation

Names from an `include`d assembler file are written with the file's name: `std.BUS_DEV_VIDEO`, `kb.KEY_SPACE`.
Everything else - constants, variables, functions - is in **one global namespace**; a duplicate name is an
error. That is why every library name starts with its module name (`gfx_fill_rect`, `kbd_down`).

## Modules and the standard library

A **module** is a `.meteor` file that starts with `module name;`. It may declare constants, variables and
functions, and its top-level statements run *before* the main program (initialisation). It is compiled with
`--module` into an `.obj` file and pulled in by `import`:

```js
import "../lib/gfx.obj";        // the path is relative to the .obj file that imports it
```

Imports are transitive and de-duplicated: a program that imports `tui` can call `text_*`, `math_*` and
`color_*` functions as well, and a module that two libraries import is included once. Only the functions
that are called end up in the program.

> **Where import paths start.** The compiler resolves `import` and `include` relative to the compiled **`.obj`**
> file, not to the source. That is why programs are compiled into `dist/` (a sibling of `lib/` and `inc/`);
> `./run`, `tools/mbuild` and the tests do that for you. `calc/` is built in `calc/`, next to its own module.

### The standard library

| module | what it gives you |
|---|---|
| `colors` | colour constants, `color_rgb`, `color_mix`, `color_hue` (a rainbow), alpha helpers |
| `sys` | `sys_vsync`, `sys_idle` (keep the window alive), `sys_video`, timers, device presence, `sys_reboot` |
| `math` | `abs min max clamp wrap map gcd pow isqrt`, fixed point `math_sin` / `math_cos`, random numbers, `math_itof` / `math_ftoi` |
| `str` | zero terminated strings: `str_len cmp copy cat find upper lower`, `str_itoa`, `str_atoi`, hex |
| `rtc` | date and time, day / month names, `rtc_second_changed` |
| `input` | the keyboard (`kbd_down`, `kbd_pressed`, `kbd_char` with SHIFT / CAPS LOCK / key repeat) and the mouse |
| `text` | text terminal: cursor, colours, number formatting, `text_put` |
| `tui` | text mode widgets: frames, windows with shadows, buttons, progress bars |
| `console` | a scrolling console with `console_readline` |
| `gfx` | pixels, lines, rectangles, circles, triangles, gradients, sprites - all clipped |
| `cursor` | an alpha-blended mouse arrow |
| `gtext` | text in graphics mode (the VM's own VGA font, scaling, cached glyphs) |
| `audio` | tones, notes and silence into a WAV in RAM, `audio_play` |
| `disk`, `net` | sector disks and the network card |
| `part` | the partition table of a disk: up to 64 volumes (logical disks), add / delete / resize |
| `fs` | a FAT-like file system on a volume: files, directories, times, labels, mount points |
| `fsck` | consistency check of a volume (and repair of lost clusters) |
| `arr` | sort, binary search, min, max, sum and shuffle of `i32` arrays |

The full reference with every function is [`lib/README.md`](lib/README.md) (generated from the comments in the
sources by `tools/gen-libdoc.py`). Typical patterns:

```js
// a game loop
loop {
    kbd_poll();
    if (kbd_down(kb.KEY_LEFT) == 1) { x = x - 4; }
    gfx_clear(COLOR_BLACK);
    gfx_fill_rect(x, 200, 20, 20, COLOR_GREEN);
    sys_vsync();                       // wait for the next frame
}
```

```js
// text input
n = console_readline(addr(line), 40);  // echo, backspace, SHIFT ... included
value = str_atoi(addr(line));
```

Rules of thumb:

* Call `kbd_poll()` / `mouse_poll()` once per frame, then ask as often as you like.
* `gfx_clear(COLOR_TRANSPARENT)` clears the pixel layer but keeps the text layer visible.
* A program that uses `gtext` needs `ram(size: 262144)` (the glyph cache is 64 KB); `gfx` needs 8 KB, `console` 24 KB, `fs` about 5 KB and `fsck` 11 KB (a program with a console, the file system and `fsck` asks for `ram(size: 131072)`).
* Library functions that take a string take its address: `text_print(addr(msg))`.
* The mouse arrow of the host is on by default; programs that draw their own (`cursor`, `calc`) look best with
  `--cursor 0`.

## Disks, volumes and files

Four layers, each one a library:

```
disk       raw 512 byte sectors                                    lib/disk
 +- partition table in sector 1: up to 64 volumes                  lib/part
     +- a volume holds some file system (this one: "MFAT")         lib/fs
         +- mounted under a name in the tree "/"                   lib/fs
```

**The partition table.** Sector 0 of a disk is reserved for a boot loader (examples/lake keeps its kernel header
there), sector 1 holds the table: 64 slots of 8 bytes, `[i32 BeginLBA] [i32 EndLBA]` (little endian), an all
zero slot is free. A volume is the sectors from Begin to End, **End included**; volumes lie between sector 2 and
the end of the disk and may not overlap. They are named by the slot - `disk0:volume0`, `disk0:volume1` - and the
numbers never move: deleting volume 0 leaves volume 1 where it is. The table says nothing about what is on a
volume, so a volume can hold any file system; a file system recognises itself by the magic number in the first
sector of its volume (`part_probe_magic`). `lib/part` creates the table, adds a volume at a given place or in
the first gap that is big enough, deletes (optionally wiping the first sector) and resizes volumes.

**The file system.** `fs_format(dev, volume, label)` makes the FAT-like file system on one volume (only on a
volume - never on a whole disk). A **label of 1 - 48 characters is mandatory**. The layout is FAT16 with 512 byte
clusters: boot sector of the volume (bytes 0 - 15 free, header from byte 16), the allocation table, the clusters;
the root directory grows like every other directory; entries are the 32 bytes of FAT with an 8.3 name, attributes
(read only, hidden, system, archive, directory), and the times of creation / change / access in the packed FAT
format (2 s resolution); `fs_open` / `fs_read` / `fs_write` / `fs_seek` / `fs_close`, `fs_mkdir`, `fs_rmdir`,
`fs_remove`, `fs_rename` (also moves, also whole directories), `fs_list_begin` / `fs_list_next`, `fs_stat`,
`fs_touch`, `fs_set_time`, `fs_set_attr`, `fs_chdir`. Everything is written through to the disk, so an unmounted disk
is always consistent (a power cut in the middle of an operation can leave lost clusters - that is what `fsck` is for).

**Mount points.** The tree has **one root, `/`, that is virtual**: it only contains the mount points, which are
directories with a name of their own, and nothing can be created in it. A mounted volume shows up below its name:

```js
fs_mount(std.BUS_DEV_DISK0, 1, addr(name));    // name = "DATA": volume 1 is /DATA
fs_open(addr(path), FS_READ);                  // path = "/DATA/DOCS/NOTE.TXT" or, after fs_chdir, "NOTE.TXT"
```

This answers *where the mount points are stored*: the table of mounted volumes is in RAM (at most
`FS_MAX_MOUNTS` = 4), but the name that a volume prefers is stored **in the volume's own header**
(`fs_set_mountname`). A disk therefore carries its mount points with it and `fs_automount(dev)` puts every volume
of a disk into the tree without any configuration file (a volume without a stored name is mounted as `D0V1` - disk
0, volume 1; when a name is taken, the automatic one is used). Nothing needs a root file system, so unformatted
disks and any number of disks work the same way. `..` in the root of a volume leads back to `/`; a file cannot be
moved from one volume to another (copy it); `fs_abspath` gives the tidy absolute form of a path.

**Checking.** `fsck_run(dev, volume, fix)` walks the tree of a volume and compares it with the allocation table:
damaged and looping chains, cross links (a cluster used by two files), a size that does not fit its chain, wrong
`.` / `..`, duplicate and invalid names, impossible dates, lost clusters (which `fix` gives back). The problems end
up in `fsck_log`, one line each.

```js
part_init(std.BUS_DEV_DISK0);                    // 1. an empty table
v = part_add_size(std.BUS_DEV_DISK0, 2000);      // 2. volume 0: 2000 sectors in the first gap
fs_format(std.BUS_DEV_DISK0, v, addr(label));    // 3. a file system with a label
fs_set_mountname(std.BUS_DEV_DISK0, v, addr(name));   // 4. "I want to be /DATA"
fs_automount(std.BUS_DEV_DISK0);                 // 5. all volumes of the disk into the tree
n = fsck_run(std.BUS_DEV_DISK0, v, 0);           // 6. 0 = consistent
```

`examples/diskprep` is exactly this, with output; `examples/fsshell` is an interactive shell for all of it
(`PART`, `FORMAT`, `FSCK`, `MOUNT`, ... and `DIR`, `CD`, `COPY`, `TYPE`, `ECHO > file`, ...). On the host,
`tools/fstool.py` makes images, edits the table, formats, copies files in and checks them - an independent
implementation of both formats, so it is also how the Meteor code is tested:

```sh
python3 tools/fstool.py mkimage disk.img 4096        # 2 MB of zeros
./run fsshell --disk0 disk.img                       # PART INIT, PART ADD 2000, FORMAT 0 System, MOUNT 0 sys ...
python3 tools/fstool.py part-list disk.img
python3 tools/fstool.py ls disk.img -r -v 0          # the files of volume 0
python3 tools/fstool.py check disk.img -v 0
```

Limits: names are 8.3 and upper case, one cluster is one sector (a volume has at most 65 524 of them, 32 MB),
4 open files, 4 mounted volumes, no long names and no permissions. A volume is not resized together with the
partition (a bigger partition just has unused sectors at its end; a smaller one is refused when the file system
would not fit).

## The tool chain

```
 hello.meteor --frontend.py--> hello.json --bsontool.py--> hello.obj --backend.py--> hello.quartz --qasm.py--> hello.bin --quartz--> window
```

| script | does |
|---|---|
| `./build-modules` | compiles `lib/*.meteor` into `lib/*.obj` (in dependency order) |
| `./run NAME [opts]` | builds `examples/NAME.meteor` or `examples/NAME.meteor` into `dist/` and starts `./quartz` |
| `./run-live` | live reload while you edit `examples/live.meteor` (needs a `scripts/watchdog.py`, not part of this repository) |
| `./calc-scenario1`, `./calc-scenario2` | build and start the calculator (scenario 2 keeps the 1.2 MB wallpaper in an external data segment - a much faster build) |
| `./calc-build [--ext-data]` | only compiles the calculator |
| `./clean`, `./calc-clean` | remove the build products |

The backend takes `--ext-data` (static data goes to a separate data segment instead of thousands of `store`
instructions), `--comments` (annotated assembler) and writes the RAM map of every program as comments at the top
of the `.quartz` file. A program whose variables do not fit into its RAM is rejected at compile time.

`doc/BACKEND.md` describes the JSON tree that the frontend produces and the backend consumes - useful if you want
to write another frontend for the same backend.

## Examples

All of them are built on the library. `./run NAME` starts one.

### `examples/` (example programs)

| name | what it shows |
|---|---|
| `hello_world`, `text_table`, `chars` | text output, colours, padded numbers, the glyph table |
| `clock`, `live` | digital clock (`lib/rtc`) - `live` is the same program for live reload |
| `command` | a tiny shell on `lib/console`: HELP, CLS, ECHO, TIME, EXIT (case insensitive) |
| `life` | Conway's Game of Life on a torus (text mode) |
| `text`, `cursortxt` | random glyphs; a text mode mouse cursor |
| `turbovision` | a Turbo Vision style GUI: menu bar, dialogs, shadows, mouse (`lib/tui`) |
| `lake` | LakeBios, a bootloader: picks DISK0/DISK1, loads a kernel from a disk and starts it |
| `pixel`, `gfx`, `gfx_shapes` | pixels, a sliding rectangle, bars, gradients, lines, circles |
| `hello`, `game`, `keyboard_dot` | a bouncing square, Pong (W/S and arrows), a dot steered with the arrows |
| `cursor` | a custom alpha mouse cursor |
| `mandelbrot`, `mandelbrot2` | the Mandelbrot set in f32 (the second is progressive with an endless zoom) |
| `beep` | a square wave WAV built in RAM |
| `map_functions`, `sort_search` | maps, functions, `switch`, `do-while`; sorting and binary search |
| `net` | the network card |
| `test` | a self test of the VM's instruction set (deliberately library free) |
| `snake` | the classic, with speed ups and a best score | arrows, Enter |
| `paint` | drawing with 16 colours, brush sizes and an eraser | mouse, `+` / `-`, `C` |
| `analog_clock` | an analog clock with a digital readout (fixed point sin / cos) | Esc |
| `starfield` | 240 stars flying past, vanishing point follows the mouse | mouse, Up / Down |
| `lissajous` | glowing Lissajous curves with fading trails (alpha blending) | arrows |
| `balls` | bouncing balls with gravity in fixed point | click kicks them, Esc |
| `plasma` | the plasma effect in text mode | any key |
| `matrix` | falling code in text mode | any key |
| `piano` | the keyboard as a piano with four waveforms | `A S D F G H J K L`, `W E T Y U O P`, `1`-`4` |
| `tictactoe` | against a computer player that blocks and wins | mouse, `R` |
| `guess` | number guessing on the console | type numbers |
| `primes` | a sieve of Eratosthenes with statistics | - |
| `stopwatch` | start / stop / laps | Space, `L`, `R`, Esc |
| `sysinfo` | RAM, screen, attached devices, disks, clock | Esc |
| `hexview` | hex dump of RAM or of disk sectors | arrows, PgUp / PgDn, `D`, Left / Right |
| `tour` | a guided tour of the language, section by section | any key |
| `fsshell` | a disk shell: partition table, format, fsck, mount points, and the file commands | type `HELP`; start with `--disk0 disk.img` |
| `diskprep` | prepares a disk step by step (partitions, formats, mount names, files, resize, fsck); erases it | start with `--disk0 disk.img` |

### The calculator (`calc/`)

A small desktop: wallpaper, a top bar with an About box and a draggable, translucent calculator window.
It is split into a **module** (`calc_engine.meteor`: the arithmetic, no user interface) and a **program**
(`calc.meteor`: the interface). The wallpaper is converted once: `cd calc/assets && ./convert` (needs Pillow,
`pip install pillow`); the text is drawn with the VM's built-in VGA font from `lib/gtext`, so the font sheets of the
first version (`src/font_*_sheet.png`, `png2bin_fontsheet.py`) are no longer used. Run it with `./calc-scenario2`. The arithmetic is left to right
(`2 + 3 * 4 = 20`), a division by zero shows `Err` until you press `C`.

## Testing

* `tests/run-tests` builds the library, then compiles and runs every `tests/test_*.meteor` (unit tests: pass /
  fail counted by `tests/testkit.meteor`) and every `tests/screens/*.meteor` (the text screen is compared with
  its `.expected` file; an optional `.in` file scripts the keyboard / mouse). A test can have a disk:
  `<name>.disk0` (its size in sectors) or `<name>.prep` (a script that builds the image, e.g. with `fstool.py`),
  and `<name>.fscheck` (options for `fstool.py check`, so that the **independent host implementation** verifies the
  disk that the Meteor code has just written). `tests/sessions/*.txt` drive a whole program with typed text
  (`examples/fsshell`) and compare its final screen with `<name>.expected`.
* `tools/check-all` builds all examples and the calculator and starts each of them in the headless VM, looking
  for compile errors and VM faults.
* **`tools/qhead`** is a headless host for the Quartz VM: it uses the real `vm.c` / `bus.c` / `disk.c` and replaces
  only the window with memory buffers.

  ```sh
  tools/qhead prog.bin --ram 8388608 --frames 60 --text            # print the text screen
  tools/qhead prog.bin --frames 60 --ppm out.ppm                   # save the pixel layer (tools/ppm2png converts it)
  tools/qhead prog.bin --frames 30 --input keys.in --cells         # scripted input, every cell with colours
  tools/qhead prog.bin --disk0 boot.img --disk1 data.img
  ```

  The input script has one event per line: `@12 keys 262,65` (key codes held from frame 12 on, empty = none)
  and `@14 mouse 100 50 1` (pixel position and button mask).

## Things to know

* **A frame has a budget of 200 000 instructions.** If the program does not reach a `vsync` before it is used up,
  the host shows what has been drawn so far - an update that is too long for one frame flickers (this is what the
  clock's black rectangle was). Measure with `tools/qhead prog.bin --frames N --report` (it lists the cut frames and
  how often the terminal had to be rendered again); `tests/budget/*.conf` keep the examples within the budget. Heavy
  text is the usual cause: build glyphs in advance with `gtext_prepare`, and redraw only what changed - the text
  screen keeps what was drawn, so an idle program should execute almost nothing.
* `sys_ticks()` counts frames (vsyncs), not time; `sys_millis()` / `sys_time()` are the wall clock in milliseconds.
* `lib/input` hides the VM's own shortcuts (Ctrl+Alt+PgUp / PgDn / P / R / S) from the program and types the numeric
  keypad; `lib/audio` fades every tone in and out (`audio_ramp`), otherwise the end of a tone clicks.
* The file system (lib/fs) is write-through and small: see the limits at the end of *Disks, volumes and files*.
  `fsck_run(..., 1)` repairs only lost clusters; the other problems it finds need a human decision.
* The compiler checks types, names and the RAM size; **runtime** problems are not caught: a division by zero
  faults the VM, an index out of range reads or writes the neighbouring memory unless `bounds_check true;`
  is on (then the program halts).
* `rnd(N)`, `step` in `for` and the arguments of `video(...)` have to be constants.
* Because registers only live for one statement, the generated code is not fast (a pixel loop costs tens of
  instructions per pixel). The library therefore does the heavy work with block transfers, `memcpy`, and caches
  (see `gfx_fill_rect`, `gtext`); do the same in your programs.
