#!/usr/bin/env python3
"""fstool.py - inspects and prepares disk images of lib/part + lib/fs on the host. It is an independent
implementation of the on-disk formats, written from the layouts described in lib/part.meteor and
lib/fs.meteor, so it also serves as a check of the Meteor code.

  images and partitions
    fstool.py mkimage disk.img SECTORS             an empty (zero filled) image
    fstool.py part-init disk.img                   an empty partition table (sector 1)
    fstool.py part-list disk.img                   the partition table
    fstool.py part-add disk.img BEGIN END          add a volume [BEGIN, END] (END included)
    fstool.py part-add disk.img -s SIZE            add a volume of SIZE sectors in the first free place
    fstool.py part-del disk.img N                  delete volume N
    fstool.py part-resize disk.img N END           move the last sector of volume N

  file systems (-v N selects the volume, default 0)
    fstool.py info  disk.img [-v N]                disk, table and file system header
    fstool.py mkfs  disk.img LABEL [-v N] [--reserved R] [--mount NAME]
    fstool.py ls    disk.img [/path] [-v N] [-r]   directory listing (paths are inside the volume)
    fstool.py cat   disk.img /path [-v N]          file contents to stdout
    fstool.py put   disk.img host_file /path [-v N]    copy a host file into the volume
    fstool.py mkdir disk.img /path [-v N]
    fstool.py check disk.img [-v N]                consistency check of the partition table and the volume:
                                                   chains, sizes, '.' / '..', cross links, lost clusters

Exit status 1 when check finds a problem.
"""
import argparse
import struct
import sys

SECTOR = 512
MAGIC = 0x5441464D
VERSION = 2
EOC_MIN = 0xFFF8
PART_MAX = 64
FIRST_LBA = 50 # 2
H_LABEL, H_MOUNT = 38, 88


class Disk:
    """a disk image with its partition table"""

    def __init__(self, path, write=False):
        self.path = path
        self.f = open(path, "r+b" if write else "rb")
        self.f.seek(0, 2)
        self.sectors = self.f.tell() // SECTOR
        self.problems = []
        self.load_table()

    def sector(self, lba):
        self.f.seek(lba * SECTOR)
        return self.f.read(SECTOR)

    def write_sector(self, lba, data):
        assert len(data) == SECTOR
        self.f.seek(lba * SECTOR)
        self.f.write(data)

    def load_table(self):
        raw = self.sector(1) if self.sectors > 1 else bytes(SECTOR)
        self.table = []
        for i in range(PART_MAX):
            b, e = struct.unpack_from("<ii", raw, i * 8)
            self.table.append(None if (b, e) == (0, 0) else (b, e))
        used = [(i, p) for i, p in enumerate(self.table) if p]
        for i, (b, e) in used:
            if b < FIRST_LBA or e < b or e >= self.sectors:
                self.problems.append("volume%d: [%d, %d] is not valid on a disk of %d sectors" % (i, b, e, self.sectors))
        for n, (i, (b, e)) in enumerate(used):
            for j, (b2, e2) in used[n + 1:]:
                if b <= e2 and e >= b2:
                    self.problems.append("volume%d and volume%d overlap" % (i, j))

    def save_table(self):
        raw = bytearray(SECTOR)
        for i, p in enumerate(self.table):
            struct.pack_into("<ii", raw, i * 8, *(p or (0, 0)))
        self.write_sector(1, bytes(raw))
        self.f.flush()

    def free_gap(self, size):
        s = FIRST_LBA
        while True:
            if s + size > self.sectors:
                return None
            moved = False
            for p in self.table:
                if p and p[0] < s + size and p[1] >= s:
                    s = p[1] + 1
                    moved = True
            if not moved:
                return s


class Volume:
    """a volume of the disk with the file system of lib/fs"""

    def __init__(self, disk, index):
        self.disk, self.index = disk, index
        p = disk.table[index] if 0 <= index < PART_MAX else None
        if not p:
            raise SystemExit("%s: there is no volume%d" % (disk.path, index))
        self.begin, self.end = p
        self.size = self.end - self.begin + 1
        self.load_header()

    def sector(self, lba):
        return self.disk.sector(self.begin + lba)

    def write_sector(self, lba, data):
        self.disk.write_sector(self.begin + lba, data)

    def load_header(self):
        s = self.sector(0)
        (magic, bps, spc, fats, reserved, fat_sectors, total, clusters, root, version) = struct.unpack_from("<IHBBHHIHHH", s, 16)
        self.magic = magic
        self.valid = (magic == MAGIC and bps == SECTOR and spc == 1 and fats == 1 and version == VERSION and root == 2)
        self.reserved, self.fat_sectors, self.total, self.clusters, self.root = reserved, fat_sectors, total, clusters, 2
        self.label = s[H_LABEL:H_LABEL + 49].split(b"\0")[0].decode("ascii", "replace")
        self.mount_name = s[H_MOUNT:H_MOUNT + 13].split(b"\0")[0].decode("ascii", "replace")
        self.fat_start = reserved
        self.data_start = reserved + fat_sectors
        if self.valid:
            fat = b"".join(self.sector(self.fat_start + i) for i in range(fat_sectors))
            self.fat = list(struct.unpack("<%dH" % (len(fat) // 2), fat))

    # ---- the table
    def chain(self, first):
        out, c, seen = [], first, set()
        while 2 <= c <= self.clusters + 1:
            if c in seen:
                raise ValueError("loop in the chain starting at %d" % first)
            seen.add(c)
            out.append(c)
            c = self.fat[c]
            if c >= EOC_MIN:
                return out
        if first != 0:
            raise ValueError("chain from %d leaves the table at %d" % (first, c))
        return out

    def cluster(self, c):
        return self.sector(self.data_start + c - 2)

    # ---- directories
    def entries(self, dir_cluster):
        for c in self.chain(dir_cluster):
            data = self.cluster(c)
            for i in range(16):
                e = data[i * 32:(i + 1) * 32]
                if e[0] == 0:
                    return
                if e[0] == 0xE5 or e[11] & 0x08:
                    continue
                yield Entry(e, self.data_start + c - 2, i)

    def find(self, dir_cluster, name):
        for e in self.entries(dir_cluster):
            if e.raw_name == name:
                return e
        return None

    def resolve(self, path):
        """-> (Entry or None for the root, directory cluster if the result is a directory else None)"""
        cur, ent = self.root, None
        for part in [p for p in path.split("/") if p]:
            if part == ".":
                continue
            if part == "..":
                cur = self.up(cur)
                ent = None
                continue
            if cur is None:
                raise NotADirectoryError(path)
            e = self.find(cur, name83(part))
            if e is None:
                raise FileNotFoundError(path)
            ent = e
            cur = (e.cluster or self.root) if e.is_dir else None
        return ent, cur

    def up(self, d):
        if d == self.root:
            return d
        e = self.find(d, b".." + b" " * 9)
        return e.cluster if e and e.cluster else self.root


class Entry:
    def __init__(self, raw, lba, idx):
        self.raw, self.lba, self.idx = raw, lba, idx
        self.raw_name = raw[:11]
        self.attr = raw[11]
        self.ctime, self.cdate, self.adate = struct.unpack_from("<HHH", raw, 14)
        self.mtime, self.mdate, self.cluster, self.size = struct.unpack_from("<HHHI", raw, 22)

    @property
    def name(self):
        n = self.raw_name[:8].decode("ascii", "replace").rstrip()
        e = self.raw_name[8:].decode("ascii", "replace").rstrip()
        return n + ("." + e if e else "")

    @property
    def is_dir(self):
        return bool(self.attr & 0x10)

    @property
    def date(self):
        d = self.mdate
        return "%04d-%02d-%02d" % ((d >> 9) + 1980, (d >> 5) & 15, d & 31)

    @property
    def time(self):
        t = self.mtime
        return "%02d:%02d:%02d" % (t >> 11, (t >> 5) & 63, (t & 31) * 2)

    @property
    def attrs(self):
        return "".join(c if self.attr & b else "-" for c, b in (("R", 1), ("H", 2), ("S", 4), ("D", 0x10), ("A", 0x20)))


def name83(part):
    part = part.upper()
    n, _, e = part.partition(".")
    if not n or len(n) > 8 or len(e) > 3 or "." in e:
        raise ValueError("bad name " + part)
    return (n.ljust(8) + e.ljust(3)).encode("ascii")


def open_volume(a, write=False):
    disk = Disk(a.image, write)
    vol = Volume(disk, a.v)
    if not vol.valid:
        sys.exit("%s: volume%d holds no lib/fs file system (magic %08X)" % (a.image, a.v, vol.magic))
    return vol


# ------------------------------------------------------------------ images and partitions
def cmd_mkimage(a):
    with open(a.image, "wb") as f:
        f.write(bytes(SECTOR * a.sectors))


def cmd_part_init(a):
    d = Disk(a.image, True)
    d.table = [None] * PART_MAX
    d.save_table()


def cmd_part_list(a):
    d = Disk(a.image)
    print("disk: %d sectors (%d KB)" % (d.sectors, d.sectors // 2))
    print("%-8s %10s %10s %10s  %s" % ("volume", "begin", "end", "sectors", "file system"))
    for i, p in enumerate(d.table):
        if p:
            fs = "?"
            try:
                v = Volume(d, i)
                fs = "MFAT \"%s\"" % v.label if v.valid else ("empty" if v.magic == 0 else "unknown (%08X)" % v.magic)
            except SystemExit:
                pass
            print("volume%-2d %10d %10d %10d  %s" % (i, p[0], p[1], p[1] - p[0] + 1, fs))
    for p in d.problems:
        print("PROBLEM:", p)


def cmd_part_add(a):
    d = Disk(a.image, True)
    if d.problems:
        sys.exit("the table is not valid: " + d.problems[0])
    if a.size:
        begin = d.free_gap(a.size)
        if begin is None:
            sys.exit("not enough free space")
        end = begin + a.size - 1
    else:
        begin, end = a.begin, a.end
    if begin < FIRST_LBA or end < begin or end >= d.sectors:
        sys.exit("[%d, %d] is outside of the disk" % (begin, end))
    if any(p and begin <= p[1] and end >= p[0] for p in d.table):
        sys.exit("overlaps another volume")
    if None not in d.table:
        sys.exit("the table is full")
    i = d.table.index(None)
    d.table[i] = (begin, end)
    d.save_table()
    print("volume%d: [%d, %d]" % (i, begin, end))


def cmd_part_del(a):
    d = Disk(a.image, True)
    if not d.table[a.n]:
        sys.exit("there is no volume%d" % a.n)
    d.table[a.n] = None
    d.save_table()


def cmd_part_resize(a):
    d = Disk(a.image, True)
    p = d.table[a.n]
    if not p:
        sys.exit("there is no volume%d" % a.n)
    if a.end < p[0] or a.end >= d.sectors or any(q and i != a.n and p[0] <= q[1] and a.end >= q[0] for i, q in enumerate(d.table)):
        sys.exit("bad end")
    d.table[a.n] = (p[0], a.end)
    d.save_table()


# ------------------------------------------------------------------ file systems
def cmd_info(a):
    d = Disk(a.image)
    cmd_part_list(a)
    v = Volume(d, a.v)
    print()
    print("volume%d: %s" % (a.v, "lib/fs %s" % ("OK" if v.valid else "- not valid")))
    if v.valid:
        used = sum(1 for c in range(2, v.clusters + 2) if v.fat[c])
        print("  label        :", v.label)
        print("  mount name   :", v.mount_name or "(none)")
        print("  reserved     : %d sector(s), table %d sector(s), data from sector %d" % (v.reserved, v.fat_sectors, v.data_start))
        print("  clusters     : %d (%d used, %d free); the file system uses %d of the %d sectors of the volume" % (v.clusters, used, v.clusters - used, v.total, v.size))


def walk(vol, cluster, prefix, recursive, out):
    for e in vol.entries(cluster):
        if e.name in (".", ".."):
            continue
        out(prefix, e)
        if recursive and e.is_dir:
            walk(vol, e.cluster or vol.root, prefix + e.name + "/", recursive, out)


def cmd_ls(a):
    vol = open_volume(a)
    ent, cur = vol.resolve(a.path)
    if cur is None:
        sys.exit("not a directory")

    def out(prefix, e):
        print("%s %10s  %s %s  %s%s" % (e.attrs, "<DIR>" if e.is_dir else e.size, e.date, e.time, prefix, e.name))
    walk(vol, cur, "", a.r, out)


def cmd_cat(a):
    vol = open_volume(a)
    e, _ = vol.resolve(a.path)
    if e is None or e.is_dir:
        sys.exit("not a file")
    data = b"".join(vol.cluster(c) for c in vol.chain(e.cluster))[:e.size]
    sys.stdout.buffer.write(data)


def cmd_check(a):
    disk = Disk(a.image)
    problems = list(disk.problems)
    vol = Volume(disk, a.v)
    if not vol.valid:
        problems.append("volume%d: no valid file system header" % a.v)
        for p in problems:
            print("PROBLEM:", p)
        sys.exit(1)
    owner = {}

    def claim(c, who):
        if c in owner:
            problems.append("cluster %d is used by %s and by %s" % (c, owner[c], who))
        owner[c] = who

    if not vol.label or len(vol.label) > 48:
        problems.append("the volume has no valid label")
    if vol.fat[0] != 0xFFF8 or vol.fat[1] != 0xFFFF:
        problems.append("reserved table entries 0 / 1 are %04X / %04X" % (vol.fat[0], vol.fat[1]))
    if vol.total > vol.size or vol.reserved + vol.fat_sectors + vol.clusters > vol.total:
        problems.append("the geometry does not fit the volume")
    files = dirs = 0

    def scan(dir_cluster, path, parent_cluster):
        nonlocal files, dirs
        try:
            chain = vol.chain(dir_cluster)
        except ValueError as ex:
            problems.append("%s: %s" % (path, ex))
            return
        for c in chain:
            claim(c, path)
        seen_names, kids = set(), []
        for e in vol.entries(dir_cluster):
            full = path.rstrip("/") + "/" + e.name
            if e.raw_name in seen_names:
                problems.append("%s: duplicate name" % full)
            seen_names.add(e.raw_name)
            if e.name == ".":
                if (e.cluster or vol.root) != dir_cluster:
                    problems.append("%s: '.' points to %d, not to %d" % (path, e.cluster, dir_cluster))
                continue
            if e.name == "..":
                if (e.cluster or vol.root) != parent_cluster:
                    problems.append("%s: '..' points to %d, not to %d" % (path, e.cluster, parent_cluster))
                continue
            if e.is_dir:
                dirs += 1
                if e.size != 0:
                    problems.append("%s: a directory with a size" % full)
                kids.append((e.cluster or vol.root, full))
            else:
                files += 1
                try:
                    chain = vol.chain(e.cluster) if e.cluster else []
                except ValueError as ex:
                    problems.append("%s: %s" % (full, ex))
                    continue
                need = (e.size + SECTOR - 1) // SECTOR
                if len(chain) != need:
                    problems.append("%s: size %d needs %d cluster(s), the chain has %d" % (full, e.size, need, len(chain)))
                for c in chain:
                    claim(c, full)
        for c, full in kids:
            scan(c, full, dir_cluster)

    scan(vol.root, "/", vol.root)
    for c in range(2, vol.clusters + 2):
        if vol.fat[c] and c not in owner:
            problems.append("cluster %d is allocated but belongs to nobody (lost)" % c)
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        sys.exit(1)
    print("OK: %d file(s), %d director%s, %d of %d clusters used" % (files, dirs, "y" if dirs == 1 else "ies", len(owner), vol.clusters))


def pack_now():
    import datetime
    n = datetime.datetime.now()
    return (n.hour << 11) | (n.minute << 5) | (n.second // 2), ((n.year - 1980) << 9) | (n.month << 5) | n.day


def make_entry(name, attr, cluster, size):
    t, d = pack_now()
    return struct.pack("<11sBBBHHHHHHHI", name, attr, 0, 0, t, d, d, 0, t, d, cluster, size)


def cmd_mkfs(a):
    disk = Disk(a.image, True)
    vol = Volume.__new__(Volume)
    vol.disk, vol.index = disk, a.v
    p = disk.table[a.v]
    if not p:
        sys.exit("there is no volume%d" % a.v)
    if not 1 <= len(a.label) <= 48 or any(not 32 <= ord(c) < 127 for c in a.label):
        sys.exit("a volume label has 1 .. 48 printable characters")
    vol.begin, vol.end = p
    vol.size = vol.end - vol.begin + 1
    reserved = max(a.reserved, 1)
    avail = vol.size - reserved
    if avail < 6:
        sys.exit("the volume is too small")
    fat_sectors = (min(avail, 65526) * 2 + 511) // 512
    clusters = min(avail - fat_sectors, 65524)
    fat_sectors = ((clusters + 2) * 2 + 511) // 512
    total = reserved + fat_sectors + clusters
    s = bytearray(vol.sector(0))
    s[16:100] = bytes(84)
    struct.pack_into("<IHBBHHIHHH", s, 16, MAGIC, SECTOR, 1, 1, reserved, fat_sectors, total, clusters, 2, VERSION)
    s[H_LABEL:H_LABEL + len(a.label)] = a.label.encode("ascii")
    if a.mount:
        s[H_MOUNT:H_MOUNT + len(a.mount)] = a.mount.upper().encode("ascii")
    vol.write_sector(0, bytes(s))
    for i in range(fat_sectors):
        vol.write_sector(reserved + i, bytes(SECTOR))
    fat0 = bytearray(SECTOR)
    struct.pack_into("<HHH", fat0, 0, 0xFFF8, 0xFFFF, 0xFFFF)
    vol.write_sector(reserved, bytes(fat0))
    vol.write_sector(reserved + fat_sectors, bytes(SECTOR))
    disk.f.flush()


def write_fat(vol):
    data = struct.pack("<%dH" % len(vol.fat), *vol.fat)
    for i in range(vol.fat_sectors):
        vol.write_sector(vol.fat_start + i, data[i * SECTOR:(i + 1) * SECTOR].ljust(SECTOR, b"\0"))


def alloc(vol):
    for c in range(2, vol.clusters + 2):
        if vol.fat[c] == 0:
            vol.fat[c] = 0xFFFF
            return c
    sys.exit("volume full")


def add_entry(vol, dir_cluster, raw):
    for c in vol.chain(dir_cluster):
        data = bytearray(vol.cluster(c))
        for i in range(16):
            if data[i * 32] in (0, 0xE5):
                data[i * 32:(i + 1) * 32] = raw
                vol.write_sector(vol.data_start + c - 2, bytes(data))
                return
    n = alloc(vol)
    vol.fat[vol.chain(dir_cluster)[-1]] = n
    data = bytearray(SECTOR)
    data[:32] = raw
    vol.write_sector(vol.data_start + n - 2, bytes(data))


def split_path(vol, path):
    parts = [p for p in path.split("/") if p]
    _, parent = vol.resolve("/" + "/".join(parts[:-1]))
    return parent, name83(parts[-1])


def cmd_mkdir(a):
    vol = open_volume(a, True)
    parent, nm = split_path(vol, a.path)
    c = alloc(vol)
    block = bytearray(SECTOR)
    block[:32] = make_entry(b".".ljust(11), 0x10, c, 0)
    block[32:64] = make_entry(b"..".ljust(11), 0x10, 0 if parent == vol.root else parent, 0)
    vol.write_sector(vol.data_start + c - 2, bytes(block))
    add_entry(vol, parent, make_entry(nm, 0x10, c, 0))
    write_fat(vol)


def cmd_put(a):
    vol = open_volume(a, True)
    data = open(a.host_file, "rb").read()
    parent, nm = split_path(vol, a.path)
    prev, first = 0, 0
    for i in range(0, len(data), SECTOR):
        c = alloc(vol)
        if prev:
            vol.fat[prev] = c
        else:
            first = c
        vol.write_sector(vol.data_start + c - 2, data[i:i + SECTOR].ljust(SECTOR, b"\0"))
        prev = c
    add_entry(vol, parent, make_entry(nm, 0x20, first, len(data)))
    write_fat(vol)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *args, volume=False, **kw):
        s = sub.add_parser(name)
        s.add_argument("image")
        for ar in args:
            s.add_argument(ar[0], **ar[1])
        if volume:
            s.add_argument("-v", type=int, default=0, help="volume number (default 0)")
        s.set_defaults(fn=fn)
        return s

    add("mkimage", cmd_mkimage, ("sectors", {"type": int}))
    add("part-init", cmd_part_init)
    add("part-list", cmd_part_list)
    s = add("part-add", cmd_part_add, ("begin", {"type": int, "nargs": "?"}), ("end", {"type": int, "nargs": "?"}))
    s.add_argument("-s", dest="size", type=int, default=0)
    add("part-del", cmd_part_del, ("n", {"type": int}))
    add("part-resize", cmd_part_resize, ("n", {"type": int}), ("end", {"type": int}))
    add("info", cmd_info, volume=True)
    s = add("mkfs", cmd_mkfs, ("label", {}), volume=True)
    s.add_argument("--reserved", type=int, default=1)
    s.add_argument("--mount", default="")
    s = add("ls", cmd_ls, ("path", {"nargs": "?", "default": "/"}), volume=True)
    s.add_argument("-r", action="store_true")
    add("cat", cmd_cat, ("path", {}), volume=True)
    add("check", cmd_check, volume=True)
    add("mkdir", cmd_mkdir, ("path", {}), volume=True)
    add("put", cmd_put, ("host_file", {}), ("path", {}), volume=True)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
